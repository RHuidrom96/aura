import json

from flask import (
    Blueprint,
    render_template,
    redirect,
    request,
    url_for,
    flash,
)

from services.auth_service import (
    require_annotator,
    current_annotator,
)

from services.qualification import (
    get_active_test,
    can_annotate,
    start_attempt,
    latest_attempt,
    latest_attempt_for_test,
    retry_status,
    get_open_attempt,
    submit_attempt,
    finish_attempt,
)

from models import QualificationSegment


annotator_qualification_bp = Blueprint(
    "annotator_qualification",
    __name__,
)


@annotator_qualification_bp.route("/qualification")
@require_annotator
def qualification():

    annotator = current_annotator()

    if can_annotate(annotator):

        flash(
            "You have already passed the qualification test.",
            "info",
        )

        return redirect(
            url_for(
                "annotator_dashboard.annotator_dashboard"
            )
        )

    test = get_active_test()

    if test is None:

        flash(
            "No qualification test is currently available.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_dashboard.annotator_dashboard"
            )
        )

    attempt = get_open_attempt(
        annotator,
        test,
    )

    if attempt:

        segments = (
            test.segments
            .order_by(
                QualificationSegment.position
            )
            .all()
        )

        return render_template(
            "qualification/test.html",
            page_role="annotator",
            annotator=annotator,
            test=test,
            attempt=attempt,
            segments=segments,
        )

    previous = latest_attempt_for_test(
        annotator,
        test,
    )

    retry = None

    if previous:

        retry = retry_status(annotator, test)

        if not retry["allowed"]:

            return redirect(
                url_for(
                    "annotator_qualification.qualification_result"
                )
            )

    return render_template(
        "qualification/start.html",
        page_role="annotator",
        annotator=annotator,
        test=test,
        retry=retry,
    )


@annotator_qualification_bp.route(
    "/qualification/start",
    methods=["POST"],
)
@require_annotator
def start_qualification():

    annotator = current_annotator()

    if can_annotate(annotator):

        flash(
            "You have already passed the qualification test.",
            "info",
        )

        return redirect(
            url_for(
                "annotator_dashboard.annotator_dashboard"
            )
        )

    test = get_active_test()

    if test is None:

        flash(
            "No qualification test is currently available.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_dashboard.annotator_dashboard"
            )
        )

    previous = latest_attempt_for_test(
        annotator,
        test,
    )

    if previous and get_open_attempt(annotator, test) is None:

        retry = retry_status(annotator, test)

        if not retry["allowed"]:

            if retry["reason"] == "max_attempts":
                flash(
                    "You have used all available attempts for this "
                    "qualification test.",
                    "error",
                )
            else:
                flash(
                    "You can retake this qualification test after the "
                    "cooldown period has passed.",
                    "error",
                )

            return redirect(
                url_for(
                    "annotator_qualification.qualification_result"
                )
            )

    attempt = start_attempt(
        annotator,
        test,
    )

    segments = (
        test.segments
        .order_by(
            QualificationSegment.position
        )
        .all()
    )

    return render_template(
        "qualification/test.html",
        page_role="annotator",
        annotator=annotator,
        test=test,
        attempt=attempt,
        segments=segments,
    )


@annotator_qualification_bp.route(
    "/qualification/submit",
    methods=["POST"],
)
@require_annotator
def submit_qualification():

    annotator = current_annotator()

    if can_annotate(annotator):

        flash(
            "You have already passed the qualification test.",
            "info",
        )

        return redirect(
            url_for(
                "annotator_dashboard.annotator_dashboard"
            )
        )

    test = get_active_test()

    if test is None:

        flash(
            "No qualification test is currently available.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_dashboard.annotator_dashboard"
            )
        )

    attempt = get_open_attempt(
        annotator,
        test,
    )

    if attempt is None:

        flash(
            "Your qualification attempt could not be found. Please start again.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_qualification.qualification"
            )
        )

    segments = (
        test.segments
        .order_by(
            QualificationSegment.position
        )
        .all()
    )

    mode = test.mode

    answers = {}

    for segment in segments:

        if mode == "likert":

            scores = {}

            for criterion in test.criteria:

                field = "score_{}_{}".format(
                    segment.id,
                    criterion.get("id"),
                )

                raw = request.form.get(field)

                if raw is None:
                    continue

                try:
                    scores[criterion.get("id")] = int(raw)
                except (TypeError, ValueError):
                    continue

            if scores:
                answers[segment.id] = {"scores": scores}

        elif mode == "pairwise":

            field = "preference_{}".format(segment.id)

            raw = request.form.get(field)

            if raw:
                answers[segment.id] = {"preference": raw}

        elif mode == "post_edit":

            field = "edited_text_{}".format(segment.id)

            raw = (request.form.get(field) or "").strip()

            if raw:
                answers[segment.id] = {"edited_text": raw}

        elif mode == "span_only":

            field = "spans_{}".format(segment.id)

            raw = request.form.get(field, "{}")

            try:
                spans = json.loads(raw)
            except (TypeError, ValueError):
                spans = {}

            if not isinstance(spans, dict):
                spans = {}

            # Keep only well-formed, non-empty per-criterion span lists.
            # An empty dict is itself a valid answer ("no errors found").
            clean = {}

            for crit_id, ranges in spans.items():
                if not isinstance(ranges, list):
                    continue
                pairs = []
                for item in ranges:
                    if (isinstance(item, list) and len(item) == 2
                            and isinstance(item[0], int) and isinstance(item[1], int)
                            and 0 <= item[0] < item[1]):
                        pairs.append([item[0], item[1]])
                if pairs:
                    pairs.sort()
                    clean[crit_id] = pairs

            answers[segment.id] = {"spans": clean}

    submit_attempt(attempt, answers)

    finish_attempt(attempt)

    return redirect(
        url_for(
            "annotator_qualification.qualification_result"
        )
    )


@annotator_qualification_bp.route("/qualification/result")
@require_annotator
def qualification_result():

    annotator = current_annotator()

    attempt = latest_attempt(annotator)

    if attempt is None:

        flash(
            "No qualification attempt found.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_qualification.qualification"
            )
        )

    retry = None

    if not attempt.passed:

        active_test = get_active_test()

        if active_test is not None:
            retry = retry_status(annotator, active_test)

    return render_template(
        "qualification/result.html",
        page_role="annotator",
        annotator=annotator,
        attempt=attempt,
        retry=retry,
    )