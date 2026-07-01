from flask import Blueprint, render_template

from services.auth_service import current_admin, current_annotator, admin_signup_enabled

public_bp = Blueprint("public", __name__)


@public_bp.route("/")
def landing():
    return render_template(
        "landing.html",
        is_admin=bool(current_admin()),
        signup_enabled=admin_signup_enabled(),
        annotator=current_annotator(),
    )
