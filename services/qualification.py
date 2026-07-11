"""
Logic for annotator qualification.

"""

import json
from datetime import datetime, timedelta

from extensions import db
from models import (
    Annotator,
    QualificationAttempt,
    QualificationResponse,
    QualificationSegment,
    QualificationTest,
)


# ---------------------------------------------------------------------------
# Test lookup
# ---------------------------------------------------------------------------

def get_test_for_campaign(campaign):
    """
    Return the qualification test belonging to this specific campaign, or None
    if the campaign has no qualification configured (e.g. a campaign created
    before per-campaign qualification existed).
    """
    if campaign is None:
        return None
    return campaign.qualification_test


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def can_annotate(annotator, campaign):
    """
    Whether an annotator may participate in THIS campaign.

    Qualification is per-campaign: passing campaign A's qualification never
    qualifies an annotator for campaign B. A campaign with no qualification
    configured (or one whose qualification is turned off) has no gate.
    """
    test = get_test_for_campaign(campaign)

    if test is None or not test.is_active:
        return True

    return (
        QualificationAttempt.query
        .filter_by(
            annotator_id=annotator.id,
            test_id=test.id,
            passed=True,
        )
        .first() is not None
    )


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------

def start_attempt(annotator, test):
    """
    Return the current open attempt or create a new one.

    Does not itself enforce the retry policy (max attempts / cooldown) --
    callers must check retry_status() first when a prior submitted attempt
    exists. `version` records this as the Nth attempt for this
    (annotator, test) pair.
    """

    attempt = get_open_attempt(annotator, test)

    if attempt:
        return attempt

    attempt = QualificationAttempt(
        annotator=annotator,
        test=test,
        started_at=datetime.utcnow(),
        version=attempts_used(annotator, test) + 1,
    )

    db.session.add(attempt)
    db.session.commit()

    return attempt

def get_open_attempt(annotator, test):
    """
    Return the unfinished attempt for this annotator, if any.
    """

    return (
        QualificationAttempt.query
        .filter_by(
            annotator_id=annotator.id,
            test_id=test.id,
        )
        .filter(
            QualificationAttempt.submitted_at.is_(None)
        )
        .first()
    )

def latest_attempt(annotator):
    """
    Return the most recent submitted qualification attempt, across any test.
    """

    return (
        QualificationAttempt.query
        .filter_by(
            annotator_id=annotator.id,
        )
        .filter(
            QualificationAttempt.submitted_at.isnot(None)
        )
        .order_by(
            QualificationAttempt.submitted_at.desc()
        )
        .first()
    )


def latest_attempt_for_test(annotator, test):
    """
    Return the most recent submitted attempt this annotator has made at
    this specific test, if any.
    """

    return (
        QualificationAttempt.query
        .filter_by(
            annotator_id=annotator.id,
            test_id=test.id,
        )
        .filter(
            QualificationAttempt.submitted_at.isnot(None)
        )
        .order_by(
            QualificationAttempt.submitted_at.desc()
        )
        .first()
    )


def attempts_used(annotator, test):
    """
    Number of submitted (i.e. completed) attempts this annotator has made
    at this specific test. An in-progress (unsubmitted) attempt doesn't
    count -- it's returned by get_open_attempt() and resumed, not retried.
    """

    return (
        QualificationAttempt.query
        .filter_by(
            annotator_id=annotator.id,
            test_id=test.id,
        )
        .filter(
            QualificationAttempt.submitted_at.isnot(None)
        )
        .count()
    )


from datetime import datetime, timedelta

def retry_status(annotator, test):
    used = attempts_used(annotator, test)
    max_attempts = test.effective_max_attempts
    cooldown_minutes = test.effective_cooldown_minutes

    base = {
        "attempts_used": used,
        "max_attempts": max_attempts,
        "cooldown_minutes": cooldown_minutes,
    }

    if used >= max_attempts:
        return {
            **base,
            "allowed": False,
            "reason": "max_attempts",
            "retry_at": None,
        }

    last = latest_attempt_for_test(annotator, test)
    if last and last.submitted_at:
        retry_at = last.submitted_at + timedelta(minutes=cooldown_minutes)
        if datetime.utcnow() < retry_at:
            return {
                **base,
                "allowed": False,
                "reason": "cooldown",
                "retry_at": retry_at,
            }

    return {
        **base,
        "allowed": True,
        "reason": None,
        "retry_at": None,
    }

# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

def submit_attempt(attempt, answers):
    """
    Save all responses for a qualification attempt.

    Parameters
    ----------
    attempt : QualificationAttempt

    answers : dict

        {
            segment_id: {
                ...
            }
        }

    The response dictionary should match the structure expected by the
    qualification test (scores, preference, spans, edited_text, etc.).
    """

    existing = {
        r.segment_id: r
        for r in attempt.responses
    }

    segments = (
        attempt.test.segments
        .order_by(QualificationSegment.position)
        .all()
    )

    for segment in segments:

        response = answers.get(segment.id)

        if response is None:
            continue

        obj = existing.get(segment.id)

        if obj is None:
            obj = QualificationResponse(
                attempt=attempt,
                segment=segment,
            )
            db.session.add(obj)

        obj.response_json = json.dumps(response)

    db.session.commit()

    return attempt


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def finish_attempt(attempt):
    """
    Finalize a qualification attempt.

    Grades every response, updates qualification status,
    and commits everything in a single transaction.
    """

    attempt.submitted_at = datetime.utcnow()

    grade_attempt(attempt)

    qualify_annotator(attempt)

    db.session.commit()

    return attempt

# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade_attempt(attempt):
    """
    Grade a qualification attempt.

    Implemented for all four evaluation modes:

        • Likert     — similarity score based on criterion ratings
        • Pairwise   — exact match of the chosen preference
        • Span-only  — exact match of the spans dict
                        (no client UI collects spans yet; see test.html)
        • Post-edit  — exact (whitespace-trimmed) match of the edited text
    """

    test = attempt.test

    correct_answers = 0
    total_score = 0.0
    total = 0

    responses = {
        r.segment_id: r
        for r in attempt.responses
    }

    segments = (
        test.segments
        .order_by(QualificationSegment.position)
        .all()
    )

    MIN_LIKERT_SCORE = 1
    MAX_LIKERT_SCORE = 5
    MAX_DIFFERENCE = MAX_LIKERT_SCORE - MIN_LIKERT_SCORE

    for segment in segments:

        total += 1

        response = responses.get(segment.id)

        if response is None:
            continue

        gold = segment.gold
        pred = response.response

        ok = False

        mode = test.eval_mode or "likert"

        # -------------------------------------------------------
        # Pairwise
        # -------------------------------------------------------

        if mode == "pairwise":

            ok = (
                pred.get("preference")
                == gold.get("preference")
            )

            response.score = 100.0 if ok else 0.0
            response.is_correct = ok

        # -------------------------------------------------------
        # Likert
        # -------------------------------------------------------

        elif mode == "likert":

            gold_scores = gold.get("scores") or {}
            pred_scores = pred.get("scores") or {}

            criterion_scores = []

            for criterion, gold_value in gold_scores.items():

                pred_value = pred_scores.get(criterion)

                if pred_value is None:
                    criterion_scores.append(0.0)
                    continue

                difference = abs(pred_value - gold_value)

                score = (
                    1 - (difference / MAX_DIFFERENCE)
                ) * 100.0

                criterion_scores.append(score)

            if criterion_scores:
                response.score = (
                    sum(criterion_scores)
                    / len(criterion_scores)
                )
            else:
                response.score = 0.0

            # A response is considered fully correct only if every
            # criterion exactly matches the gold annotation.
            response.is_correct = (
                response.score == 100.0
            )

        # -------------------------------------------------------
        # Span-only
        # -------------------------------------------------------

        elif mode == "span_only":

            ok = (
                (pred.get("spans") or {})
                == (gold.get("spans") or {})
            )

            response.score = 100.0 if ok else 0.0
            response.is_correct = ok

        # -------------------------------------------------------
        # Post-edit
        # -------------------------------------------------------

        elif mode == "post_edit":

            ok = (
                (pred.get("edited_text") or "").strip()
                == (gold.get("edited_text") or "").strip()
            )

            response.score = 100.0 if ok else 0.0
            response.is_correct = ok

        else:

            response.score = 0.0
            response.is_correct = False

        total_score += response.score

        if response.is_correct:
            correct_answers += 1

    attempt.correct_answers = correct_answers
    attempt.total_questions = total

    if total:
        attempt.score = total_score / total
    else:
        attempt.score = 0.0

    attempt.passed = (
        attempt.score >= test.passing_score
    )

    return attempt.score


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------

def qualify_annotator(attempt):
    """
    Update annotator qualification state.
    """

    annotator = attempt.annotator

    if attempt.passed:

        annotator.qualification_status = "qualified"

        annotator.qualified_at = datetime.utcnow()

    else:

        annotator.qualification_status = "failed"

    return annotator

