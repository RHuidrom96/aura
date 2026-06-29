"""Session-based auth helpers.

Admin: single account, credentials in env vars.
Annotator: row in the Annotators table.
Sessions: Flask's built-in secure cookies.
"""

import os
from functools import wraps

from flask import session, redirect, url_for, flash, request, jsonify

from models import Annotator, Admin

ADMIN_EMAIL = os.environ.get("MT_EVAL_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("MT_EVAL_ADMIN_PASSWORD", "change-me")


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
            return redirect(url_for("admin_login_view", next=request.path))
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
                return redirect(url_for("annotator_login_view", campaign_id=campaign_id))
            return redirect(url_for("landing"))
        return view(*args, **kwargs)
    return wrapper
