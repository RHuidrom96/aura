"""
Admin management of qualification tests.

Mirrors the shape of routes/admin/campaigns.py (collect-form helper +
new/edit views), scaled down to what a qualification test needs:
metadata, evaluation criteria, passing score, active flag, and segments.
"""

import json
import logging

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)

from models import db, QualificationTest, QualificationSegment
from utils.constants import EVAL_MODES
from utils.forms import parse_criteria_from_form
from services.auth_service import require_admin

logger = logging.getLogger(__name__)

admin_qualification_bp = Blueprint(
    "admin_qualification",
    __name__,
    url_prefix="/admin",
)

_EVAL_MODE_IDS = {m["id"] for m in EVAL_MODES}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_qualification_segments(segments, eval_mode, criteria):
    """Return a list of error strings for the given segments under a mode."""
    errors = []

    if not isinstance(segments, list) or len(segments) == 0:
        return ["Segments JSON must be a non-empty array."]

    criterion_ids = {c["id"] for c in criteria}

    for i, s in enumerate(segments):
        n = i + 1

        if not isinstance(s, dict):
            errors.append(f"Segment {n}: must be an object.")
            continue

        if not (s.get("source") or "").strip() if isinstance(s.get("source"), str) else not s.get("source"):
            errors.append(f"Segment {n}: must have a 'source'.")

        if eval_mode == "pairwise":
            if not s.get("target_a") or not s.get("target_b"):
                errors.append(
                    f"Segment {n}: pairwise mode needs 'target_a' and 'target_b'."
                )
        else:
            if not s.get("target"):
                errors.append(f"Segment {n}: must have a 'target'.")

        gold = s.get("gold")

        if eval_mode == "likert":
            if not isinstance(gold, dict) or not isinstance(gold.get("scores"), dict) or not gold.get("scores"):
                errors.append(
                    f'Segment {n}: needs a gold answer, e.g. {{"scores": {{...}}}}.'
                )
            elif criterion_ids and set(gold["scores"].keys()) != criterion_ids:
                errors.append(
                    f"Segment {n}: gold scores must cover exactly these criteria: "
                    + ", ".join(sorted(criterion_ids))
                    + "."
                )
        elif eval_mode == "pairwise":
            if not isinstance(gold, dict) or not gold.get("preference"):
                errors.append(
                    f'Segment {n}: needs a gold answer, e.g. {{"preference": "a"}}.'
                )
        elif eval_mode == "post_edit":
            if not isinstance(gold, dict) or not (gold.get("edited_text") or "").strip():
                errors.append(
                    f'Segment {n}: needs a gold answer, e.g. {{"edited_text": "..."}}.'
                )
        elif eval_mode == "span_only":
            if not isinstance(gold, dict) or not isinstance(gold.get("spans"), dict):
                errors.append(
                    f'Segment {n}: needs a gold answer, e.g. {{"spans": {{...}}}}.'
                )
        # Gold spans are character offsets into `target` ([start, end),
        # end-exclusive), keyed by criterion id, sorted ascending by start --
        # this must match exactly what the annotator's span-marking UI
        # produces (see templates/qualification/test.html), since grading
        # is exact dict equality (see services/qualification.grade_attempt).

    return errors


def _segments_as_dicts(test):
    """Represent a test's current segments the same way freshly-parsed JSON would,
    so they can be re-validated against a (possibly changed) evaluation mode."""
    out = []
    for seg in test.segments.order_by(QualificationSegment.position).all():
        d = {
            "source": seg.source,
            "target": seg.target,
            "gold": seg.gold,
        }
        if seg.target_a:
            d["target_a"] = seg.target_a
        if seg.target_b:
            d["target_b"] = seg.target_b
        out.append(d)
    return out


def _write_segments(test, segments):
    """Replace all of a test's segments with a freshly-parsed list of dicts."""
    for seg in list(test.segments):
        db.session.delete(seg)

    for i, s in enumerate(segments):
        db.session.add(
            QualificationSegment(
                test=test,
                position=i + 1,
                source=s.get("source", ""),
                target=s.get("target", ""),
                target_a=s.get("target_a", ""),
                target_b=s.get("target_b", ""),
                reference=s.get("reference", ""),
                difficulty=s.get("difficulty", "medium"),
                gold_json=json.dumps(s.get("gold") or {}, ensure_ascii=False),
            )
        )


def _collect_qualification_form(
    form,
    files,
    *,
    parse_segments,
    existing_segments=None,
    field_prefix="",
    segments_file_key="segments_file",
    segments_paste_key="segments_paste",
):
    """Read + validate all qualification-test fields from a submitted form.

    Returns (config_kwargs, segments_or_None, errors, form_view).
    """
    errors = []
    prefix = field_prefix or ""

    title = (form.get(prefix + "title") or "").strip()
    language = (form.get(prefix + "language") or "").strip()
    description = (form.get(prefix + "description") or "").strip()

    eval_mode = (form.get(prefix + "eval_mode") or "likert").strip()
    if eval_mode not in _EVAL_MODE_IDS:
        errors.append("Invalid evaluation mode.")
        eval_mode = "likert"

    try:
        passing_score = float(form.get(prefix + "passing_score") or 80)
    except (TypeError, ValueError):
        passing_score = 80.0
    passing_score = max(0.0, min(100.0, passing_score))

    try:
        time_limit_minutes = int(form.get(prefix + "time_limit_minutes") or 30)
    except (TypeError, ValueError):
        time_limit_minutes = 30
    time_limit_minutes = max(1, time_limit_minutes)

    # Retry policy: blank or zero falls back to the model's defaults.
    try:
        max_attempts = int(form.get(prefix + "max_attempts") or 0)
    except (TypeError, ValueError):
        max_attempts = 0
    max_attempts = max_attempts if max_attempts > 0 else QualificationTest.DEFAULT_MAX_ATTEMPTS

    try:
        retry_cooldown_minutes = int(form.get(prefix + "retry_cooldown_minutes") or 0)
    except (TypeError, ValueError):
        retry_cooldown_minutes = 0
    retry_cooldown_minutes = (retry_cooldown_minutes if retry_cooldown_minutes > 0
                             else QualificationTest.DEFAULT_COOLDOWN_MINUTES)

    is_active = form.get(prefix + "is_active") == "on"

    criteria, criteria_missing_desc = parse_criteria_from_form(form, field_prefix=prefix)

    # ---- validation ----
    if not title:
        errors.append("Title is required.")
    if not language:
        errors.append("Language is required.")

    if eval_mode in ("likert", "span_only") and len(criteria) == 0:
        label = "evaluation criterion" if eval_mode == "likert" else "error-type category"
        errors.append(f"Please define at least one {label}.")
    if criteria_missing_desc:
        names = ", ".join(criteria_missing_desc)
        errors.append(
            "Each criterion needs a definition shown to annotators. "
            f"Missing definition for: {names}."
        )

    # ---- segments ----
    segments = None
    if parse_segments:
        segments_raw = ""
        upload = files.get(segments_file_key) if files else None
        if upload and upload.filename:
            try:
                segments_raw = upload.read().decode("utf-8")
            except UnicodeDecodeError:
                errors.append("Could not read the uploaded file as UTF-8.")
                segments_raw = ""
        else:
            segments_raw = (form.get(segments_paste_key) or "").strip()

        if not segments_raw:
            errors.append("Please upload or paste the segments JSON file.")
        else:
            try:
                segments = json.loads(segments_raw)
                errors.extend(
                    _validate_qualification_segments(segments, eval_mode, criteria)
                )
            except json.JSONDecodeError as e:
                errors.append(f"Segments JSON is not valid: {e}")
        form_segments_paste = segments_raw
    else:
        # Editing without a replacement upload: re-validate the existing
        # segments in case the evaluation mode changed underneath them.
        if existing_segments is not None:
            errors.extend(
                _validate_qualification_segments(existing_segments, eval_mode, criteria)
            )
        form_segments_paste = ""

    config = {
        "title": title,
        "language": language,
        "description": description,
        "passing_score": passing_score,
        "time_limit_minutes": time_limit_minutes,
        "max_attempts": max_attempts,
        "retry_cooldown_minutes": retry_cooldown_minutes,
        "eval_mode": eval_mode,
        "criteria_json": json.dumps(criteria, ensure_ascii=False),
        "is_active": is_active,
    }

    form_view = {
        prefix + "title": title,
        prefix + "language": language,
        prefix + "description": description,
        prefix + "eval_mode": eval_mode,
        prefix + "passing_score": passing_score,
        prefix + "time_limit_minutes": time_limit_minutes,
        prefix + "max_attempts": max_attempts,
        prefix + "retry_cooldown_minutes": retry_cooldown_minutes,
        prefix + "is_active": is_active,
        prefix + "criteria": [{"name": c["name"], "desc": c["guide"]} for c in criteria],
        prefix + "segments_paste": form_segments_paste,
    }

    return config, segments, errors, form_view


def _default_qualification_form_view():
    return {
        "title": "",
        "language": "",
        "description": "",
        "eval_mode": "likert",
        "passing_score": 80,
        "time_limit_minutes": 30,
        "max_attempts": QualificationTest.DEFAULT_MAX_ATTEMPTS,
        "retry_cooldown_minutes": QualificationTest.DEFAULT_COOLDOWN_MINUTES,
        "is_active": True,
        "criteria": [],
        "segments_paste": "",
    }


def _deactivate_other_tests(exclude_id, campaign_id=None):
    """Keep only one active qualification test per campaign scope.

    For campaign-specific tests, only other tests for the same campaign are
    deactivated. Global tests remain independent.
    """
    query = QualificationTest.query.filter(
        QualificationTest.id != exclude_id,
        QualificationTest.is_active.is_(True),
    )
    if campaign_id is None:
        query = query.filter(QualificationTest.campaign_id.is_(None))
    else:
        query = query.filter(QualificationTest.campaign_id == campaign_id)
    query.update({"is_active": False}, synchronize_session="fetch")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_qualification_bp.route("/qualification")
@require_admin
def admin_qualification_list():
    tests = (
        QualificationTest.query
        .order_by(QualificationTest.created_at.desc())
        .all()
    )
    return render_template(
        "admin_qualification_list.html",
        tests=tests,
    )


@admin_qualification_bp.route("/qualification/new", methods=["GET", "POST"])
@require_admin
def admin_qualification_new():
    if request.method == "POST":

        config, segments, errors, form_view = _collect_qualification_form(
            request.form, request.files, parse_segments=True,
        )

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "admin_qualification_new.html",
                form_data=form_view,
                eval_modes=EVAL_MODES,
            )

        test = QualificationTest(**config)
        db.session.add(test)
        db.session.flush()  # assign test.id so segments can reference it

        _write_segments(test, segments)

        if test.is_active:
            _deactivate_other_tests(test.id)

        db.session.commit()

        flash(f"Qualification test '{test.title}' created.", "success")
        return redirect(url_for("admin_qualification.admin_qualification_list"))

    return render_template(
        "admin_qualification_new.html",
        form_data=_default_qualification_form_view(),
        eval_modes=EVAL_MODES,
    )


@admin_qualification_bp.route("/qualification/<test_id>/edit", methods=["GET", "POST"])
@require_admin
def admin_qualification_edit(test_id):
    test = QualificationTest.query.get_or_404(test_id)

    if request.method == "POST":

        upload = request.files.get("segments_file")
        has_upload = bool(upload and upload.filename)
        has_paste = bool((request.form.get("segments_paste") or "").strip())
        replacing_segments = has_upload or has_paste

        existing_segments = None if replacing_segments else _segments_as_dicts(test)

        config, segments, errors, form_view = _collect_qualification_form(
            request.form, request.files,
            parse_segments=replacing_segments,
            existing_segments=existing_segments,
        )

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "admin_qualification_edit.html",
                test=test,
                form_data=form_view,
                eval_modes=EVAL_MODES,
            )

        for key, value in config.items():
            setattr(test, key, value)

        if segments is not None:
            _write_segments(test, segments)

        if test.is_active:
            _deactivate_other_tests(test.id)

        db.session.commit()

        flash(f"Qualification test '{test.title}' updated.", "success")
        return redirect(url_for("admin_qualification.admin_qualification_list"))

    form_view = {
        "title": test.title,
        "language": test.language,
        "description": test.description or "",
        "eval_mode": test.eval_mode,
        "passing_score": test.passing_score,
        "time_limit_minutes": test.time_limit_minutes,
        "max_attempts": test.max_attempts or QualificationTest.DEFAULT_MAX_ATTEMPTS,
        "retry_cooldown_minutes": test.retry_cooldown_minutes or QualificationTest.DEFAULT_COOLDOWN_MINUTES,
        "is_active": test.is_active,
        "criteria": [
            {"name": c.get("name", ""), "desc": c.get("guide", "")}
            for c in test.criteria
        ],
        "segments_paste": "",
    }

    return render_template(
        "admin_qualification_edit.html",
        test=test,
        form_data=form_view,
        eval_modes=EVAL_MODES,
    )


@admin_qualification_bp.route("/qualification/<test_id>/toggle", methods=["POST"])
@require_admin
def admin_qualification_toggle(test_id):
    test = QualificationTest.query.get_or_404(test_id)

    test.is_active = not test.is_active

    if test.is_active:
        _deactivate_other_tests(test.id)

    db.session.commit()

    flash(
        "Qualification test '{}' is now {}.".format(
            test.title,
            "active" if test.is_active else "inactive",
        ),
        "success",
    )

    return redirect(url_for("admin_qualification.admin_qualification_list"))
