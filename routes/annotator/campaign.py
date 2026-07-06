from flask import Blueprint, render_template, redirect, url_for, session

from models import Campaign, FLUENCY_LEVELS, AGE_GROUPS
from utils.constants import SCRIPT_OPTIONS, get_criteria_for
from services.auth_service import current_annotator

from flask import request, flash
from models import Annotator
from services.auth_service import login_annotator

annotator_campaign_bp = Blueprint(
    "annotator_campaign",
    __name__,
)


@annotator_campaign_bp.route("/campaign/<campaign_id>")
def annotator_landing_view(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    if campaign.is_closed:
        return render_template(
            "campaign_closed.html",
            campaign=campaign,
        )

    if current_annotator():
        session["last_campaign_id"] = campaign_id

        return redirect(
            url_for(
                "annotator_rating.rate_view",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "annotator_landing.html",
        campaign=campaign,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(campaign),
    )

@annotator_campaign_bp.route(
    "/campaign/<campaign_id>/login",
    methods=["GET", "POST"]
)
def annotator_login_view(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    if campaign.is_closed:
        return render_template(
            "campaign_closed.html",
            campaign=campaign,
        )

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        annotator = Annotator.query.filter_by(email=email).first()

        if annotator and annotator.check_password(password):

            login_annotator(annotator, campaign_id)

            return redirect(
                url_for(
                    "annotator_rating.rate_view",
                    campaign_id=campaign_id,
                )
            )

        flash("Invalid credentials")

    if current_annotator():
        session["last_campaign_id"] = campaign_id
        return redirect(
            url_for(
                "annotator_rating.rate_view",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "annotator_login.html",
        campaign=campaign,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(campaign),
    )


@annotator_campaign_bp.route("/campaign/<campaign_id>/signup")
def annotator_signup_view(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    if campaign.is_closed:
        return render_template(
            "campaign_closed.html",
            campaign=campaign,
        )

    return render_template(
        "annotator_signup.html",
        campaign=campaign,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(campaign),
        fluency_levels=FLUENCY_LEVELS,
        age_groups=AGE_GROUPS,
    )