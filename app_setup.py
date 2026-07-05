import logging
import click
from flask import request, jsonify

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

from services.auth_service import current_annotator, ADMIN_EMAIL
from utils.security import load_secret_key
import mailer


def register_components(app):

    app.config["SECRET_KEY"] = load_secret_key()

    @app.context_processor
    def inject_globals():
        ep = request.endpoint or ""

        if ep.startswith("admin_"):
            page_role = "admin"

        elif ep.startswith("annotator_") or ep in {
            "rate_view",
            "api_submit",
            "api_config",
        }:
            page_role = "annotator"

        else:
            page_role = "public"

        return {
            "current_annotator": current_annotator(),
            "page_role": page_role,
        }

    @app.cli.command("test-email")
    @click.argument("address")
    def test_email_command(address):
        """Send a test email to ADDRESS to check your email configuration."""
        transport = mailer.active_transport()
        print("Active transport:", transport or "none (will only log)")
        ok, detail = mailer.send_email(
            address, "Aura test email",
            "This is a test email from Aura. If you received this, email delivery is working.")
        print("Sent:", ok)
        print("Detail:", detail)
        if not ok and not transport:
            print("\nNo email transport is configured. Set one of:")
            print("  RESEND_API_KEY   (+ MT_EVAL_EMAIL_FROM)   : recommended for hosted apps")
            print("  SENDGRID_API_KEY (+ MT_EVAL_EMAIL_FROM)")
            print("  MT_EVAL_SMTP_HOST / _USER / _PASSWORD ...  : classic SMTP")

    # ============================================================================
    # Healthcheck
    # ============================================================================

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True, "admin_configured": ADMIN_EMAIL != "admin@example.com"})

    @app.route("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.svg")
