"""
Helpers for building/validating a campaign's qualification test.

Qualification is no longer a standalone, globally-managed entity: every
campaign owns exactly one qualification test, created inline at the bottom of
the Campaign Creation page and edited from the campaign's own detail page
(see routes/admin/campaigns.py). This module keeps the form-collection /
segment-validation logic that used to live behind the standalone
"Qualification tests" admin pages, so that logic is reused rather than
duplicated.
"""

import json

from models import db, QualificationTest, QualificationSegment
from utils.constants import EVAL_MODES
from utils.forms import parse_criteria_from_form

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


def _collect_qualification_form(form, files, *, parse_segments, existing_segments=None):
    """Read + validate all qualification-test fields from a submitted form.

    Returns (config_kwargs, segments_or_None, errors, form_view).
    """
    errors = []

    title = (form.get("title") or "").strip()
    language = (form.get("language") or "").strip()
    description = (form.get("description") or "").strip()

    eval_mode = (form.get("eval_mode") or "likert").strip()
    if eval_mode not in _EVAL_MODE_IDS:
        errors.append("Invalid evaluation mode.")
        eval_mode = "likert"

    try:
        passing_score = float(form.get("passing_score") or 80)
    except (TypeError, ValueError):
        passing_score = 80.0
    passing_score = max(0.0, min(100.0, passing_score))

    try:
        time_limit_minutes = int(form.get("time_limit_minutes") or 30)
    except (TypeError, ValueError):
        time_limit_minutes = 30
    time_limit_minutes = max(1, time_limit_minutes)

    # Retry policy: blank or zero falls back to the model's defaults.
    try:
        max_attempts = int(form.get("max_attempts") or 0)
    except (TypeError, ValueError):
        max_attempts = 0
    max_attempts = max_attempts if max_attempts > 0 else QualificationTest.DEFAULT_MAX_ATTEMPTS

    try:
        retry_cooldown_minutes = int(form.get("retry_cooldown_minutes") or 0)
    except (TypeError, ValueError):
        retry_cooldown_minutes = 0
    retry_cooldown_minutes = (retry_cooldown_minutes if retry_cooldown_minutes > 0
                             else QualificationTest.DEFAULT_COOLDOWN_MINUTES)

    is_active = form.get("is_active") == "on"

    criteria, criteria_missing_desc = parse_criteria_from_form(form)

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
        upload = files.get("segments_file") if files else None
        if upload and upload.filename:
            try:
                segments_raw = upload.read().decode("utf-8")
            except UnicodeDecodeError:
                errors.append("Could not read the uploaded file as UTF-8.")
                segments_raw = ""
        else:
            segments_raw = (form.get("segments_paste") or "").strip()

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
        "title": title,
        "language": language,
        "description": description,
        "eval_mode": eval_mode,
        "passing_score": passing_score,
        "time_limit_minutes": time_limit_minutes,
        "max_attempts": max_attempts,
        "retry_cooldown_minutes": retry_cooldown_minutes,
        "is_active": is_active,
        "criteria": [{"name": c["name"], "desc": c["guide"]} for c in criteria],
        "segments_paste": form_segments_paste,
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
