from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

from models import (
    Campaign,
    Annotator,
    normalize_fluency,
    normalize_age_group,
)

from extensions import db

from services.auth_service import (
    require_annotator,
    current_annotator,
    login_annotator,
    logout_annotator,
    get_resume_campaign,
    update_password_for,
    start_annotator_signup,
    verify_annotator_signup_otp,
    password_problems,
    send_password_reset_otp_for,
    verify_reset_otp,
    reset_password_for,
    EMAIL_RE,
)

annotator_auth_bp = Blueprint(
    "annotator_auth",
    __name__,
    url_prefix="/annotator",
)


@annotator_auth_bp.route("/campaign/<campaign_id>/login", methods=["POST"])
def annotator_login_post(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    if campaign.is_closed:
        return render_template(
            "campaign_closed.html",
            campaign=campaign,
        )

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

    annotator = Annotator.query.filter_by(email=email).first()

    if not annotator or not annotator.check_password(password):
        flash("Incorrect email or password.", "error")

        return redirect(
            url_for(
                "annotator_campaign.annotator_login_view",
                campaign_id=campaign_id,
            )
        )

    if not annotator.email_verified:
        flash(
            "Please verify your email before signing in.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_campaign.annotator_login_view",
                campaign_id=campaign_id,
            )
        )

    login_annotator(annotator, campaign_id)
    session.permanent = True

    return redirect(
        url_for(
            "annotator_rating.rate_view",
            campaign_id=campaign_id,
        )
    )

@annotator_auth_bp.route("/campaign/<campaign_id>/register", methods=["POST"])
def annotator_register(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    if campaign.is_closed:
        return render_template(
            "campaign_closed.html",
            campaign=campaign,
        )

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("password_confirm", "")
    native_lang = request.form.get("native_language", "").strip()
    expertise = (request.form.get("expertise", "") or "").strip().lower()
    source_fluency = normalize_fluency(
        request.form.get("source_fluency", "")
    )
    target_fluency = normalize_fluency(
        request.form.get("target_fluency", "")
    )
    location = request.form.get("location", "").strip()[:120]
    dialect = request.form.get("dialect", "").strip()[:120]
    age_group = normalize_age_group(
        request.form.get("age_group", "")
    )

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

    if campaign.difficulty_method != "none" and not expertise:
        errors.append("Please select your expertise level.")

    if campaign.is_cross_lingual:
        if not source_fluency:
            errors.append(
                f"Please rate your fluency in "
                f"{campaign.source_language or 'the source language'}."
            )

        if not target_fluency:
            errors.append(
                f"Please rate your fluency in "
                f"{campaign.target_language or 'the target language'}."
            )

    if not native_lang:
        errors.append("Native language is required.")

    if not location:
        errors.append("Location is required.")

    if not dialect:
        errors.append("Dialect / variety is required.")

    if not age_group:
        errors.append("Please select your age group.")

    # Stop here if any validation errors occurred
    if errors:
        for error in errors:
            flash(error, "danger")

        return redirect(
            url_for(
                "annotator_auth.annotator_login",
                campaign_id=campaign_id,
            )
        )

    existing = Annotator.query.filter_by(email=email).first()

    if existing:
        if existing.email_verified:
            errors.append(
                "An account with that email already exists. Sign in instead."
            )
        else:
            # Update the existing unverified account
            existing.name = name
            existing.native_language = native_lang
            existing.expertise = expertise
            existing.source_fluency = source_fluency
            existing.target_fluency = target_fluency
            existing.location = location
            existing.dialect = dialect
            existing.age_group = age_group
            existing.set_password(password)

            db.session.commit()

            # Generate and send a new OTP
            start_annotator_signup(existing)

            session["signup_email"] = existing.email

            flash("We resent your verification code.", "success")
            return redirect(
                url_for(
                    "annotator_auth.annotator_signup_verify",
                    campaign_id=campaign_id,
                )
            )
    if errors:
        for error in errors:
            flash(error, "danger")

        return redirect(
            url_for(
                "annotator_auth.annotator_login",
                campaign_id=campaign_id,
            )
        )

    annotator = Annotator(
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

    annotator.set_password(password)

    db.session.add(annotator)
    db.session.commit()

    start_annotator_signup(annotator)

    session["signup_email"] = annotator.email

    flash(
        "A verification code has been sent to your email.",
        "success",
    )

    return redirect(
        url_for(
            "annotator_auth.annotator_signup_verify",
            campaign_id=campaign_id,
        )
    )

@annotator_auth_bp.route(
    "/campaign/<campaign_id>/signup/verify-otp",
    methods=["GET", "POST"],
)
def annotator_signup_verify(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    email = session.get("signup_email")

    if not email:
        flash(
            "Session expired. Please sign up again.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_campaign.annotator_signup_view",
                campaign_id=campaign_id,
            )
        )

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not verify_annotator_signup_otp(email, otp):
            flash("Invalid or expired code.", "error")

            return redirect(
                url_for(
                    "annotator_auth.annotator_signup_verify",
                    campaign_id=campaign_id,
                )
            )

        session.pop("signup_email", None)

        flash(
            "Account verified. Please sign in.",
            "success",
        )

        return redirect(
            url_for(
                "annotator_campaign.annotator_login_view",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "verify_otp.html",
        action_url=url_for(
            "annotator_auth.annotator_signup_verify",
            campaign_id=campaign_id,
        ),
        back_url=url_for(
            "annotator_campaign.annotator_signup_view",
            campaign_id=campaign_id,
        ),
        campaign=campaign,
    )

@annotator_auth_bp.route(
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
                "annotator_auth.annotator_verify_otp",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "forget_password.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_auth.annotator_forgot_password",
            campaign_id=campaign_id,
        ),
        back_url=url_for(
            "annotator_campaign.annotator_login_view",
            campaign_id=campaign_id,
        ),
    )


@annotator_auth_bp.route(
    "/campaign/<campaign_id>/verify-otp",
    methods=["GET", "POST"],
)
def annotator_verify_otp(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    email = session.get("reset_email")

    if not email:
        flash(
            "Password reset session expired. Please start again.",
            "error",
        )

        return redirect(
            url_for(
                "annotator_auth.annotator_forgot_password",
                campaign_id=campaign_id,
            )
        )

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not verify_reset_otp("annotator", email, otp):
            flash(
                "Invalid or expired verification code.",
                "error",
            )

            return redirect(
                url_for(
                    "annotator_auth.annotator_verify_otp",
                    campaign_id=campaign_id,
                )
            )

        session["reset_verified"] = True

        flash(
            "Verification successful. Please choose a new password.",
            "success",
        )

        return redirect(
            url_for(
                "annotator_auth.annotator_reset_password",
                campaign_id=campaign_id,
            )
        )

    return render_template(
        "verify_otp.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_auth.annotator_verify_otp",
            campaign_id=campaign_id,
        ),
        back_url=url_for(
            "annotator_auth.annotator_forgot_password",
            campaign_id=campaign_id,
        ),
    )


@annotator_auth_bp.route(
    "/campaign/<campaign_id>/reset-password",
    methods=["GET", "POST"],
)
def annotator_reset_password(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    email = session.get("reset_email")

    if not email or not session.get("reset_verified"):
        flash("Please verify your identity first.", "error")

        return redirect(
            url_for(
                "annotator_auth.annotator_forgot_password",
                campaign_id=campaign_id,
            )
        )

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        success, errors = reset_password_for(
            "annotator",
            email,
            password,
            confirm,
        )

        if not success:
            for error in errors:
                flash(error, "error")

            return redirect(
                url_for(
                    "annotator_auth.annotator_reset_password",
                    campaign_id=campaign_id,
                )
            )

        session.pop("reset_email", None)
        session.pop("reset_verified", None)

        flash(
            "Password has been reset successfully.",
            "success",
        )

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
            "annotator_auth.annotator_reset_password",
            campaign_id=campaign_id,
        ),
    )

@annotator_auth_bp.route("/logout", methods=["POST"])
def annotator_logout():
    campaign_id = session.get("last_campaign_id")  # ✅ define it first

    session.clear()

    if campaign_id:
        return redirect(url_for(
            "annotator_campaign.annotator_login_view",
            campaign_id=campaign_id
        ))

    return redirect(url_for("public.landing"))