from flask import Blueprint, render_template, session

from services.auth_service import require_annotator, current_annotator
from services.qualification import can_annotate
from models import Campaign, Rating
from utils.constants import get_criteria_for

annotator_dashboard_bp = Blueprint(
    "annotator_dashboard",
    __name__,
    url_prefix="/annotator",
)


@annotator_dashboard_bp.route("/dashboard")
@require_annotator
def annotator_dashboard():
    ann = current_annotator()

    # Find all campaigns this annotator has ratings for
    campaign_ids = {
        r.campaign_id
        for r in Rating.query.filter_by(annotator_id=ann.id).all()
    }

    # Also include the campaign they most recently signed in from, even with no
    # ratings yet, so they have a way into it from the dashboard.
    last_cid = session.get("last_campaign_id")
    if last_cid:
        campaign_ids.add(last_cid)

    rows = []

    for cid in campaign_ids:
        c = Campaign.query.get(cid)
        if not c:
            continue

        ratings = Rating.query.filter_by(
            campaign_id=cid,
            annotator_id=ann.id,
        ).all()

        criteria = get_criteria_for(c)

        completed = sum(
            1
            for r in ratings
            if c.rating_is_complete(r, criteria)
        )

        last_update = max(
            (r.updated_at for r in ratings),
            default=None,
        )

        rows.append(
            {
                "campaign": c,
                "completed": completed,
                "total": c.num_segments,
                "last_update": last_update,
                "pct": int(completed / c.num_segments * 100)
                if c.num_segments
                else 0,
            }
        )

    rows.sort(
        key=lambda r: (
            r["campaign"].is_closed,
            -r["pct"],
            r["campaign"].name,
        )
    )

    return render_template(
        "annotator_dashboard.html",
        annotator=ann,
        rows=rows,
        qualified=can_annotate(ann),
    )
