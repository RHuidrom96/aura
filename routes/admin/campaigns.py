import os
import json
import logging
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session,
    jsonify, current_app,
)

import models
import results
import llm
import exporter
import mailer
import seed_data
from models import db, Campaign, Annotator, Rating, AssistantLog, CampaignGroup
from models import (
    normalize_fluency, FLUENCY_LEVELS, normalize_age_group, AGE_GROUPS, fluency_label,
)
from utils.constants import (
    get_criteria_for, EVAL_MODES, SCALE_TYPES, SCALE_DESIGNS, SCRIPT_OPTIONS,
    CRITERIA_DEFAULTS, LANGUAGE_SCRIPTS, script_allowed_for_language, script_label,
)
from utils.forms import (
    parse_scale_from_form, parse_criteria_from_form, parse_preferences_from_form,
)
from utils.ingest import parse_segments_upload, ACCEPT_ATTR as SEGMENTS_ACCEPT
from services.auth_service import require_admin, ADMIN_EMAIL
from services.ai_assistant import task_label

logger = logging.getLogger(__name__)

admin_campaign_bp = Blueprint(
    "admin_campaign",
    __name__,
    url_prefix="/admin",
)

@admin_campaign_bp.route("/dashboard")
@require_admin
def admin_dashboard():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    groups = CampaignGroup.query.order_by(CampaignGroup.created_at.desc()).all()
    # Annotator counts per campaign
    stats = {}
    for c in campaigns:
        crit_ids = [cr["id"] for cr in get_criteria_for(c)]
        annotator_ids = {r.annotator_id for r in c.ratings}
        stats[c.id] = {
            "annotator_count": len(annotator_ids),
            "total_ratings": sum(1 for r in c.ratings if c.rating_is_complete(r, get_criteria_for(c))),
            "max_possible": c.num_segments * max(1, len(annotator_ids)),
        }
    # Per-group campaign counts for the dashboard summary.
    group_counts = {g.id: 0 for g in groups}
    for c in campaigns:
        if c.group_id in group_counts:
            group_counts[c.group_id] += 1
    return render_template("admin_dashboard.html",
                        campaigns=campaigns, stats=stats,
                        groups=groups, group_counts=group_counts,
                        has_demo=seed_data.demo_present(Campaign),)


@admin_campaign_bp.route("/dashboard.json")
@require_admin
def admin_dashboard_json():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    out = []
    for c in campaigns:
        crit = get_criteria_for(c)
        annotator_ids = {r.annotator_id for r in c.ratings}
        out.append({
            "id": c.id,
            "annotator_count": len(annotator_ids),
            "total_ratings": sum(1 for r in c.ratings if c.rating_is_complete(r, crit)),
            "is_closed": bool(c.is_closed),
        })
    return jsonify({"ok": True, "campaigns": out})


@admin_campaign_bp.route("/seed-demo", methods=["POST"])
@require_admin
def admin_seed_demo():
    try:
        summary = seed_data.seed_all(db, Campaign, Annotator, Rating)
    except Exception:
        logger.exception("Demo seeding failed")
        flash("Could not load the demo campaigns. See the server log for details.", "error")
        return redirect(url_for("admin_campaign.admin_dashboard"))
    n_c = len(summary["campaigns"])
    if n_c or summary["annotators"] or summary["ratings"]:
        flash(f"Loaded {n_c} demo campaign(s), {summary['annotators']} demo annotator(s), "
            f"and {summary['ratings']} synthetic rating(s) so the dashboards are populated. "
            "The sample translations are illustrative; replace them with your own outputs "
            "before a real evaluation.", "success")
    else:
        flash("The demo data is already loaded.", "info")
    return redirect(url_for("admin_campaign.admin_dashboard"))


@admin_campaign_bp.route("/unload-demo", methods=["POST"])
@require_admin
def admin_unload_demo():
    try:
        summary = seed_data.unload_demo(db, Campaign, Annotator, Rating, AssistantLog)
    except Exception:
        logger.exception("Demo unload failed")
        flash("Could not remove the demo data. See the server log for details.", "error")
        return redirect(url_for("admin_campaign.admin_dashboard"))
    if summary["campaigns"] or summary["annotators"] or summary["ratings"]:
        flash(f"Removed {summary['campaigns']} demo campaign(s), {summary['annotators']} demo "
            f"annotator(s), and {summary['ratings']} demo rating(s).", "success")
    else:
        flash("There was no demo data to remove.", "info")
    return redirect(url_for("admin_campaign.admin_dashboard"))


def _validate_segments_for_mode(segments, eval_mode, campaign_for_norm=None):
    """Return a list of error strings for the given segments under a mode."""
    errors = []
    if not isinstance(segments, list) or len(segments) == 0:
        return ["Your segments must be a non-empty list (JSON array, or CSV/TSV rows)."]
    seen_ids = set()
    # Use a throwaway Campaign just for the candidate-normalization helper.
    norm = campaign_for_norm or Campaign(segments_json="[]")
    for i, s in enumerate(segments):
        if not isinstance(s, dict):
            errors.append(f"Segment {i+1}: must be an object.")
            continue
        if "id" not in s or "source" not in s:
            errors.append(f"Segment {i+1}: must have at least 'id' and 'source'.")
            continue
        if eval_mode == "pairwise":
            a, b, _, _ = norm.pairwise_candidates(s)
            if not a or not b:
                errors.append(f"Segment {i+1}: pairwise mode needs two candidates "
                            "(provide 'target_a' and 'target_b', or a 'candidates' list of two).")
        else:
            if "target" not in s:
                errors.append(f"Segment {i+1}: must have a 'target'.")
        if s.get("id") in seen_ids:
            errors.append(f"Duplicate segment id: {s.get('id')}")
        seen_ids.add(s.get("id"))
    return errors


def _collect_campaign_form(form, files, *, parse_segments, existing_segments=None,
                        existing_campaign=None):
    """Read + validate all campaign-config fields from a submitted form.

    Returns (config_kwargs, segments_or_None, errors, form_view).
    `config_kwargs` excludes segments_json (caller decides whether to set it).
    """
    errors = []
    name = form.get("name", "").strip()
    task_type = (form.get("task_type") or "translation").strip()
    if task_type not in models.TASK_TYPES:
        task_type = "translation"
    input_label = form.get("input_label", "").strip()[:80]
    output_label = form.get("output_label", "").strip()[:80]
    difficulty_method = (form.get("difficulty_method") or "auto").strip()
    if difficulty_method not in ("auto", "length", "manual", "none"):
        difficulty_method = "auto"
    def _posint(name):
        try:
            return max(0, int(form.get(name) or 0))
        except (TypeError, ValueError):
            return 0
    difficulty_easy_max = _posint("difficulty_easy_max")
    difficulty_hard_min = _posint("difficulty_hard_min")
    # Which difficulty levels to serve. Store "" when all (or none) are checked, so the
    # default is "serve everything"; otherwise store the chosen subset.
    _served = [d for d in form.getlist("served_diff") if d in ("easy", "medium", "hard")]
    if difficulty_method == "none" or set(_served) == {"easy", "medium", "hard"}:
        served_difficulties = ""
    else:
        served_difficulties = ",".join(d for d in ("easy", "medium", "hard") if d in _served)
    expertise_matching = bool(form.get("expertise_matching")) and difficulty_method != "none"
    shuffle_segments = bool(form.get("shuffle_segments"))
    rating_position = (form.get("rating_position") or "side").strip()
    if rating_position not in ("side", "below"):
        rating_position = "side"
    span_position = (form.get("span_position") or "below").strip()
    if span_position not in ("below", "beside"):
        span_position = "below"
    source_language = form.get("source_language", "").strip()
    target_language = form.get("target_language", "").strip()
    source_script = form.get("source_script", "").strip() or None
    target_script = form.get("target_script", "").strip() or None
    # Legacy single field: accept it if present, and keep it populated from the per-side
    # scripts so older readers still show something.
    legacy_script = form.get("script", "").strip() or None
    script = target_script or source_script or legacy_script
    instructions = form.get("instructions", "").strip()
    span_instructions = form.get("span_instructions", "").strip()
    if not instructions:
        errors.append("Annotation instructions are required.")

    eval_mode = (form.get("eval_mode") or "likert").strip()
    if eval_mode not in {m["id"] for m in EVAL_MODES}:
        errors.append("Invalid evaluation mode.")
        eval_mode = "likert"

    # Spans depend on the mode.
    if eval_mode == "span_only":
        enable_spans = True
        span_scope = form.get("span_scope", "").strip()
        if span_scope not in ("target", "both"):
            span_scope = ""   # validated below
    elif eval_mode in ("pairwise", "post_edit"):
        enable_spans = False
        span_scope = "target"
    else:  # likert
        enable_spans = form.get("enable_spans") == "on"
        span_scope = form.get("span_scope", "").strip()
        if enable_spans:
            if span_scope not in ("target", "both"):
                span_scope = ""
        else:
            span_scope = "target"

    try:
        segments_per_page = int(form.get("segments_per_page") or 3)
    except (TypeError, ValueError):
        segments_per_page = 3
    try:
        min_segments_per_page = int(form.get("min_segments_per_page") or 1)
    except (TypeError, ValueError):
        min_segments_per_page = 1
    try:
        max_segments_per_page = int(form.get("max_segments_per_page") or 10)
    except (TypeError, ValueError):
        max_segments_per_page = 10

    # Scale config (only meaningful in likert mode; keep sane defaults otherwise).
    scale_errors = []
    if eval_mode == "likert":
        scale_kwargs = parse_scale_from_form(form, scale_errors)
    else:
        scale_kwargs = {"scale_type": "likert", "scale_points": 5, "scale_min": 0,
                        "scale_max": 100, "scale_design": "circles", "scale_labels_json": ""}

    # Criteria (used in likert + span_only) and preferences (pairwise).
    criteria, criteria_missing_desc = parse_criteria_from_form(form)
    preferences = parse_preferences_from_form(form)

    # ---- validation ----
    if not name:
        errors.append("Campaign name is required.")
    bilingual = models.task_defaults(task_type).get("bilingual")
    if bilingual:
        if not source_language:
            errors.append("Source language is required.")
        if not target_language:
            errors.append("Target language is required.")
    script_ids = {s["id"] for s in SCRIPT_OPTIONS}
    if source_script and source_script not in script_ids:
        errors.append("Invalid source-language script option.")
    if target_script and target_script not in script_ids:
        errors.append("Invalid target-language script option.")
    if legacy_script and legacy_script not in script_ids:
        errors.append("Invalid script option.")
    # A script must be plausible for the chosen language (unknown languages are unrestricted).
    if source_script and source_language and not script_allowed_for_language(source_script, source_language):
        errors.append(f"“{script_label(source_script)}” isn't a valid script for {source_language}. "
                      "Pick a script listed for that language, or choose “Other”.")
    if target_script and target_language and not script_allowed_for_language(target_script, target_language):
        errors.append(f"“{script_label(target_script)}” isn't a valid script for {target_language}. "
                      "Pick a script listed for that language, or choose “Other”.")
    # Source and target may share a language ONLY if the scripts differ (a script-conversion /
    # transliteration task). This applies to inherently bilingual tasks (machine translation),
    # where identical language *and* identical (or both-empty) script is a no-op. Monolingual
    # and optionally-cross-lingual tasks (e.g. English→English summarization) legitimately use
    # the same language on both sides, so they're exempt.
    cross_mode = models.task_defaults(task_type).get("cross_lingual", "none")
    if (cross_mode == "required" and source_language and target_language
            and source_language.casefold() == target_language.casefold()):
        if (source_script or "") == (target_script or ""):
            errors.append("Source and target are identical (same language and script). "
                          "Use two different languages, or the same language with two "
                          "different scripts (for a transliteration / script-conversion task).")
    if segments_per_page < 1 or segments_per_page > 50:
        errors.append("Segments per page must be between 1 and 50.")
    if min_segments_per_page < 1 or max_segments_per_page > 50:
        errors.append("Segments-per-page limits must be between 1 and 50.")
    if min_segments_per_page > max_segments_per_page:
        errors.append("Minimum segments per page can't be greater than the maximum.")
    if not (min_segments_per_page <= segments_per_page <= max_segments_per_page):
        errors.append("The default segments-per-page must be within the min/max limits you set.")

    if eval_mode in ("likert", "span_only"):
        if len(criteria) == 0:
            label = "evaluation criterion" if eval_mode == "likert" else "error-type category"
            errors.append(f"Please define at least one {label}.")
        if criteria_missing_desc:
            names = ", ".join(criteria_missing_desc)
            errors.append("Each criterion needs a definition shown to annotators. "
                        f"Missing definition for: {names}.")
    if eval_mode == "likert":
        errors.extend(scale_errors)
        if enable_spans and span_scope not in ("target", "both"):
            errors.append("Please select whether span annotation applies to the target only or to both source and target.")
    if eval_mode == "span_only":
        if span_scope not in ("target", "both"):
            errors.append("Please select whether spans are marked in the target only or in both source and target.")
    if eval_mode == "pairwise":
        if len(preferences) < 2:
            errors.append("Please define at least two preference options for pairwise comparison.")

    # ---- segments ----
    segments = None
    if parse_segments:
        segments_raw = ""
        upload_name = ""
        upload = files.get("segments_file") if files else None
        if upload and upload.filename:
            upload_name = upload.filename
            try:
                segments_raw = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                errors.append("Could not read the uploaded file as UTF-8.")
                segments_raw = ""
        else:
            segments_raw = form.get("segments_paste", "").strip()

        if not segments_raw:
            errors.append("Please upload or paste your segments (JSON, JSONL, CSV, or TSV).")
        else:
            segments, parse_err = parse_segments_upload(segments_raw, upload_name)
            if parse_err:
                errors.append(f"Could not parse the segments file: {parse_err}")
                segments = None
            else:
                errors.extend(_validate_segments_for_mode(segments, eval_mode))
        form_segments_paste = segments_raw
    else:
        # Editing: validate the existing segments against the (possibly new) mode.
        if existing_segments is not None:
            errors.extend(_validate_segments_for_mode(existing_segments, eval_mode))
        form_segments_paste = ""

    # ---- AI assistant config ----
    ai_enabled = form.get("ai_enabled") == "on"
    ai_provider = (form.get("ai_provider") or "").strip()
    ai_model = (form.get("ai_model") or "").strip()
    ai_base_url = (form.get("ai_base_url") or "").strip()
    ai_key_input = form.get("ai_api_key") or ""
    existing_key_enc = (existing_campaign.ai_api_key_enc if existing_campaign else "") or ""
    # Encrypt a freshly-entered key; otherwise keep whatever was stored.
    if ai_key_input.strip():
        ai_api_key_enc = llm.encrypt_secret(ai_key_input.strip(), current_app.config["SECRET_KEY"])
    else:
        ai_api_key_enc = existing_key_enc
    has_key = bool(ai_api_key_enc)

    # Randomised AI experiment
    ai_ab_enabled = bool(form.get("ai_ab_enabled")) and ai_enabled
    try:
        ai_ab_fraction = max(0, min(100, int(form.get("ai_ab_fraction") or 50)))
    except (TypeError, ValueError):
        ai_ab_fraction = 50

    if ai_enabled:
        meta = llm.provider_by_id(ai_provider)
        if not meta:
            errors.append("Please choose a model provider for the AI assistant.")
        else:
            if meta["needs_key"] and not has_key:
                errors.append(f"{meta['name']} needs an API key for the AI assistant.")
            if meta["needs_base_url"] and not (ai_base_url or meta["default_base_url"]):
                errors.append(f"{meta['name']} needs an endpoint URL for the AI assistant.")
            if not (ai_model or meta["default_model"]):
                errors.append("Please specify a model name for the AI assistant.")

    # Incentives parsing
    incentive_co_authorship = form.get("incentive_co_authorship") == "on"
    incentive_money = form.get("incentive_money") == "on"
    try:
        incentive_min_wage = float(form.get("incentive_min_wage") or 0.0)
    except (TypeError, ValueError):
        incentive_min_wage = 0.0
    try:
        incentive_target_tasks = int(form.get("incentive_target_tasks") or 0)
    except (TypeError, ValueError):
        incentive_target_tasks = 0
    try:
        incentive_level = float(form.get("incentive_level") or 1.0)
    except (TypeError, ValueError):
        incentive_level = 1.0
    try:
        incentive_intensity = float(form.get("incentive_intensity") or 1.0)
    except (TypeError, ValueError):
        incentive_intensity = 1.0
    try:
        incentive_bonus_amount = float(form.get("incentive_bonus_amount") or 0.0)
    except (TypeError, ValueError):
        incentive_bonus_amount = 0.0

    config = {
        "name": name,
        "task_type": task_type,
        "input_label": input_label,
        "output_label": output_label,
        "difficulty_method": difficulty_method,
        "difficulty_easy_max": difficulty_easy_max,
        "difficulty_hard_min": difficulty_hard_min,
        "served_difficulties": served_difficulties,
        "expertise_matching": expertise_matching,
        "shuffle_segments": shuffle_segments,
        "rating_position": rating_position,
        "span_position": span_position,
        "source_language": source_language,
        "target_language": target_language,
        "script": script,
        "source_script": source_script,
        "target_script": target_script,
        "criteria_json": json.dumps(criteria, ensure_ascii=False),
        "instructions": instructions,
        "span_instructions": span_instructions,
        "enable_spans": enable_spans,
        "span_scope": span_scope,
        "segments_per_page": segments_per_page,
        "min_segments_per_page": min_segments_per_page,
        "max_segments_per_page": max_segments_per_page,
        "eval_mode": eval_mode,
        "preferences_json": json.dumps(preferences, ensure_ascii=False),
        "ai_enabled": ai_enabled,
        "ai_ab_enabled": ai_ab_enabled,
        "ai_ab_fraction": ai_ab_fraction,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "ai_base_url": ai_base_url,
        "ai_api_key_enc": ai_api_key_enc,
        "incentive_co_authorship": incentive_co_authorship,
        "incentive_money": incentive_money,
        "incentive_min_wage": incentive_min_wage,
        "incentive_target_tasks": incentive_target_tasks,
        "incentive_level": incentive_level,
        "incentive_intensity": incentive_intensity,
        "incentive_bonus_amount": incentive_bonus_amount,
        **scale_kwargs,
    }
    form_view = {
        "name": name, "source_language": source_language,
        "task_type": task_type, "input_label": input_label, "output_label": output_label,
        "difficulty_method": difficulty_method,
        "difficulty_easy_max": difficulty_easy_max or "",
        "difficulty_hard_min": difficulty_hard_min or "",
        "served_difficulties": served_difficulties,
        "expertise_matching": expertise_matching,
        "shuffle_segments": shuffle_segments,
        "rating_position": rating_position, "span_position": span_position,
        "target_language": target_language, "script": script or "",
        "source_script": source_script or "", "target_script": target_script or "",
        "instructions": instructions,
        "span_instructions": span_instructions, "enable_spans": enable_spans,
        "span_scope": span_scope, "segments_per_page": segments_per_page,
        "min_segments_per_page": min_segments_per_page,
        "max_segments_per_page": max_segments_per_page,
        "eval_mode": eval_mode,
        "criteria": [{"name": cr["name"], "desc": cr["guide"]} for cr in criteria],
        "preferences": preferences,
        "scale": _scale_form_view(scale_kwargs),
        "segments_paste": form_segments_paste,
        "ai_enabled": ai_enabled, "ai_ab_enabled": ai_ab_enabled, "ai_ab_fraction": ai_ab_fraction,
        "ai_provider": ai_provider, "ai_model": ai_model,
        "ai_base_url": ai_base_url, "ai_has_key": has_key,
        "incentive_co_authorship": incentive_co_authorship,
        "incentive_money": incentive_money,
        "incentive_min_wage": incentive_min_wage,
        "incentive_target_tasks": incentive_target_tasks,
        "incentive_level": incentive_level,
        "incentive_intensity": incentive_intensity,
        "incentive_bonus_amount": incentive_bonus_amount,
    }
    return config, segments, errors, form_view


@admin_campaign_bp.route("/campaign/new", methods=["GET", "POST"])
@require_admin
def admin_campaign_new():
    if request.method == "POST":
        config, segments, errors, form_view = _collect_campaign_form(
            request.form, request.files, parse_segments=True)
        if errors:
            for e in errors:
                flash(e, "error")
            return _render_new_campaign_form(form_data=form_view)
        c = Campaign(segments_json=json.dumps(segments, ensure_ascii=False), **config)
        c.owner_email = (session.get("admin_email") or ADMIN_EMAIL or "").strip().lower()
        db.session.add(c)
        db.session.commit()
        flash(f"Campaign '{config['name']}' created.", "success")
        return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))

    return _render_new_campaign_form()


def _scale_form_view(scale_kwargs):
    """Turn stored scale kwargs into a flat dict the form template can read back."""
    labels = {}
    try:
        labels = json.loads(scale_kwargs.get("scale_labels_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        labels = {}
    return {
        "type": scale_kwargs.get("scale_type", "likert"),
        "design": scale_kwargs.get("scale_design", "circles"),
        "points": scale_kwargs.get("scale_points", 5),
        "min": scale_kwargs.get("scale_min", 0),
        "max": scale_kwargs.get("scale_max", 100),
        "labels": {str(k): v for k, v in labels.items()},
    }


def _default_scale_view():
    return {"type": "likert", "design": "circles", "points": 5,
            "min": 0, "max": 100, "labels": {}}


def _render_new_campaign_form(form_data=None):
    fd = form_data or {}
    # Provide default criteria for a fresh form
    if "criteria" not in fd:
        fd["criteria"] = [{"name": c["name"], "desc": c["guide"]} for c in CRITERIA_DEFAULTS]
    if "scale" not in fd:
        fd["scale"] = _default_scale_view()
    if "segments_per_page" not in fd:
        fd["segments_per_page"] = 3
    if "min_segments_per_page" not in fd:
        fd["min_segments_per_page"] = 1
    if "max_segments_per_page" not in fd:
        fd["max_segments_per_page"] = 10
    if "eval_mode" not in fd:
        fd["eval_mode"] = "likert"
    if "preferences" not in fd:
        fd["preferences"] = list(Campaign.DEFAULT_PREFERENCES)
    if "ai_enabled" not in fd:
        fd["ai_enabled"] = False
    if "ai_provider" not in fd:
        fd["ai_provider"] = "anthropic"
    fd.setdefault("ai_model", "")
    fd.setdefault("ai_base_url", "")
    fd.setdefault("ai_has_key", False)
    if "incentive_co_authorship" not in fd:
        fd["incentive_co_authorship"] = False
    if "incentive_money" not in fd:
        fd["incentive_money"] = False
    fd.setdefault("incentive_min_wage", 0.0)
    fd.setdefault("incentive_target_tasks", 10)
    fd.setdefault("incentive_level", 1.0)
    fd.setdefault("incentive_intensity", 1.0)
    fd.setdefault("incentive_bonus_amount", 0.0)
    return render_template("admin_campaign_new.html",
                        scripts=SCRIPT_OPTIONS,
                        language_scripts=LANGUAGE_SCRIPTS,
                        scale_types=SCALE_TYPES,
                        scale_designs=SCALE_DESIGNS,
                        eval_modes=EVAL_MODES,
                        task_types=models.TASK_TYPES,
                        criterion_guides=models.CRITERION_GUIDES,
                        ai_providers=llm.PROVIDERS,
                        form_data=fd)


@admin_campaign_bp.route("/campaign/<campaign_id>/edit", methods=["GET", "POST"])
@require_admin
def admin_campaign_edit(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        flash("This campaign is closed and can no longer be edited.", "error")
        return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))

    if request.method == "POST":
        config, _segments, errors, form_view = _collect_campaign_form(
            request.form, request.files, parse_segments=False,
            existing_segments=c.segments, existing_campaign=c)
        if errors:
            for e in errors:
                flash(e, "error")
            return _render_edit_campaign_form(c, form_data=form_view)
        # Apply every config field except segments (segments are fixed after creation).
        for k, v in config.items():
            setattr(c, k, v)
        db.session.commit()
        flash("Campaign configuration updated.", "success")
        return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))

    # GET -- prefill from the campaign
    crit = get_criteria_for(c)
    view = {
        "name": c.name,
        "task_type": c.task_type or "translation",
        "input_label": c.input_label or "",
        "output_label": c.output_label or "",
        "difficulty_method": c.difficulty_method or "auto",
        "difficulty_easy_max": c.difficulty_easy_max or "",
        "difficulty_hard_min": c.difficulty_hard_min or "",
        "served_difficulties": c.served_difficulties or "",
        "expertise_matching": c.expertise_matching,
        "shuffle_segments": bool(c.shuffle_segments),
        "rating_position": c.rating_position or "side",
        "span_position": c.span_position or "below",
        "source_language": c.source_language,
        "target_language": c.target_language,
        "script": c.script or "",
        "source_script": (c.source_script or c.script or ""),
        "target_script": (c.target_script or c.script or ""),
        "instructions": c.instructions or "",
        "span_instructions": c.span_instructions or "",
        "enable_spans": c.enable_spans if c.enable_spans is not None else True,
        "span_scope": c.span_scope or "target",
        "segments_per_page": c.segments_per_page or 3,
        "min_segments_per_page": (c.min_segments_per_page or 1),
        "max_segments_per_page": (c.max_segments_per_page or 10),
        "eval_mode": c.mode,
        "criteria": [{"name": x["name"], "desc": x.get("guide") or x.get("desc") or ""} for x in crit],
        "preferences": c.preferences,
        "ai_enabled": bool(c.ai_enabled),
        "ai_ab_enabled": bool(c.ai_ab_enabled),
        "ai_ab_fraction": c.ai_ab_fraction or 50,
        "ai_provider": c.ai_provider or "anthropic",
        "ai_model": c.ai_model or "",
        "ai_base_url": c.ai_base_url or "",
        "ai_has_key": bool(c.ai_api_key_enc),
        "incentive_co_authorship": bool(c.incentive_co_authorship),
        "incentive_money": bool(c.incentive_money),
        "incentive_min_wage": c.incentive_min_wage or 0.0,
        "incentive_target_tasks": c.incentive_target_tasks or 0,
        "incentive_level": c.incentive_level or 1.0,
        "incentive_intensity": c.incentive_intensity or 1.0,
        "incentive_bonus_amount": c.incentive_bonus_amount or 0.0,
        "scale": {
            "type": c.scale_type or "likert",
            "design": c.scale_design or "circles",
            "points": c.scale_points or 5,
            "min": c.scale_min if c.scale_min is not None else 0,
            "max": c.scale_max if c.scale_max is not None else 100,
            "labels": c.scale_labels,
        },
    }
    return _render_edit_campaign_form(c, form_data=view)


def _render_edit_campaign_form(campaign, form_data):
    return render_template("admin_campaign_edit.html",
                        campaign=campaign,
                        scripts=SCRIPT_OPTIONS,
                        language_scripts=LANGUAGE_SCRIPTS,
                        scale_types=SCALE_TYPES,
                        scale_designs=SCALE_DESIGNS,
                        eval_modes=EVAL_MODES,
                        task_types=models.TASK_TYPES,
                        criterion_guides=models.CRITERION_GUIDES,
                        ai_providers=llm.PROVIDERS,
                        form_data=form_data)


@admin_campaign_bp.route("/campaign/<campaign_id>")
@require_admin
def admin_campaign_detail(campaign_id):
    from models import CampaignAnnotator
    c = Campaign.query.get_or_404(campaign_id)
    crit_ids = [cr["id"] for cr in get_criteria_for(c)]
    # Gather per-annotator progress
    rows = []
    annotator_ids = sorted({r.annotator_id for r in c.ratings})
    for aid in annotator_ids:
        ann = Annotator.query.get(aid)
        if not ann:
            continue
        link = CampaignAnnotator.query.filter_by(campaign_id=c.id, annotator_id=aid).first()
        if not link:
            link = CampaignAnnotator(campaign_id=c.id, annotator_id=aid)
            db.session.add(link)
            db.session.commit()
        rs = [r for r in c.ratings if r.annotator_id == aid]
        completed = sum(1 for r in rs if c.rating_is_complete(r, get_criteria_for(c)))
        last_update = max((r.updated_at for r in rs), default=None)
        rows.append({
            "annotator": ann,
            "completed": completed,
            "total": c.num_segments,
            "last_update": last_update,
            "link": link,
        })
    rows.sort(key=lambda r: (-r["completed"], r["annotator"].email))

    share_url = url_for("annotator_campaign.annotator_login_view", campaign_id=c.id, _external=True)
    in_label, out_label = c.io_labels()
    mode_label = next((m["name"] for m in EVAL_MODES if m["id"] == c.mode), c.mode)
    diff_label = {"auto": "Automatic (composite heuristic)", "length": "By input length",
                "manual": "Manual labels only", "none": "None"}.get(
                    c.difficulty_method or "auto", "Automatic")
    groups = CampaignGroup.query.order_by(CampaignGroup.name.asc()).all()
    return render_template("admin_campaign_detail.html",
                        campaign=c, rows=rows, share_url=share_url,
                        criteria=get_criteria_for(c),
                        mode_label=mode_label, task_label=task_label(c),
                        input_label=in_label, output_label=out_label,
                        difficulty_label=diff_label,
                        groups=groups,)


@admin_campaign_bp.route("/campaign/<campaign_id>/progress.json")
@require_admin
def admin_campaign_progress(campaign_id):
    from models import CampaignAnnotator
    c = Campaign.query.get_or_404(campaign_id)
    crit = get_criteria_for(c)
    rows = []
    annotator_ids = sorted({r.annotator_id for r in c.ratings})
    for aid in annotator_ids:
        ann = db.session.get(Annotator, aid)
        if not ann:
            continue
        link = CampaignAnnotator.query.filter_by(campaign_id=c.id, annotator_id=aid).first()
        if not link:
            link = CampaignAnnotator(campaign_id=c.id, annotator_id=aid)
            db.session.add(link)
            db.session.commit()
        rs = [r for r in c.ratings if r.annotator_id == aid]
        completed = sum(1 for r in rs if c.rating_is_complete(r, crit))
        last = max((r.updated_at for r in rs), default=None)
        rows.append({
            "id": ann.id,
            "name": ann.name, "email": ann.email,
            "expertise": ann.expertise or "",
            "source_fluency": ann.source_fluency_label,
            "target_fluency": ann.target_fluency_label,
            "profile_location": ann.location or "",
            "dialect": ann.dialect or "",
            "age_group": ann.age_group_label,
            "completed": completed, "total": c.num_segments,
            "last_update": last.strftime("%Y-%m-%d %H:%M UTC") if last else None,
            "annotator_id": ann.id,
            "has_star": bool(link.has_star),
            "quality_score": float(link.quality_score),
            "form_submitted": bool(link.form_submitted),
            "location": link.location or "",
            "parents_language": link.parents_language or "",
            "stayed_outside": bool(link.stayed_outside),
            "stayed_outside_duration": link.stayed_outside_duration or "",
            "stayed_outside_purpose": link.stayed_outside_purpose or "",
            "exposure": link.exposure or "",
        })
    rows.sort(key=lambda r: (-r["completed"], r["email"]))
    return jsonify({"ok": True, "rows": rows})


@admin_campaign_bp.route("/campaign/<campaign_id>/annotator/<annotator_id>/evaluate", methods=["POST"])
@require_admin
def admin_evaluate_annotator(campaign_id, annotator_id):
    from models import CampaignAnnotator
    c = Campaign.query.get_or_404(campaign_id)
    link = CampaignAnnotator.query.filter_by(campaign_id=c.id, annotator_id=annotator_id).first()
    if not link:
        link = CampaignAnnotator(campaign_id=c.id, annotator_id=annotator_id)
        db.session.add(link)
    try:
        link.quality_score = float(request.form.get("quality_score") or 100.0)
    except (TypeError, ValueError):
        pass
    link.has_star = request.form.get("has_star") == "1"
    db.session.commit()
    flash("Annotator evaluation updated.", "success")
    return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))
    return jsonify({"rows": rows, "n_annotators": len(rows)})


@admin_campaign_bp.route("/campaign/<campaign_id>/close", methods=["POST"])
@require_admin
def admin_campaign_close(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        flash("Campaign is already closed.", "info")
    else:
        c.closed_at = datetime.utcnow()
        db.session.commit()
        # Freeze a results snapshot so the closed campaign keeps its final stats.
        try:
            crit = get_criteria_for(c)
            all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
            c.results_snapshot_json = json.dumps(
                results.compute_results(c, all_ratings, crit), ensure_ascii=False)
            db.session.commit()
        except Exception:
            logger.exception("Results snapshot failed for %s", c.id)
        # Build a complete results ZIP and email it to the campaign owner.
        try:
            crit = get_criteria_for(c)
            all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
            completed = [r for r in all_ratings if c.rating_is_complete(r, crit)]
            res = results.compute_results(c, all_ratings, crit)
            charts = results.build_charts(res)
            try:
                try:
                    with open(os.path.join(current_app.static_folder, "results.js"), encoding="utf-8") as fh:
                        _results_js = fh.read()
                except OSError:
                    _results_js = ""
                report_html = render_template(
                    "results_report.html", campaign=c, results=res, charts=charts,
                    frozen=True, results_js=_results_js,
                    generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
            except Exception:
                logger.exception("Report render for email failed for %s", c.id)
                report_html = None
            zip_bytes = exporter.build_results_zip(c, completed, crit, res, charts, report_html)
            to_email = (c.owner_email or ADMIN_EMAIL or "").strip()
            fname = exporter._safe_filename(c.name) + "_results.zip"
            subject = f"Aura results: {c.name}"
            body = (f"Your Aura campaign \"{c.name}\" has been closed.\n\n"
                    f"Attached is a ZIP with the full results: the per-rating CSV, a results "
                    f"summary, all charts as PNGs, the raw results JSON, and an HTML report.\n\n"
                    f"Completed annotations: {len(completed)}.")
            ok, detail = mailer.send_email(to_email, subject, body,
                                        attachments=[(fname, zip_bytes, "application/zip")])
            if ok:
                flash(f"Campaign closed. Results emailed to {to_email}.", "success")
            elif mailer.active_transport():
                flash(f"Campaign closed, but the results email failed: {detail} "
                    f"You can still download everything from the results page.", "error")
            else:
                flash("Campaign closed. Email isn't configured, so results weren't sent; "
                    "download them from the results page (or set up email to receive them).",
                    "info")
        except Exception:
            logger.exception("Building/sending results email failed for %s", c.id)
            flash("Campaign closed. Annotators can no longer edit. "
                "(Preparing the results email failed; see the results page.)", "info")
    return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))


@admin_campaign_bp.route("/campaign/<campaign_id>/delete", methods=["POST"])
@require_admin
def admin_campaign_delete(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    # Require the admin to type the campaign name to confirm
    confirm = request.form.get("confirm_name", "").strip()
    if confirm != c.name:
        flash("Deletion cancelled: the name you typed didn't match.", "error")
        return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))
    name = c.name
    # Cascade deletes ratings (relationship cascade) -- delete the campaign row
    db.session.delete(c)
    db.session.commit()
    flash(f"Campaign '{name}' and all its ratings were deleted.", "success")
    return redirect(url_for("admin_campaign.admin_dashboard"))


# ---------------------------------------------------------------------------
# Campaign groups: bundle related campaigns (e.g. easy/medium/hard variants of one
# study) so their results can be viewed and exported together.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Editing an annotator's profile (admin). Lets admins correct or fill in the
# social / background variables (location, dialect, age group) plus language
# metadata, which help interpret an annotator's ratings.
# ---------------------------------------------------------------------------

@admin_campaign_bp.route("/annotator/<annotator_id>/edit", methods=["GET", "POST"])
@require_admin
def admin_annotator_edit(annotator_id):
    ann = Annotator.query.get_or_404(annotator_id)
    # Where to return afterwards (e.g. back to the campaign the admin came from).
    back = request.values.get("next") or url_for("admin_campaign.admin_dashboard")

    if request.method == "POST":
        ann.name = (request.form.get("name") or ann.name).strip()[:200]
        ann.native_language = (request.form.get("native_language") or "").strip()[:100]
        expertise = (request.form.get("expertise") or "").strip().lower()
        ann.expertise = expertise if expertise in ("easy", "medium", "hard") else ""
        ann.source_fluency = normalize_fluency(request.form.get("source_fluency", ""))
        ann.target_fluency = normalize_fluency(request.form.get("target_fluency", ""))
        ann.location = (request.form.get("location") or "").strip()[:120]
        ann.dialect = (request.form.get("dialect") or "").strip()[:120]
        ann.age_group = normalize_age_group(request.form.get("age_group", ""))
        db.session.commit()
        flash(f"Updated {ann.name}'s profile.", "success")
        return redirect(back)

    return render_template("admin_annotator_edit.html",
                        annotator=ann, back=back,
                        fluency_levels=FLUENCY_LEVELS, age_groups=AGE_GROUPS)


@admin_campaign_bp.route("/groups/new", methods=["POST"])
@require_admin
def admin_group_new():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash("A group name is required.", "error")
        return redirect(url_for("admin_campaign.admin_dashboard"))
    group = CampaignGroup(name=name, description=description, owner_email=ADMIN_EMAIL)
    db.session.add(group)
    db.session.commit()
    # Optionally attach a campaign immediately (from the campaign detail page).
    cid = (request.form.get("campaign_id") or "").strip()
    if cid:
        c = db.session.get(Campaign, cid)
        if c:
            c.group_id = group.id
            db.session.commit()
        flash(f"Created group '{name}' and added this campaign to it.", "success")
        return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=cid))
    flash(f"Created group '{name}'.", "success")
    return redirect(url_for("admin_campaign.admin_dashboard"))


@admin_campaign_bp.route("/groups/<group_id>/delete", methods=["POST"])
@require_admin
def admin_group_delete(group_id):
    group = CampaignGroup.query.get_or_404(group_id)
    name = group.name
    # Detach campaigns first (deleting a group never deletes its campaigns).
    for c in group.ordered_campaigns():
        c.group_id = None
    db.session.delete(group)
    db.session.commit()
    flash(f"Deleted group '{name}'. Its campaigns were kept and un-grouped.", "success")
    return redirect(url_for("admin_campaign.admin_dashboard"))


@admin_campaign_bp.route("/campaign/<campaign_id>/group", methods=["POST"])
@require_admin
def admin_campaign_set_group(campaign_id):
    """Assign this campaign to an existing group, or remove it from its group."""
    c = Campaign.query.get_or_404(campaign_id)
    group_id = (request.form.get("group_id") or "").strip()
    if not group_id:
        c.group_id = None
        db.session.commit()
        flash("Removed this campaign from its group.", "success")
        return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))
    group = db.session.get(CampaignGroup, group_id)
    if not group:
        flash("That group no longer exists.", "error")
        return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))
    c.group_id = group.id
    db.session.commit()
    flash(f"Added this campaign to '{group.name}'.", "success")
    return redirect(url_for("admin_campaign.admin_campaign_detail", campaign_id=c.id))


@admin_campaign_bp.route("/campaign/<campaign_id>/download_csv")
@require_admin
def admin_campaign_download_csv(campaign_id):
    """Download the master CSV directly from the server (alongside Drive)."""
    from flask import Response
    c = Campaign.query.get_or_404(campaign_id)
    crit = get_criteria_for(c)
    crit_ids = [cr["id"] for cr in crit]
    all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
    completed = [r for r in all_ratings if c.rating_is_complete(r, get_criteria_for(c))]
    data = exporter.build_master_csv(c, completed, crit)
    filename = exporter._safe_filename(c.name) + "_master.csv"
    return Response(
        data, mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@admin_campaign_bp.route("/campaign/ai/test", methods=["POST"])
@require_admin
def admin_ai_test():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip()
    meta = llm.provider_by_id(provider)
    if not meta:
        return jsonify({"ok": False, "message": "Choose a provider first."})
    key = (data.get("api_key") or "").strip()
    # If no fresh key was typed, fall back to the campaign's stored key.
    if not key and data.get("campaign_id"):
        c = Campaign.query.get(data["campaign_id"])
        if c:
            key = llm.decrypt_secret(c.ai_api_key_enc or "", current_app.config["SECRET_KEY"],)
    cfg = {
        "provider": provider,
        "model": (data.get("model") or "").strip() or meta["default_model"],
        "base_url": (data.get("base_url") or "").strip() or meta["default_base_url"],
        "api_key": key,
    }
    if not llm.is_configured(cfg):
        missing = []
        if not cfg["model"]:
            missing.append("model")
        if meta["needs_key"] and not cfg["api_key"]:
            missing.append("API key")
        if meta["needs_base_url"] and not cfg["base_url"]:
            missing.append("endpoint URL")
        return jsonify({"ok": False, "message": "Missing: " + ", ".join(missing)})
    ok, msg = llm.test_config(cfg)
    return jsonify({"ok": ok, "message": msg})