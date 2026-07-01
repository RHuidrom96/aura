from flask import Blueprint, render_template, request, redirect, url_for, flash

from services.auth_service import (
    require_annotator,
    current_annotator,
    update_password_for,
    logout_annotator,
    get_resume_campaign,
)

annotator_auth_bp = Blueprint(
    "annotator_auth",
    __name__,
    url_prefix="/annotator",
)


@annotator_auth_bp.route("/update-password", methods=["GET", "POST"])
@require_annotator
def annotator_update_password():
    annotator = current_annotator()
    if request.method == "POST":

        success, errors = update_password_for(
            annotator,
            request.form.get("current_password"),
            request.form.get("new_password"),
            request.form.get("confirm_password"),
        )

        if not success:
            for error in errors:
                flash(error, "error")
            return redirect(
                url_for("annotator_auth.annotator_update_password")
            )

        flash("Password updated successfully.", "success")
        return redirect(
            url_for("annotator_dashboard.annotator_dashboard")
        )
    return render_template(
        "update_password.html",
        role="annotator",
        action_url=url_for("annotator_auth.annotator_update_password")
    )


@annotator_auth_bp.route("/logout", methods=["POST"])
@require_annotator
def annotator_logout():

    last_campaign_id = logout_annotator()

    flash("Signed out.", "info")

    if last_campaign_id:

        campaign = get_resume_campaign(last_campaign_id)

        if campaign and not campaign.is_closed:
            return redirect(
                url_for(
                    "annotator_campaign.annotator_login_view",
                    campaign_id=last_campaign_id,
                )
            )

    return redirect(url_for("public.landing"))
