from flask import Blueprint, render_template, request, session, jsonify, current_app, redirect, url_for, flash
import json
from datetime import datetime

from models import Campaign, Rating, AssistantLog, AnnotatorCampaignPref
from extensions import db
from utils.constants import get_criteria_for, get_scale_for
from services.auth_service import require_annotator, current_annotator
from services.qualification import can_annotate
from services.ai_assistant import _assist_system_prompt, _assist_user_prompt
import llm

annotator_rating_bp = Blueprint(
    "annotator_rating",
    __name__,
)


@annotator_rating_bp.route("/campaign/<campaign_id>/rate")
@require_annotator
def rate_view(campaign_id):
    from models import CampaignAnnotator
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    session["last_campaign_id"] = campaign_id
    ann = current_annotator()
    if not can_annotate(ann):
        flash("Please complete the qualification test before joining this campaign.", "info")
        return redirect(url_for("annotator_qualification.qualification"))
    criteria = get_criteria_for(c)
    mode = c.mode
    link = CampaignAnnotator.query.filter_by(campaign_id=c.id, annotator_id=ann.id).first()
    if not link:
        link = CampaignAnnotator(campaign_id=c.id, annotator_id=ann.id)
        db.session.add(link)
        db.session.commit()
    # Load existing ratings for this (campaign, annotator)
    existing = {r.segment_id: r for r in
                Rating.query.filter_by(campaign_id=c.id, annotator_id=ann.id).all()}
    existing_payload = {
        seg_id: {"scores": r.scores_dict(), "spans": r.spans_dict(),
                "comments": r.comments or "",
                "preference": r.preference or "",
                "reviewed": bool(r.reviewed),
                "edited_text": (r.edited_text or "")}
        for seg_id, r in existing.items()
    }
    # Segments served to this annotator (after the difficulty-serving filter, if any).
    served = c.served_segments_for(ann)
    served_ids = {s.get("id") for s in served}
    completed_count = sum(1 for sid, r in existing.items()
                        if sid in served_ids and c.rating_is_complete(r, criteria))
    scale = get_scale_for(c)
    # Build a small legend for the instructions panel (works for likert + continuous).
    if scale["type"] == "likert":
        scale_legend = [{"value": v, "label": scale["labels"].get(str(v), "")}
                        for v in scale["values"]]
    else:
        scale_legend = [
            {"value": scale["min"], "label": scale.get("min_label") or "minimum"},
            {"value": scale["max"], "label": scale.get("max_label") or "maximum"},
        ]

    # For pairwise mode, attach normalized candidate info to each segment so the
    # client can render two panels without guessing the field names.
    segments = [dict(s) for s in served]
    # Difficulty is for the admin/results only — never shown to annotators, so we do not
    # attach it to the segment objects sent to the rating page.
    if mode == "pairwise":
        for s in segments:
            a, b, sa, sb = c.pairwise_candidates(s)
            s["_cand_a"], s["_cand_b"] = a, b
            s["_sys_a"], s["_sys_b"] = sa, sb

    in_label, out_label = c.io_labels()
    return render_template(
        "rate.html",
        campaign=c, annotator=ann,
        segments=segments, criteria=criteria,
        eval_mode=mode, preferences=c.preferences,
        input_label=in_label, output_label=out_label,
        is_bilingual=c.is_bilingual,
        scale=scale, scale_legend=scale_legend,
        existing=existing_payload, completed_count=completed_count,
        span_scope=c.span_scope or "target",
        enable_spans=(c.enable_spans if c.enable_spans is not None else True) if mode != "pairwise" else False,
        instructions=c.instructions or "",
        span_instructions=c.span_instructions or "",
        segments_per_page=c.per_page_for(ann),
        ai_available=llm.is_configured(llm.effective_config(c, current_app.config["SECRET_KEY"])),
        ai_ab_enabled=bool(c.ai_ab_enabled),
        ai_ab_eligible={s["id"]: c.ai_ab_eligible(ann.id, s["id"]) for s in segments},
        link=link,
    )


@annotator_rating_bp.route("/campaign/<campaign_id>/api/config")
@require_annotator
def api_config(campaign_id):
    """Lightweight, pollable view of the annotator-visible configuration.
    The rating page polls this and re-renders itself when `version` changes,
    so admin edits show up without a manual refresh."""
    c = Campaign.query.get_or_404(campaign_id)
    ann = current_annotator()
    criteria = get_criteria_for(c)
    scale = get_scale_for(c)
    if scale["type"] == "likert":
        scale_legend = [{"value": v, "label": scale["labels"].get(str(v), "")}
                        for v in scale["values"]]
    else:
        scale_legend = [
            {"value": scale["min"], "label": scale.get("min_label") or "minimum"},
            {"value": scale["max"], "label": scale.get("max_label") or "maximum"},
        ]
    return jsonify({
        "ok": True,
        "version": c.config_fingerprint(),
        "closed": c.is_closed,
        "eval_mode": c.mode,
        "criteria": criteria,
        "scale": scale,
        "scale_legend": scale_legend,
        "preferences": c.preferences,
        "enable_spans": (c.enable_spans if c.enable_spans is not None else True) if c.mode != "pairwise" else False,
        "span_scope": c.span_scope or "target",
        "span_instructions": c.span_instructions or "",
        "segments_per_page": c.per_page_for(ann),
        "min_segments_per_page": c.per_page_bounds()[0],
        "max_segments_per_page": c.per_page_bounds()[2],
        "instructions": c.instructions or "",
        "rating_position": c.rating_position or "side",
        "span_position": c.span_position or "below",
        "segments": [dict(s) for s in c.served_segments_for(ann)],
    })


@annotator_rating_bp.route("/campaign/<campaign_id>/api/per-page", methods=["POST"])
@require_annotator
def api_set_per_page(campaign_id):
    """Persist this annotator's segments-per-page choice for this campaign (clamped to the
    admin's min/max range), so it follows them across devices and browsers."""
    c = Campaign.query.get_or_404(campaign_id)
    ann = current_annotator()
    data = request.get_json(silent=True) or {}
    try:
        requested = int(data.get("segments_per_page"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid value."}), 400
    lo, _default, hi = c.per_page_bounds()
    value = max(lo, min(requested, hi))

    pref = AnnotatorCampaignPref.query.filter_by(
        annotator_id=ann.id, campaign_id=c.id).first()
    if pref is None:
        pref = AnnotatorCampaignPref(annotator_id=ann.id, campaign_id=c.id)
        db.session.add(pref)
    pref.segments_per_page = value
    db.session.commit()
    return jsonify({"ok": True, "segments_per_page": value})


@annotator_rating_bp.route("/campaign/<campaign_id>/api/submit", methods=["POST"])
@require_annotator
def api_submit(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return jsonify({"ok": False, "error": "Campaign is closed."}), 403
    ann = current_annotator()
    if not can_annotate(ann):
        return jsonify({"ok": False, "error": "You must pass the qualification test before submitting ratings."}), 403
    criteria = get_criteria_for(c)
    data = request.get_json(silent=True) or {}

    segment_id = (data.get("segment_id") or "").strip()
    scores = data.get("scores") or {}
    spans = data.get("spans") or {}
    comments = (data.get("comments") or "").strip()
    preference = (data.get("preference") or "").strip()
    reviewed = bool(data.get("reviewed"))
    edited_text = data.get("edited_text")
    if edited_text is not None:
        edited_text = str(edited_text)
    time_spent = data.get("time_spent_seconds", 0)

    seg = c.segment_by_id(segment_id)
    if not seg:
        return jsonify({"ok": False, "error": "Unknown segment_id."}), 400

    mode = c.mode
    clean_scores = {}
    clean_spans = {}
    clean_pref = ""

    if mode == "likert":
        # Validate scores against this campaign's scale bounds
        lo, hi = c.score_bounds
        for crit in criteria:
            v = scores.get(crit["id"])
            if v is None or not isinstance(v, int) or v < lo or v > hi:
                return jsonify({"ok": False, "error": f"Missing or invalid rating for {crit['name']}."}), 400
            clean_scores[crit["id"]] = v

    if mode == "pairwise":
        valid_pref_ids = {p["id"] for p in c.preferences}
        if preference not in valid_pref_ids:
            return jsonify({"ok": False, "error": "Please choose a preference option."}), 400
        clean_pref = preference

    # Spans apply in likert (if enabled) and span_only modes.
    span_enabled = (c.enable_spans if c.enable_spans is not None else True)
    if mode == "pairwise":
        span_enabled = False
    if mode == "span_only":
        span_enabled = True

    if span_enabled:
        # A span item is [start, end] (target) OR [start, end, "source"/"target"] in "both" mode.
        target_len = len(seg.get("target", ""))
        source_len = len(seg.get("source", ""))
        span_scope = c.span_scope or "target"
        for crit in criteria:
            raw = spans.get(crit["id"], [])
            clean = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, list) or len(item) < 2:
                        continue
                    s, e = item[0], item[1]
                    if not (isinstance(s, int) and isinstance(e, int)):
                        continue
                    which = item[2] if (len(item) >= 3 and span_scope == "both") else "target"
                    if which == "source" and span_scope == "both":
                        if 0 <= s < e <= source_len:
                            clean.append([s, e, "source"])
                    else:
                        if 0 <= s < e <= target_len:
                            if span_scope == "both":
                                clean.append([s, e, "target"])
                            else:
                                clean.append([s, e])
            clean_spans[crit["id"]] = clean

    # Upsert
    rating = Rating.query.filter_by(
        campaign_id=c.id, annotator_id=ann.id, segment_id=segment_id
    ).first()
    if not rating:
        rating = Rating(campaign_id=c.id, annotator_id=ann.id, segment_id=segment_id)
        db.session.add(rating)
    rating.scores_json = json.dumps(clean_scores)
    rating.spans_json = json.dumps(clean_spans, ensure_ascii=False)
    rating.comments = comments
    rating.preference = clean_pref
    if mode == "span_only":
        rating.reviewed = reviewed
    if mode == "post_edit" and edited_text is not None:
        rating.edited_text = edited_text[:20000]
    if isinstance(time_spent, (int, float)) and time_spent > 0:
        rating.time_spent_seconds = (rating.time_spent_seconds or 0) + int(time_spent)
    rating.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"ok": True})




@annotator_rating_bp.route("/campaign/<campaign_id>/api/assist", methods=["POST"])
@require_annotator
def api_assist(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return jsonify({"ok": False, "error": "Campaign is closed."}), 403
    cfg = llm.effective_config(c, current_app.config["SECRET_KEY"])
    if not llm.is_configured(cfg):
        return jsonify({"ok": False, "error": "The guideline assistant isn't configured for this campaign."}), 400
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "Please enter a question."}), 400
    if len(question) > 1000:
        question = question[:1000]

    # Map every segment id to its global 1-based number (matches the UI labels).
    id_to_n = {}
    seg_by_id = {}
    for i, s in enumerate(c.segments):
        sid = s.get("id")
        if sid is not None:
            id_to_n[str(sid)] = i + 1
            seg_by_id[str(sid)] = s

    # The client tells us which segments the annotator can currently see (+ their
    # in-progress judgments). Fall back to a single focused segment_id.
    raw_segs = data.get("segments")
    if not isinstance(raw_segs, list):
        sid = (data.get("segment_id") or "").strip()
        raw_segs = [{"id": sid, "judgment": data.get("judgment") or ""}] if sid else []
    segctx = []
    seen = set()
    for item in raw_segs[:25]:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "")
        if sid not in seg_by_id or sid in seen:
            continue
        seen.add(sid)
        segctx.append({"n": id_to_n[sid], "seg": seg_by_id[sid],
                    "judgment": (str(item.get("judgment") or ""))[:300]})
    segctx.sort(key=lambda x: x["n"])
    focused_id = (data.get("segment_id") or (segctx[0]["seg"].get("id") if segctx else "")) or ""

    # Randomised AI experiment: refuse help on segments in the no-AI arm for this annotator.
    if c.ai_ab_enabled:
        ann = current_annotator()
        if focused_id and not c.ai_ab_eligible(ann.id, focused_id):
            return jsonify({"ok": False,
                            "error": "For this study, the AI assistant is turned off on this item. "
                                    "Please use your own judgment here."}), 403
        segctx = [s for s in segctx
                if c.ai_ab_eligible(ann.id, str(s["seg"].get("id") or ""))]

    # Recent conversation turns for follow-up continuity.
    raw_hist = data.get("history") if isinstance(data.get("history"), list) else []
    history = []
    for h in raw_hist[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
            history.append({"role": h["role"], "content": str(h["content"])[:2000]})

    criteria = get_criteria_for(c)
    try:
        answer = llm.call_llm(cfg, _assist_system_prompt(),
                            _assist_user_prompt(c, segctx, criteria, question),
                            history=history, max_tokens=500)
    except llm.LLMError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    if not answer:
        return jsonify({"ok": False, "error": "The model returned an empty response."}), 502
    ann = current_annotator()
    log = AssistantLog(campaign_id=c.id, annotator_id=ann.id, segment_id=str(focused_id),
                    role="annotator", provider=cfg["provider"], model=cfg["model"],
                    question=question, answer=answer)
    db.session.add(log)
    db.session.commit()
    return jsonify({"ok": True, "answer": answer, "log_id": log.id})


@annotator_rating_bp.route("/campaign/<campaign_id>/api/assist/feedback", methods=["POST"])
@require_annotator
def api_assist_feedback(campaign_id):
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip()
    if action not in ("helpful", "dismissed", "reconsidered"):
        return jsonify({"ok": False, "error": "Unknown action."}), 400
    log = AssistantLog.query.filter_by(id=data.get("log_id"), campaign_id=campaign_id).first()
    if log:
        log.action = action
        db.session.commit()
    return jsonify({"ok": True})


@annotator_rating_bp.route("/campaign/<campaign_id>/api/submit_form_b", methods=["POST"])
@require_annotator
def submit_form_b(campaign_id):
    from models import CampaignAnnotator
    c = Campaign.query.get_or_404(campaign_id)
    ann = current_annotator()
    link = CampaignAnnotator.query.filter_by(campaign_id=c.id, annotator_id=ann.id).first()
    if not link:
        link = CampaignAnnotator(campaign_id=c.id, annotator_id=ann.id)
        db.session.add(link)
    
    link.location = request.form.get("location", "").strip()
    link.parents_language = request.form.get("parents_language", "").strip()
    link.stayed_outside = request.form.get("stayed_outside") == "1"
    link.stayed_outside_duration = request.form.get("stayed_outside_duration", "").strip()
    link.stayed_outside_purpose = request.form.get("stayed_outside_purpose", "").strip()
    link.exposure = request.form.get("exposure", "").strip()
    link.form_submitted = True
    
    db.session.commit()
    return jsonify({"ok": True, "message": "Background details submitted successfully!"})