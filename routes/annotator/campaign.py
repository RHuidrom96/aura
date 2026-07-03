from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from models import Campaign, Annotator, normalize_fluency, FLUENCY_LEVELS, normalize_age_group, AGE_GROUPS
from extensions import db
from utils.constants import SCRIPT_OPTIONS, get_criteria_for
from services.auth_service import (
    current_annotator,
    password_problems,
    send_password_reset_otp_for,
    verify_reset_otp,
    reset_password_for,
    EMAIL_RE,
)

annotator_campaign_bp = Blueprint(
    "annotator_campaign",
    __name__,
)


@annotator_campaign_bp.route("/campaign/<campaign_id>")
def annotator_landing_view(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

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
        campaign=c,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(c)
    )


@annotator_campaign_bp.route("/campaign/<campaign_id>/login")
def annotator_login_view(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

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
        campaign=c,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(c),
    )


@annotator_campaign_bp.route(
    "/campaign/<campaign_id>/forgot-password",
    methods=["GET", "POST"],
)
def annotator_forgot_password(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        send_password_reset_otp_for("annotator", email)

        flash(
            "If an account with that email exists, a verification code has been sent.",
            "success",
        )

        session["reset_email"] = email

        return redirect(
            url_for(
                "annotator_campaign.annotator_verify_otp",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "forget_password.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_campaign.annotator_forgot_password",
            campaign_id=campaign_id,
        ),
        back_url=url_for(
            "annotator_campaign.annotator_login_view",
            campaign_id=campaign_id,
        ),
    )


@annotator_campaign_bp.route("/campaign/<campaign_id>/verify-otp", methods=["GET", "POST"])
def annotator_verify_otp(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    email = session.get("reset_email")
    if not email:
        flash("Password reset session expired. Please start again.", "error")
        return redirect(
            url_for(
                "annotator_campaign.annotator_forgot_password",
                campaign_id=campaign_id,
            )
        )

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not verify_reset_otp("annotator", email, otp):
            flash("Invalid or expired verification code.", "error")
            return redirect(
                url_for(
                    "annotator_campaign.annotator_verify_otp",
                    campaign_id=campaign_id,
                )
            )

        session["reset_verified"] = True

        flash("Verification successful. Please choose a new password.", "success")

        return redirect(
            url_for(
                "annotator_campaign.annotator_reset_password",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "verify_otp.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_campaign.annotator_verify_otp",
            campaign_id=campaign_id,
        ),
        back_url=url_for(
            "annotator_campaign.annotator_forgot_password",
            campaign_id=campaign_id,
        ),
    )


@annotator_campaign_bp.route("/campaign/<campaign_id>/reset-password", methods=["GET", "POST"])
def annotator_reset_password(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    email = session.get("reset_email")

    if not email or not session.get("reset_verified"):
        flash("Please verify your identity first.", "error")
        return redirect(
            url_for(
                "annotator_campaign.annotator_forgot_password",
                campaign_id=campaign_id,
            )
        )

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        success, errors = reset_password_for("annotator", email, password, confirm)

        if not success:
            for error in errors:
                flash(error, "error")

            return redirect(
                url_for(
                    "annotator_campaign.annotator_reset_password",
                    campaign_id=campaign_id,
                )
            )

        session.pop("reset_email", None)
        session.pop("reset_verified", None)

        flash("Password has been reset successfully.", "success")

        return redirect(
            url_for(
                "annotator_campaign.annotator_login_view",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "reset_password.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_campaign.annotator_reset_password",
            campaign_id=campaign_id,
        ),
    )


@annotator_campaign_bp.route("/campaign/<campaign_id>/signup")
def annotator_signup_view(campaign_id):

    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

    return render_template(
        "annotator_signup.html",
        campaign=c,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(c),
        fluency_levels=FLUENCY_LEVELS,
        age_groups=AGE_GROUPS,
    )


@annotator_campaign_bp.route("/campaign/<campaign_id>/login", methods=["POST"])
def annotator_login_post(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(
            url_for(
                "annotator_campaign.annotator_login_view",
                campaign_id=campaign_id,
            )
        )

    ann = Annotator.query.filter_by(email=email).first()

    if not ann or not ann.check_password(password):
        flash("Incorrect email or password.", "error")
        return redirect(
            url_for(
                "annotator_campaign.annotator_login_view",
                campaign_id=campaign_id,
            )
        )

    session.permanent = True
    session["annotator_id"] = ann.id
    session["last_campaign_id"] = campaign_id

    return redirect(
        url_for(
            "annotator_rating.rate_view",
            campaign_id=campaign_id,
        )
    )


@annotator_campaign_bp.route("/campaign/<campaign_id>/register", methods=["POST"])
def annotator_register(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("password_confirm", "")
    native_lang = request.form.get("native_language", "").strip()
    expertise = (request.form.get("expertise", "") or "").strip().lower()
    source_fluency = normalize_fluency(request.form.get("source_fluency", ""))
    target_fluency = normalize_fluency(request.form.get("target_fluency", ""))
    location = request.form.get("location", "").strip()[:120]
    dialect = request.form.get("dialect", "").strip()[:120]
    age_group = normalize_age_group(request.form.get("age_group", ""))

    if expertise not in ("easy", "medium", "hard"):
        expertise = ""

    errors = []

    if not name:
        errors.append("Name is required.")

    if not email or not EMAIL_RE.match(email):
        errors.append("A valid email is required.")

    problems = password_problems(password)
    if problems:
        errors.append(
            "Password must contain " + ", ".join(problems) + "."
        )

    if password != confirm:
        errors.append("Passwords don't match.")

    if not request.form.get("consent"):
        errors.append("Please confirm the consent checkbox.")

    if c.difficulty_method != "none" and not expertise:
        errors.append("Please select your expertise level.")

    # For cross-lingual tasks (e.g. MT), fluency in both the source and target language is
    # required; for monolingual tasks these fields aren't shown and stay blank.
    if c.is_cross_lingual:
        if not source_fluency:
            errors.append(f"Please rate your fluency in {c.source_language or 'the source language'}.")
        if not target_fluency:
            errors.append(f"Please rate your fluency in {c.target_language or 'the target language'}.")

    if not errors and Annotator.query.filter_by(email=email).first():
        errors.append(
            "An account with that email already exists. Sign in instead."
        )

    if errors:
        for error in errors:
            flash(error, "error")

        return redirect(
            url_for(
                "annotator_campaign.annotator_login_view",
                campaign_id=campaign_id,
            )
        )

    ann = Annotator(
        name=name,
        email=email,
        native_language=native_lang,
        expertise=expertise,
        source_fluency=source_fluency,
        target_fluency=target_fluency,
        location=location,
        dialect=dialect,
        age_group=age_group,
    )

    ann.set_password(password)

    db.session.add(ann)
    db.session.commit()

    session.permanent = True
    session["annotator_id"] = ann.id
    session["last_campaign_id"] = campaign_id

    flash("Account created. Welcome!", "success")

    return redirect(
        url_for(
            "annotator_rating.rate_view",
            campaign_id=campaign_id,
        )
    )
