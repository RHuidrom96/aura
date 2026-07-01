from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from models import Admin
from services.auth_service import (
    admin_login,
    admin_signup_enabled,
    login_admin,
    logout_admin,
    require_admin,
    send_password_reset_otp_for,
    verify_reset_otp,
    reset_password_for,
    update_password_for,
    start_admin_signup,
    verify_admin_signup_otp,
)

admin_auth_bp = Blueprint(
    "admin_auth",
    __name__,
    url_prefix="/admin",
)


@admin_auth_bp.route("/login", methods=["GET", "POST"])
def admin_login_view():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if admin_login(email, password):
            login_admin(email)

            return redirect(
                request.args.get("next") or url_for("admin_campaign.admin_dashboard")
            )

        flash("Invalid admin credentials.", "error")

    return render_template(
        "admin_login.html",
        signup_enabled=admin_signup_enabled()
    )


@admin_auth_bp.route("/signup", methods=["GET", "POST"])
def admin_signup():
    if not admin_signup_enabled():
        flash("Admin sign-up is currently disabled.", "error")
        return redirect(url_for("admin_auth.admin_login_view"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")
        invite_code = request.form.get("invite_code", "")

        account, errors = start_admin_signup(email, password, confirm, invite_code)

        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("admin_auth.admin_signup"))

        session["signup_email"] = account.email

        flash("A verification code has been sent to your email.", "success")
        return redirect(url_for("admin_auth.admin_signup_verify"))

    return render_template(
        "admin_signup.html",
        action_url=url_for("admin_auth.admin_signup"),
    )


@admin_auth_bp.route("/signup/verify-otp", methods=["GET", "POST"])
def admin_signup_verify():
    email = session.get("signup_email")

    if not email:
        flash("Session expired. Please sign up again.", "error")
        return redirect(url_for("admin_auth.admin_signup"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not verify_admin_signup_otp(email, otp):
            flash("Invalid or expired code.", "error")
            return redirect(url_for("admin_auth.admin_signup_verify"))

        session.pop("signup_email", None)

        login_admin(email)

        flash("Account verified. Welcome!", "success")
        return redirect(url_for("admin_campaign.admin_dashboard"))

    return render_template(
        "verify_otp.html",
        action_url=url_for("admin_auth.admin_signup_verify"),
        back_url=url_for("admin_auth.admin_signup"),
    )


@admin_auth_bp.route("/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        send_password_reset_otp_for("admin", email)

        session["reset_email"] = email

        flash(
            "If an account exists, a verification code has been sent.",
            "success"
        )

        return redirect(url_for("admin_auth.admin_verify_otp"))

    return render_template(
        "forget_password.html",
        action_url=url_for("admin_auth.admin_forgot_password"),
        back_url=url_for("admin_auth.admin_login_view"),
    )


@admin_auth_bp.route("/verify-otp", methods=["GET", "POST"])
def admin_verify_otp():
    email = session.get("reset_email")

    if not email:
        flash("Session expired. Start again.", "error")
        return redirect(url_for("admin_auth.admin_forgot_password"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not verify_reset_otp("admin", email, otp):
            flash("Invalid or expired code.", "error")
            return redirect(url_for("admin_auth.admin_verify_otp"))

        session["reset_verified"] = True

        return redirect(url_for("admin_auth.admin_reset_password"))

    return render_template(
        "verify_otp.html",
        action_url=url_for("admin_auth.admin_verify_otp"),
        back_url=url_for("admin_auth.admin_forgot_password"),
    )


@admin_auth_bp.route("/reset-password", methods=["GET", "POST"])
def admin_reset_password():
    email = session.get("reset_email")

    if not email or not session.get("reset_verified"):
        flash("Verify identity first.", "error")
        return redirect(url_for("admin_auth.admin_forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        success, errors = reset_password_for("admin", email, password, confirm)

        if not success:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("admin_auth.admin_reset_password"))

        session.pop("reset_email", None)
        session.pop("reset_verified", None)

        flash("Password reset successful.", "success")
        return redirect(url_for("admin_auth.admin_login_view"))

    return render_template("reset_password.html")


@admin_auth_bp.route("/update-password", methods=["GET", "POST"])
@require_admin
def admin_update_password():
    admin = Admin.query.filter_by(email=session.get("admin_email")).first_or_404()

    if request.method == "POST":
        success, errors = update_password_for(
            admin,
            request.form.get("current_password"),
            request.form.get("new_password"),
            request.form.get("confirm_password"),
        )

        if not success:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("admin_auth.admin_update_password"))

        flash("Password updated successfully.", "success")
        return redirect(url_for("admin_campaign.admin_dashboard"))

    return render_template(
        "update_password.html",
        role="admin",
        action_url=url_for("admin_auth.admin_update_password")
    )


@admin_auth_bp.route("/logout", methods=["POST"])
def admin_logout():
    logout_admin()
    flash("Signed out.", "info")
    return redirect(url_for("public.landing"))
