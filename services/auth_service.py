import os
import hmac
import re
import secrets
from functools import wraps

from flask import session, redirect, url_for, flash, request, jsonify

from models import Admin, Annotator, Campaign
from extensions import db
from mailer import send_password_reset_otp, send_otp

ADMIN_EMAIL = os.environ.get("MT_EVAL_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("MT_EVAL_ADMIN_PASSWORD", "change-me")

# Maps a role name to its account model. Both Admin and Annotator implement the
# same password/OTP interface (set_password, check_password, set_otp, check_otp,
# clear_otp), which is what lets the helpers below be shared across both roles
# instead of being duplicated per-role.
_ROLE_MODELS = {"admin": Admin, "annotator": Annotator}


def _model_for(role):
    try:
        return _ROLE_MODELS[role]
    except KeyError:
        raise ValueError(f"Unknown role: {role!r}")


def _valid_email(e):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e or ""))


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def admin_login(email, password):
    """True if credentials match the env-var admin OR a verified self-registered admin."""
    email = (email or "").strip().lower()
    if email == ADMIN_EMAIL.strip().lower() and password == ADMIN_PASSWORD:
        return True
    admin = Admin.query.filter_by(email=email, is_verified=True).first()
    return bool(admin and admin.check_password(password))


def current_admin():
    return session.get("admin_email") if session.get("is_admin") else None


def current_annotator():
    aid = session.get("annotator_id")
    if not aid:
        return None
    return Annotator.query.get(aid)


def _wants_json():
    """True for XHR/fetch/API requests that should get a JSON error, not a redirect."""
    if "/api/" in request.path:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] >= accept["text/html"] and accept["application/json"] > 0


def require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            if _wants_json():
                return jsonify({"ok": False, "error": "auth_required", "role": "admin"}), 401
            flash("Please sign in as admin.", "info")
            return redirect(url_for("admin_auth.admin_login_view"))
        return view(*args, **kwargs)
    return wrapper


def require_annotator(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("annotator_id"):
            if _wants_json():
                return jsonify({"ok": False, "error": "auth_required", "role": "annotator"}), 401
            # Find a campaign to send them to without ever building an empty
            # "/campaign/" URL (which would 404). Prefer the route's own
            # campaign_id, then the last one they worked on, else the landing page.
            campaign_id = (kwargs.get("campaign_id")
                           or (request.view_args or {}).get("campaign_id")
                           or session.get("last_campaign_id"))
            flash("Please sign in to continue.", "info")
            if campaign_id:
                return redirect(url_for("annotator_campaign.annotator_login_view", campaign_id=campaign_id))
            return redirect(url_for("public.landing"))
        return view(*args, **kwargs)
    return wrapper


def password_problems(pw):
    """Return unmet password requirements."""
    probs = []
    pw = pw or ""

    if len(pw) < 8:
        probs.append("at least 8 characters")
    if not re.search(r"[a-z]", pw):
        probs.append("a lowercase letter")
    if not re.search(r"[A-Z]", pw):
        probs.append("an uppercase letter")
    if not re.search(r"[0-9]", pw):
        probs.append("a number")
    if not re.search(r"[^A-Za-z0-9]", pw):
        probs.append("a symbol")

    return probs


def generate_otp():
    return f"{secrets.randbelow(1000000):06d}"


def _csv_env(name):
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def admin_signup_enabled():
    return os.environ.get("MT_EVAL_ADMIN_SIGNUP_ENABLED", "1").lower() not in (
        "0", "false", "no", "off"
    )


def admin_invite_codes():
    return _csv_env("MT_EVAL_ADMIN_INVITE_CODES")


def admin_allowed_domains():
    return [d.lower().lstrip("@") for d in _csv_env("MT_EVAL_ADMIN_EMAIL_DOMAINS")]


def _invite_ok(code):
    codes = admin_invite_codes()
    if not codes:
        return True
    code = (code or "").strip()
    return any(hmac.compare_digest(code, c) for c in codes)


def _domain_ok(email):
    domains = admin_allowed_domains()
    if not domains:
        return True
    dom = (email.rsplit("@", 1)[-1] if "@" in email else "").lower()
    return dom in domains


def login_admin(email):
    session.clear()
    session["is_admin"] = True
    session["admin_email"] = email


def logout_admin():
    session.pop("is_admin", None)
    session.pop("admin_email", None)


# ----- Annotator -------
def login_annotator(annotator):
    session.clear()
    session["annotator_id"] = annotator.id


def logout_annotator():
    last_campaign_id = session.get("last_campaign_id")

    session.pop("annotator_id", None)
    session.pop("last_campaign_id", None)

    return last_campaign_id


def get_resume_campaign(last_campaign_id):
    campaign = db.session.get(Campaign, last_campaign_id)

    if campaign and not campaign.is_closed:
        return campaign

    return None


# ============================================================================
# Shared password-reset / OTP / password-update helpers.
#
# Admin and Annotator both implement set_password/check_password/set_otp/
# check_otp/clear_otp, so a single generic implementation (parameterized by
# `role`, or by the account instance itself) covers both roles instead of
# each route module rolling its own copy.
# ============================================================================

def start_admin_signup(email, password, confirm, invite_code):
    """Create (or refresh) an unverified Admin account and email a verification OTP.

    Returns (account_or_None, errors). Mirrors the reset-password OTP helpers above,
    but for first-time signup rather than a password reset.
    """
    email = (email or "").strip().lower()
    errors = []

    if not EMAIL_RE.match(email):
        errors.append("A valid email is required.")
    if not _domain_ok(email):
        errors.append("Sign-ups are restricted to specific email domains.")
    if not _invite_ok(invite_code):
        errors.append("Invalid or missing invite code.")

    probs = password_problems(password)
    if probs:
        errors.append("Password must contain " + ", ".join(probs) + ".")
    if password != confirm:
        errors.append("Passwords do not match.")

    if errors:
        return None, errors

    existing = Admin.query.filter_by(email=email).first()
    if existing and existing.is_verified:
        return None, ["An admin account with that email already exists. Sign in instead."]

    account = existing or Admin(email=email)
    account.set_password(password)

    otp = generate_otp()
    account.set_otp(otp)

    if not existing:
        db.session.add(account)
    db.session.commit()

    send_otp(email, otp)

    return account, []


def verify_admin_signup_otp(email, otp):
    """True and marks the account verified if `otp` is valid; False otherwise."""
    account = Admin.query.filter_by(email=email).first()
    if not account or not account.check_otp(otp):
        return False
    account.is_verified = True
    account.clear_otp()
    db.session.commit()
    return True


def send_password_reset_otp_for(role, email):
    """Look up the account by email/role, generate + store an OTP, and email it.

    Silent (no-op) if the account doesn't exist, so callers can give a uniform
    "if an account exists..." message without leaking which emails are registered.
    """
    model = _model_for(role)
    account = model.query.filter_by(email=email).first()
    if account:
        otp = generate_otp()
        account.set_otp(otp)
        db.session.commit()
        send_password_reset_otp(email, otp)
    return account


def verify_reset_otp(role, email, otp):
    """True and clears the OTP if `otp` is valid for the account; False otherwise."""
    model = _model_for(role)
    account = model.query.filter_by(email=email).first()
    if not account or not account.check_otp(otp):
        return False
    account.clear_otp()
    db.session.commit()
    return True


def reset_password_for(role, email, password, confirm):
    """Set a new password after OTP verification. Returns (success, errors)."""
    model = _model_for(role)
    account = model.query.filter_by(email=email).first()
    if not account:
        return False, ["Account not found."]

    errors = []
    probs = password_problems(password)
    if probs:
        errors.append("Password must contain " + ", ".join(probs) + ".")
    if password != confirm:
        errors.append("Passwords do not match.")
    if errors:
        return False, errors

    account.set_password(password)
    account.clear_otp()
    db.session.commit()
    return True, []


def update_password_for(account, current_password, new_password, confirm_password):
    """Change password for a logged-in account, verifying the current password.

    `account` is an Admin or Annotator instance. Returns (success, errors).
    """
    errors = []

    if not account.check_password(current_password):
        errors.append("Current password is incorrect.")

    probs = password_problems(new_password)
    if probs:
        errors.append("New password must contain " + ", ".join(probs) + ".")

    if new_password != confirm_password:
        errors.append("New passwords don't match.")

    if errors:
        return False, errors

    account.set_password(new_password)
    db.session.commit()
    return True, []
