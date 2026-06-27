"""MT Evaluation Platform -- Flask app.

Admin: env vars MT_EVAL_ADMIN_EMAIL / MT_EVAL_ADMIN_PASSWORD.
Annotators: registered via /campaign/<id> after admin shares the link.

Run:
    PORT=8000 python app.py

Production:
    gunicorn -w 4 -b 0.0.0.0:8000 app:app
"""

import json
import logging
import os
import re
import click
from datetime import datetime, timedelta
from pathlib import Path
from flask_migrate import Migrate

from flask import (Flask, render_template, request, jsonify, redirect, url_for,
                   session, flash, abort)

from dotenv import load_dotenv

load_dotenv()                   

import exporter
import results
import llm
import seed_data
from auth import (admin_login, current_admin, current_annotator,
                  require_admin, require_annotator, ADMIN_EMAIL)
import models
import mailer
from models import db, Annotator, Campaign, Rating, AssistantLog, Admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).parent
DATA_DIR = APP_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)




# -- Evaluation rubric (shared with the rating template + JS) -----------------
CRITERIA_DEFAULTS = [
    {"id": "adequacy", "name": "Adequacy",
     "color": "#0072B2",
     "desc": "Source information preserved in translation",
     "guide": "Measures how much information from the source sentence is correctly preserved in the translation. A high adequacy score means the translated sentence conveys the same content and intent as the source. Penalize omissions, additions, and mistranslations."},
    {"id": "fluency", "name": "Fluency",
     "color": "#E69F00",
     "desc": "Natural and grammatical target text",
     "guide": "Measures how natural, grammatically correct, and readable the translated sentence is in the target language. Judge this independently of the source -- ignore meaning for a moment and ask whether a native speaker would find the sentence well-formed."},
    {"id": "meaning_preservation", "name": "Meaning preservation",
     "color": "#009E73",
     "desc": "Overall meaning intact, no hallucination",
     "guide": "Measures whether the overall meaning and context of the source sentence are maintained without distortion or misunderstanding in the translation, and whether any hallucinated content (information not present in the source) has been introduced. Even a fluent and superficially adequate translation should score low here if it subtly changes the meaning or invents details."},
]

# Pool of colors auto-assigned to admin-defined criteria
CRITERION_COLOR_POOL = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442"]


def _slugify(s, fallback):
    """Lowercase, ascii, underscores. For deriving criterion IDs from names."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or fallback


def get_criteria_for(campaign):
    """Return the effective criteria list for a campaign."""
    return campaign.criteria or CRITERIA_DEFAULTS


def get_scale_for(campaign):
    """Return the normalized scale config dict for a campaign (template/JS friendly)."""
    return campaign.scale_config


# Valid rating-scale designs, grouped by scale type. Used to validate admin input
# and to populate the campaign form selectors.
SCALE_DESIGNS = {
    "likert": [
        {"id": "circles", "name": "Numbered circles", "desc": "Round 1–N buttons (the classic look)."},
        {"id": "buttons", "name": "Labelled buttons", "desc": "Wider pills showing the number and its label."},
        {"id": "radio",   "name": "Radio buttons",    "desc": "Classic radio dials with a label under each."},
        {"id": "stars",   "name": "Star rating",      "desc": "Click a star from 1 to N."},
    ],
    "continuous": [
        {"id": "slider",  "name": "Slider",           "desc": "A draggable slider across the range (e.g. Direct Assessment 0–100)."},
    ],
}

SCALE_TYPES = [
    {"id": "likert", "name": "Likert (discrete points)",
     "desc": "Annotators pick one of a fixed number of points (e.g. a 1–5 quality scale)."},
    {"id": "continuous", "name": "Continuous (slider)",
     "desc": "Annotators pick any value on a range, e.g. Direct Assessment from 0 to 100."},
]

# Top-level evaluation modes the admin can choose between.
EVAL_MODES = [
    {"id": "likert", "name": "Likert scale rating",
     "desc": "Annotators score each criterion on a scale. Optionally also mark error spans (target only, or both source and target)."},
    {"id": "pairwise", "name": "Pairwise preference",
     "desc": "Annotators compare two candidate translations and choose which is better, using preference options you define."},
    {"id": "span_only", "name": "Span annotation only",
     "desc": "Annotators only mark error spans (no scoring). Your criteria become the error-type categories."},
    {"id": "post_edit", "name": "Post-editing",
     "desc": "Annotators correct the output by editing it directly. Results report how much editing each system needed (edit rate / distance), a lighter-is-better quality signal."},
]


def parse_preferences_from_form(form):
    """Parse parallel pref_label[] inputs into an ordered options list [{id,label}]."""
    labels = form.getlist("pref_label")
    prefs = []
    used = set()
    for i, lb in enumerate(labels):
        lb = (lb or "").strip()
        if not lb:
            continue
        base = _slugify(lb, f"option_{i+1}")
        pid = base
        n = 2
        while pid in used:
            pid = f"{base}_{n}"; n += 1
        used.add(pid)
        prefs.append({"id": pid, "label": lb})
    return prefs


def _valid_design_for(scale_type, design):
    return design in {d["id"] for d in SCALE_DESIGNS.get(scale_type, [])}


def parse_scale_from_form(form, errors):
    """Read the rating-scale fields from a submitted form.

    Returns a dict of Campaign kwargs (scale_type, scale_points, scale_min,
    scale_max, scale_design, scale_labels_json). Appends to `errors` on problems.
    """
    scale_type = (form.get("scale_type") or "likert").strip()
    if scale_type not in {t["id"] for t in SCALE_TYPES}:
        errors.append("Invalid rating scale type.")
        scale_type = "likert"

    design = (form.get("scale_design") or "").strip()
    labels = {}
    scale_points, scale_min, scale_max = 5, 0, 100

    if scale_type == "likert":
        try:
            scale_points = int(form.get("scale_points") or 5)
        except (TypeError, ValueError):
            scale_points = 5
        if scale_points < 2 or scale_points > 11:
            errors.append("A Likert scale must have between 2 and 11 points.")
            scale_points = max(2, min(11, scale_points))
        if not _valid_design_for("likert", design):
            design = "circles"
        # Optional per-point labels: fields scale_label_1 .. scale_label_N
        for v in range(1, scale_points + 1):
            txt = (form.get(f"scale_label_{v}") or "").strip()
            if txt:
                labels[str(v)] = txt
    else:  # continuous
        try:
            scale_min = int(form.get("scale_min") or 0)
            scale_max = int(form.get("scale_max") or 100)
        except (TypeError, ValueError):
            errors.append("Continuous scale bounds must be whole numbers.")
            scale_min, scale_max = 0, 100
        if scale_max <= scale_min:
            errors.append("The continuous scale's maximum must be greater than its minimum.")
        if not _valid_design_for("continuous", design):
            design = "slider"
        min_lbl = (form.get("scale_min_label") or "").strip()
        max_lbl = (form.get("scale_max_label") or "").strip()
        if min_lbl:
            labels["min"] = min_lbl
        if max_lbl:
            labels["max"] = max_lbl

    return {
        "scale_type": scale_type,
        "scale_points": scale_points,
        "scale_min": scale_min,
        "scale_max": scale_max,
        "scale_design": design,
        "scale_labels_json": json.dumps(labels, ensure_ascii=False) if labels else "",
    }


def parse_criteria_from_form(form):
    """Parse the parallel crit_name[]/crit_desc[] arrays into a criteria list.

    Returns (criteria, criteria_missing_desc).
    """
    crit_names = form.getlist("crit_name")
    crit_descs = form.getlist("crit_desc")
    criteria = []
    criteria_missing_desc = []
    used_ids = set()
    for i, nm in enumerate(crit_names):
        nm = (nm or "").strip()
        if not nm:
            continue
        desc = (crit_descs[i] if i < len(crit_descs) else "").strip()
        if not desc:
            criteria_missing_desc.append(nm)
        base_id = _slugify(nm, f"criterion_{i+1}")
        cid = base_id
        n = 2
        while cid in used_ids:
            cid = f"{base_id}_{n}"; n += 1
        used_ids.add(cid)
        color = CRITERION_COLOR_POOL[len(criteria) % len(CRITERION_COLOR_POOL)]
        criteria.append({"id": cid, "name": nm, "color": color, "desc": "", "guide": desc})
    return criteria, criteria_missing_desc

SCALE_LABELS = {1: "Very poor", 2: "Poor", 3: "Acceptable", 4: "Good", 5: "Excellent"}

SCRIPT_OPTIONS = [
    {"id": "bengali",     "name": "Bengali–Assamese (Eastern Nagari)", "short": "Bengali–Assamese"},
    {"id": "meetei",      "name": "Meitei Mayek",                       "short": "Meitei Mayek"},
    {"id": "devanagari",  "name": "Devanagari",                         "short": "Devanagari"},
    {"id": "latin",       "name": "Latin / Roman",                      "short": "Latin"},
    {"id": "tibetan",     "name": "Tibetan",                            "short": "Tibetan"},
    {"id": "ol_chiki",    "name": "Ol Chiki (Santali)",                 "short": "Ol Chiki"},
    {"id": "tai",         "name": "Tai (Ahom / Tai Le)",                "short": "Tai"},
    {"id": "wancho",      "name": "Wancho",                             "short": "Wancho"},
    {"id": "other",       "name": "Other",                              "short": "Other"},
]

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


# -- App setup ----------------------------------------------------------------
app = Flask(__name__)
def _load_or_create_secret_key():
    """Use the env var if set; otherwise persist a generated key to data/secret_key
    so sessions survive restarts and multiple workers share the same key."""
    env_key = os.environ.get("MT_EVAL_SECRET_KEY")
    if env_key:
        return env_key
    key_file = DATA_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    import secrets
    key = secrets.token_hex(32)
    try:
        key_file.write_text(key)
        logger.warning("MT_EVAL_SECRET_KEY not set; generated and saved one to %s. "
                       "For production, set MT_EVAL_SECRET_KEY explicitly.", key_file)
    except Exception:
        logger.exception("Could not persist generated secret key")
    return key

app.config["SECRET_KEY"] = _load_or_create_secret_key()


# Database: use DATABASE_URL when provided (e.g. hosted Postgres on Render/Railway/Neon),

_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url.startswith("postgres://"):
    _db_url = "postgresql://" + _db_url[len("postgres://"):]

# PostgreSQL setup
# PostgreSQL setup
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.getenv("DATABASE_URL") or
    f"postgresql+psycopg2://"
    f"{os.getenv('POSTGRES_USER')}:"
    f"{os.getenv('POSTGRES_PASSWORD')}@"
    f"{os.getenv('POSTGRES_HOST')}:"
    f"{os.getenv('POSTGRES_PORT')}/"
    f"{os.getenv('POSTGRES_DB')}"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload cap

# Keep people signed in across tabs, navigation, and browser restarts. Sessions
# are marked permanent on login (see the auth routes) and persist for 30 days.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

db.init_app(app)
migrate = Migrate(app, db)


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


# ============================================================================
# Public landing
# ============================================================================

@app.route("/")
def landing():
    return render_template("landing.html",
                           is_admin=bool(current_admin()),
                           signup_enabled=admin_signup_enabled(),
                           annotator=current_annotator())


# ============================================================================
# Admin auth
# ============================================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login_view():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if admin_login(email, password):
            # Don't wipe an annotator session that may be active in another tab;
            # admin and annotator roles can be signed in side by side.
            session.permanent = True
            session["is_admin"] = True
            session["admin_email"] = email
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "error")
    return render_template("admin_login.html", signup_enabled=admin_signup_enabled())


@app.route("/admin/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        admin = Admin.query.filter_by(email=email).first()

        if admin:
            otp = generate_otp()
            admin.set_otp(otp)
            db.session.commit()

            mailer.send_password_reset_otp(email, otp)

        flash(
            "If an account with that email exists, a verification code has been sent.",
            "success"
        )

        session["reset_email"] = email

        return redirect(url_for("admin_verify_otp"))

    return render_template(
        "forget_password.html",
        action_url=url_for("admin_forgot_password"),
        back_url=url_for("admin_login_view")
    )


@app.route("/admin/verify-otp", methods=["GET", "POST"])
def admin_verify_otp():

    email = session.get("reset_email")

    if not email:
        flash("Password reset session expired. Please start again.", "error")
        return redirect(url_for("admin_forgot_password"))

    admin = Admin.query.filter_by(email=email).first()

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not admin or not admin.check_otp(otp):
            flash("Invalid or expired verification code.", "error")
            return redirect(url_for("admin_verify_otp"))

        # OTP is valid
        admin.clear_otp()
        db.session.commit()

        session["reset_verified"] = True

        flash(
            "Verification successful. Please choose a new password.",
            "success"
        )

        return redirect(url_for("admin_reset_password"))

    return render_template(
        "verify_otp.html",
        action_url=url_for("admin_verify_otp"),
        back_url=url_for("admin_forgot_password")
    )

@app.route("/admin/reset-password", methods=["GET", "POST"])
def admin_reset_password():

    email = session.get("reset_email")

    if not email or not session.get("reset_verified"):
        flash("Please verify your identity first.", "error")
        return redirect(url_for("admin_forgot_password"))

    admin = Admin.query.filter_by(email=email).first()

    if not admin:
        flash("Account not found.", "error")
        return redirect(url_for("admin_forgot_password"))

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        errors = []

        problems = password_problems(password)
        if problems:
            errors.append(
                "Password must contain " + ", ".join(problems) + "."
            )

        if password != confirm:
            errors.append("Passwords do not match.")

        if errors:
            for error in errors:
                flash(error, "error")

            return redirect(url_for("admin_reset_password"))

        admin.set_password(password)
        admin.clear_otp()

        db.session.commit()

        session.pop("reset_email", None)
        session.pop("reset_verified", None)

        flash("Password has been reset successfully.", "success")

        return redirect(url_for("admin_login_view"))

    return render_template(
        "reset_password.html",
        action_url=url_for("admin_reset_password")
    )

@app.route("/admin/update-password", methods=["GET", "POST"])
@require_admin
def admin_update_password():
    admin = Admin.query.filter_by(
        email=session.get("admin_email")
    ).first_or_404()

    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        errors = []

        if not admin.check_password(current_pw):
            errors.append("Current password is incorrect.")

        _np = password_problems(new_pw)
        if _np:
            errors.append(
                "New password must contain " +
                ", ".join(_np) + "."
            )

        if new_pw != confirm_pw:
            errors.append("New passwords don't match.")

        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("admin_update_password"))

        admin.set_password(new_pw)
        db.session.commit()

        flash("Password updated successfully.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template(
        "update_password.html",
        role="admin",
        action_url=url_for("admin_update_password")
    )


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


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin_email", None)
    flash("Signed out.", "info")
    return redirect(url_for("landing"))


def _valid_email(e):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e or ""))


PASSWORD_RULE_TEXT = ("at least 8 characters, including an uppercase letter, a lowercase letter, "
                      "a number, and a symbol")


def password_problems(pw):
    """Return a list of unmet password requirements (empty list = strong enough)."""
    probs = []
    if len(pw or "") < 8:
        probs.append("at least 8 characters")
    if not re.search(r"[a-z]", pw or ""):
        probs.append("a lowercase letter")
    if not re.search(r"[A-Z]", pw or ""):
        probs.append("an uppercase letter")
    if not re.search(r"[0-9]", pw or ""):
        probs.append("a number")
    if not re.search(r"[^A-Za-z0-9]", pw or ""):
        probs.append("a symbol")
    return probs

import secrets

def generate_otp():
    return f"{secrets.randbelow(1000000):06d}"


def _csv_env(name):
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def admin_signup_enabled():
    """Master switch for admin self-registration (default on)."""
    return os.environ.get("MT_EVAL_ADMIN_SIGNUP_ENABLED", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def admin_invite_codes():
    return _csv_env("MT_EVAL_ADMIN_INVITE_CODES")


def admin_allowed_domains():
    return [d.lower().lstrip("@") for d in _csv_env("MT_EVAL_ADMIN_EMAIL_DOMAINS")]


def _invite_ok(code):
    import hmac
    codes = admin_invite_codes()
    if not codes:
        return True
    code = (code or "").strip()
    return any(hmac.compare_digest(code, c) for c in codes)


def _domain_ok(email):
    domains = admin_allowed_domains()
    if not domains:
        return True
    dom = (email.rsplit("@", 1)[-1] if "@" in (email or "") else "").lower()
    return dom in domains


@app.route("/admin/signup", methods=["GET", "POST"])
def admin_signup_view():
    invite_required = bool(admin_invite_codes())
    allowed_domains = admin_allowed_domains()
    ctx = {"invite_required": invite_required, "allowed_domains": allowed_domains,
           "signup_enabled": admin_signup_enabled()}
    if not admin_signup_enabled():
        return render_template("admin_signup.html", form_email="", signup_disabled=True, **ctx)
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")
        invite = request.form.get("invite_code", "")
        errors = []
        if not _valid_email(email):
            errors.append("Please enter a valid email address.")
        if not _domain_ok(email):
            errors.append("That email domain isn't allowed to register here. "
                          "Allowed: " + ", ".join("@" + d for d in allowed_domains) + ".")
        if invite_required and not _invite_ok(invite):
            errors.append("That invite code isn't valid.")
        probs = password_problems(password)
        if probs:
            errors.append("Password must contain " + ", ".join(probs) + ".")
        if password != confirm:
            errors.append("Passwords do not match.")
        if email == ADMIN_EMAIL.strip().lower():
            errors.append("That email is reserved for the built-in admin; please sign in instead.")
        existing = Admin.query.filter_by(email=email).first()
        if existing and existing.is_verified:
            errors.append("An admin account with that email already exists. Please sign in.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin_signup.html", form_email=email, **ctx)

        admin = existing or Admin(email=email)
        admin.set_password(password)
        admin.is_verified = False
        code = mailer.generate_otp()
        admin.set_otp(code)
        db.session.add(admin)
        db.session.commit()
        sent, detail = mailer.send_otp(email, code)
        session["pending_admin_email"] = email
        if sent:
            flash("We've emailed you a 6-digit verification code. Enter it below to finish.", "success")
        elif mailer.active_transport():
            flash("We couldn't send the email: " + detail + " Please check your email settings "
                  "or try resending.", "error")
        elif mailer.dev_echo_enabled():
            flash(f"Email isn't configured on this server. Your verification code is {code} "
                  "(shown because dev echo is on).", "info")
        else:
            flash("Email isn't configured on this server, so the verification code was written "
                  "to the server log. Configure an email transport (RESEND_API_KEY, "
                  "SENDGRID_API_KEY, or SMTP) to deliver codes by email.", "info")
        return redirect(url_for("admin_verify_view"))
    return render_template("admin_signup.html", form_email="", **ctx)


@app.route("/admin/verify", methods=["GET", "POST"])
def admin_verify_view():
    email = (request.values.get("email") or session.get("pending_admin_email") or "").strip().lower()
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        admin = Admin.query.filter_by(email=email).first()
        if not admin:
            flash("No pending sign-up found for that email. Please sign up again.", "error")
            return redirect(url_for("admin_signup_view"))
        if admin.is_verified:
            flash("This account is already verified; please sign in.", "info")
            return redirect(url_for("admin_login_view"))
        admin.otp_attempts = (admin.otp_attempts or 0) + 1
        if admin.otp_attempts > 6:
            db.session.commit()
            flash("Too many attempts. Please request a new code.", "error")
            return render_template("admin_verify.html", email=email)
        if admin.check_otp(code):
            admin.is_verified = True
            admin.otp_hash = ""
            admin.otp_expires_at = None
            db.session.commit()
            session.pop("pending_admin_email", None)
            session.permanent = True
            session["is_admin"] = True
            session["admin_email"] = email
            flash("Your admin account is verified. Welcome to Aura!", "success")
            return redirect(url_for("admin_dashboard"))
        db.session.commit()
        flash("That code is incorrect or has expired. Try again or request a new code.", "error")
    return render_template("admin_verify.html", email=email)


@app.route("/admin/verify/resend", methods=["POST"])
def admin_verify_resend():
    email = (request.form.get("email") or session.get("pending_admin_email") or "").strip().lower()
    admin = Admin.query.filter_by(email=email, is_verified=False).first()
    if not admin:
        flash("No pending sign-up found for that email.", "error")
        return redirect(url_for("admin_signup_view"))
    code = mailer.generate_otp()
    admin.set_otp(code)
    db.session.commit()
    sent, detail = mailer.send_otp(email, code)
    if sent:
        flash("A new code is on its way.", "success")
    elif mailer.active_transport():
        flash("We couldn't send the email: " + detail, "error")
    elif mailer.dev_echo_enabled():
        flash(f"New verification code: {code} (dev echo).", "info")
    else:
        flash("A new code was written to the server log (email isn't configured).", "info")
    return redirect(url_for("admin_verify_view"))


# ============================================================================
# Admin dashboard + campaign CRUD
# ============================================================================

@app.route("/admin/dashboard")
@require_admin
def admin_dashboard():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    # Annotator counts per campaign
    stats = {}
    for c in campaigns:
        crit_ids = [cr["id"] for cr in get_criteria_for(c)]
        annotator_ids = {r.annotator_id for r in c.ratings}
        stats[c.id] = {
            "annotator_count": len(annotator_ids),
            "total_ratings": sum(1 for r in c.ratings if c.rating_is_complete(r, get_criteria_for(c))),
            "max_possible": c.num_segments * max(1, len(annotator_ids)),
        }
    return render_template("admin_dashboard.html",
                           campaigns=campaigns, stats=stats,
                           has_demo=seed_data.demo_present(Campaign),)


@app.route("/admin/dashboard.json")
@require_admin
def admin_dashboard_json():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    out = []
    for c in campaigns:
        crit = get_criteria_for(c)
        annotator_ids = {r.annotator_id for r in c.ratings}
        out.append({
            "id": c.id,
            "annotator_count": len(annotator_ids),
            "total_ratings": sum(1 for r in c.ratings if c.rating_is_complete(r, crit)),
            "is_closed": bool(c.is_closed),
        })
    return jsonify({"ok": True, "campaigns": out})


@app.route("/admin/seed-demo", methods=["POST"])
@require_admin
def admin_seed_demo():
    try:
        summary = seed_data.seed_all(db, Campaign, Annotator, Rating)
    except Exception:
        logger.exception("Demo seeding failed")
        flash("Could not load the demo campaigns. See the server log for details.", "error")
        return redirect(url_for("admin_dashboard"))
    n_c = len(summary["campaigns"])
    if n_c or summary["annotators"] or summary["ratings"]:
        flash(f"Loaded {n_c} demo campaign(s), {summary['annotators']} demo annotator(s), "
              f"and {summary['ratings']} synthetic rating(s) so the dashboards are populated. "
              "The sample translations are illustrative; replace them with your own outputs "
              "before a real evaluation.", "success")
    else:
        flash("The demo data is already loaded.", "info")
    return redirect(url_for("admin_dashboard"))


@app.cli.command("seed-demo")
def seed_demo_command():
    """Load the Northeast India language demo campaigns, annotators, and ratings."""
    with app.app_context():
        summary = seed_data.seed_all(db, Campaign, Annotator, Rating)
    print("Demo campaigns created: %d" % len(summary["campaigns"]))
    for name in summary["campaigns"]:
        print("  -", name)
    print("Demo annotators created: %d" % summary["annotators"])
    print("Synthetic ratings created: %d" % summary["ratings"])


@app.route("/admin/unload-demo", methods=["POST"])
@require_admin
def admin_unload_demo():
    try:
        summary = seed_data.unload_demo(db, Campaign, Annotator, Rating, AssistantLog)
    except Exception:
        logger.exception("Demo unload failed")
        flash("Could not remove the demo data. See the server log for details.", "error")
        return redirect(url_for("admin_dashboard"))
    if summary["campaigns"] or summary["annotators"] or summary["ratings"]:
        flash(f"Removed {summary['campaigns']} demo campaign(s), {summary['annotators']} demo "
              f"annotator(s), and {summary['ratings']} demo rating(s).", "success")
    else:
        flash("There was no demo data to remove.", "info")
    return redirect(url_for("admin_dashboard"))


@app.cli.command("unload-demo")
def unload_demo_command():
    """Remove the demo campaigns, annotators, and synthetic ratings."""
    with app.app_context():
        summary = seed_data.unload_demo(db, Campaign, Annotator, Rating, AssistantLog)
    print("Removed %(campaigns)d campaign(s), %(annotators)d annotator(s), "
          "%(ratings)d rating(s)." % summary)


def _validate_segments_for_mode(segments, eval_mode, campaign_for_norm=None):
    """Return a list of error strings for the given segments under a mode."""
    errors = []
    if not isinstance(segments, list) or len(segments) == 0:
        return ["Segments JSON must be a non-empty array."]
    seen_ids = set()
    # Use a throwaway Campaign just for the candidate-normalization helper.
    norm = campaign_for_norm or Campaign(segments_json="[]")
    for i, s in enumerate(segments):
        if not isinstance(s, dict):
            errors.append(f"Segment {i+1}: must be an object.")
            continue
        if "id" not in s or "source" not in s:
            errors.append(f"Segment {i+1}: must have at least 'id' and 'source'.")
            continue
        if eval_mode == "pairwise":
            a, b, _, _ = norm.pairwise_candidates(s)
            if not a or not b:
                errors.append(f"Segment {i+1}: pairwise mode needs two candidates "
                              "(provide 'target_a' and 'target_b', or a 'candidates' list of two).")
        else:
            if "target" not in s:
                errors.append(f"Segment {i+1}: must have a 'target'.")
        if s.get("id") in seen_ids:
            errors.append(f"Duplicate segment id: {s.get('id')}")
        seen_ids.add(s.get("id"))
    return errors


def _collect_campaign_form(form, files, *, parse_segments, existing_segments=None,
                           existing_campaign=None):
    """Read + validate all campaign-config fields from a submitted form.

    Returns (config_kwargs, segments_or_None, errors, form_view).
    `config_kwargs` excludes segments_json (caller decides whether to set it).
    """
    errors = []
    name = form.get("name", "").strip()
    task_type = (form.get("task_type") or "translation").strip()
    if task_type not in models.TASK_TYPES:
        task_type = "translation"
    input_label = form.get("input_label", "").strip()[:80]
    output_label = form.get("output_label", "").strip()[:80]
    difficulty_method = (form.get("difficulty_method") or "auto").strip()
    if difficulty_method not in ("auto", "length", "manual", "none"):
        difficulty_method = "auto"
    def _posint(name):
        try:
            return max(0, int(form.get(name) or 0))
        except (TypeError, ValueError):
            return 0
    difficulty_easy_max = _posint("difficulty_easy_max")
    difficulty_hard_min = _posint("difficulty_hard_min")
    # Which difficulty levels to serve. Store "" when all (or none) are checked, so the
    # default is "serve everything"; otherwise store the chosen subset.
    _served = [d for d in form.getlist("served_diff") if d in ("easy", "medium", "hard")]
    if difficulty_method == "none" or set(_served) == {"easy", "medium", "hard"}:
        served_difficulties = ""
    else:
        served_difficulties = ",".join(d for d in ("easy", "medium", "hard") if d in _served)
    expertise_matching = bool(form.get("expertise_matching")) and difficulty_method != "none"
    rating_position = (form.get("rating_position") or "side").strip()
    if rating_position not in ("side", "below"):
        rating_position = "side"
    span_position = (form.get("span_position") or "below").strip()
    if span_position not in ("below", "beside"):
        span_position = "below"
    source_language = form.get("source_language", "").strip()
    target_language = form.get("target_language", "").strip()
    script = form.get("script", "").strip() or None
    instructions = form.get("instructions", "").strip()
    span_instructions = form.get("span_instructions", "").strip()
    if not instructions:
        errors.append("Annotation instructions are required.")

    eval_mode = (form.get("eval_mode") or "likert").strip()
    if eval_mode not in {m["id"] for m in EVAL_MODES}:
        errors.append("Invalid evaluation mode.")
        eval_mode = "likert"

    # Spans depend on the mode.
    if eval_mode == "span_only":
        enable_spans = True
        span_scope = form.get("span_scope", "").strip()
        if span_scope not in ("target", "both"):
            span_scope = ""   # validated below
    elif eval_mode in ("pairwise", "post_edit"):
        enable_spans = False
        span_scope = "target"
    else:  # likert
        enable_spans = form.get("enable_spans") == "on"
        span_scope = form.get("span_scope", "").strip()
        if enable_spans:
            if span_scope not in ("target", "both"):
                span_scope = ""
        else:
            span_scope = "target"

    try:
        segments_per_page = int(form.get("segments_per_page") or 3)
    except (TypeError, ValueError):
        segments_per_page = 3

    # Scale config (only meaningful in likert mode; keep sane defaults otherwise).
    scale_errors = []
    if eval_mode == "likert":
        scale_kwargs = parse_scale_from_form(form, scale_errors)
    else:
        scale_kwargs = {"scale_type": "likert", "scale_points": 5, "scale_min": 0,
                        "scale_max": 100, "scale_design": "circles", "scale_labels_json": ""}

    # Criteria (used in likert + span_only) and preferences (pairwise).
    criteria, criteria_missing_desc = parse_criteria_from_form(form)
    preferences = parse_preferences_from_form(form)

    # ---- validation ----
    if not name:
        errors.append("Campaign name is required.")
    bilingual = models.task_defaults(task_type).get("bilingual")
    if bilingual:
        if not source_language:
            errors.append("Source language is required.")
        if not target_language:
            errors.append("Target language is required.")
    if script and script not in {s["id"] for s in SCRIPT_OPTIONS}:
        errors.append("Invalid script option.")
    if segments_per_page < 1 or segments_per_page > 50:
        errors.append("Segments per page must be between 1 and 50.")

    if eval_mode in ("likert", "span_only"):
        if len(criteria) == 0:
            label = "evaluation criterion" if eval_mode == "likert" else "error-type category"
            errors.append(f"Please define at least one {label}.")
        if criteria_missing_desc:
            names = ", ".join(criteria_missing_desc)
            errors.append("Each criterion needs a definition shown to annotators. "
                          f"Missing definition for: {names}.")
    if eval_mode == "likert":
        errors.extend(scale_errors)
        if enable_spans and span_scope not in ("target", "both"):
            errors.append("Please select whether span annotation applies to the target only or to both source and target.")
    if eval_mode == "span_only":
        if span_scope not in ("target", "both"):
            errors.append("Please select whether spans are marked in the target only or in both source and target.")
    if eval_mode == "pairwise":
        if len(preferences) < 2:
            errors.append("Please define at least two preference options for pairwise comparison.")

    # ---- segments ----
    segments = None
    if parse_segments:
        segments_raw = ""
        upload = files.get("segments_file") if files else None
        if upload and upload.filename:
            try:
                segments_raw = upload.read().decode("utf-8")
            except UnicodeDecodeError:
                errors.append("Could not read the uploaded file as UTF-8.")
                segments_raw = ""
        else:
            segments_raw = form.get("segments_paste", "").strip()

        if not segments_raw:
            errors.append("Please upload or paste the segments JSON file.")
        else:
            try:
                segments = json.loads(segments_raw)
                errors.extend(_validate_segments_for_mode(segments, eval_mode))
            except json.JSONDecodeError as e:
                errors.append(f"Segments JSON is not valid: {e}")
        form_segments_paste = segments_raw
    else:
        # Editing: validate the existing segments against the (possibly new) mode.
        if existing_segments is not None:
            errors.extend(_validate_segments_for_mode(existing_segments, eval_mode))
        form_segments_paste = ""

    # ---- AI assistant config ----
    ai_enabled = form.get("ai_enabled") == "on"
    ai_provider = (form.get("ai_provider") or "").strip()
    ai_model = (form.get("ai_model") or "").strip()
    ai_base_url = (form.get("ai_base_url") or "").strip()
    ai_key_input = form.get("ai_api_key") or ""
    existing_key_enc = (existing_campaign.ai_api_key_enc if existing_campaign else "") or ""
    # Encrypt a freshly-entered key; otherwise keep whatever was stored.
    if ai_key_input.strip():
        ai_api_key_enc = llm.encrypt_secret(ai_key_input.strip(), app.config["SECRET_KEY"])
    else:
        ai_api_key_enc = existing_key_enc
    has_key = bool(ai_api_key_enc)

    # Randomised AI experiment
    ai_ab_enabled = bool(form.get("ai_ab_enabled")) and ai_enabled
    try:
        ai_ab_fraction = max(0, min(100, int(form.get("ai_ab_fraction") or 50)))
    except (TypeError, ValueError):
        ai_ab_fraction = 50

    if ai_enabled:
        meta = llm.provider_by_id(ai_provider)
        if not meta:
            errors.append("Please choose a model provider for the AI assistant.")
        else:
            if meta["needs_key"] and not has_key:
                errors.append(f"{meta['name']} needs an API key for the AI assistant.")
            if meta["needs_base_url"] and not (ai_base_url or meta["default_base_url"]):
                errors.append(f"{meta['name']} needs an endpoint URL for the AI assistant.")
            if not (ai_model or meta["default_model"]):
                errors.append("Please specify a model name for the AI assistant.")

    config = {
        "name": name,
        "task_type": task_type,
        "input_label": input_label,
        "output_label": output_label,
        "difficulty_method": difficulty_method,
        "difficulty_easy_max": difficulty_easy_max,
        "difficulty_hard_min": difficulty_hard_min,
        "served_difficulties": served_difficulties,
        "expertise_matching": expertise_matching,
        "rating_position": rating_position,
        "span_position": span_position,
        "source_language": source_language,
        "target_language": target_language,
        "script": script,
        "criteria_json": json.dumps(criteria, ensure_ascii=False),
        "instructions": instructions,
        "span_instructions": span_instructions,
        "enable_spans": enable_spans,
        "span_scope": span_scope,
        "segments_per_page": segments_per_page,
        "eval_mode": eval_mode,
        "preferences_json": json.dumps(preferences, ensure_ascii=False),
        "ai_enabled": ai_enabled,
        "ai_ab_enabled": ai_ab_enabled,
        "ai_ab_fraction": ai_ab_fraction,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "ai_base_url": ai_base_url,
        "ai_api_key_enc": ai_api_key_enc,
        **scale_kwargs,
    }
    form_view = {
        "name": name, "source_language": source_language,
        "task_type": task_type, "input_label": input_label, "output_label": output_label,
        "difficulty_method": difficulty_method,
        "difficulty_easy_max": difficulty_easy_max or "",
        "difficulty_hard_min": difficulty_hard_min or "",
        "served_difficulties": served_difficulties,
        "expertise_matching": expertise_matching,
        "rating_position": rating_position, "span_position": span_position,
        "target_language": target_language, "script": script or "",
        "instructions": instructions,
        "span_instructions": span_instructions, "enable_spans": enable_spans,
        "span_scope": span_scope, "segments_per_page": segments_per_page,
        "eval_mode": eval_mode,
        "criteria": [{"name": cr["name"], "desc": cr["guide"]} for cr in criteria],
        "preferences": preferences,
        "scale": _scale_form_view(scale_kwargs),
        "segments_paste": form_segments_paste,
        "ai_enabled": ai_enabled, "ai_ab_enabled": ai_ab_enabled, "ai_ab_fraction": ai_ab_fraction,
        "ai_provider": ai_provider, "ai_model": ai_model,
        "ai_base_url": ai_base_url, "ai_has_key": has_key,
    }
    return config, segments, errors, form_view


@app.route("/admin/campaign/new", methods=["GET", "POST"])
@require_admin
def admin_campaign_new():
    if request.method == "POST":
        config, segments, errors, form_view = _collect_campaign_form(
            request.form, request.files, parse_segments=True)
        if errors:
            for e in errors:
                flash(e, "error")
            return _render_new_campaign_form(form_data=form_view)
        c = Campaign(segments_json=json.dumps(segments, ensure_ascii=False), **config)
        c.owner_email = (session.get("admin_email") or ADMIN_EMAIL or "").strip().lower()
        db.session.add(c)
        db.session.commit()
        flash(f"Campaign '{config['name']}' created.", "success")
        return redirect(url_for("admin_campaign_detail", campaign_id=c.id))

    return _render_new_campaign_form()


def _scale_form_view(scale_kwargs):
    """Turn stored scale kwargs into a flat dict the form template can read back."""
    labels = {}
    try:
        labels = json.loads(scale_kwargs.get("scale_labels_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        labels = {}
    return {
        "type": scale_kwargs.get("scale_type", "likert"),
        "design": scale_kwargs.get("scale_design", "circles"),
        "points": scale_kwargs.get("scale_points", 5),
        "min": scale_kwargs.get("scale_min", 0),
        "max": scale_kwargs.get("scale_max", 100),
        "labels": {str(k): v for k, v in labels.items()},
    }


def _default_scale_view():
    return {"type": "likert", "design": "circles", "points": 5,
            "min": 0, "max": 100, "labels": {}}


def _render_new_campaign_form(form_data=None):
    fd = form_data or {}
    # Provide default criteria for a fresh form
    if "criteria" not in fd:
        fd["criteria"] = [{"name": c["name"], "desc": c["guide"]} for c in CRITERIA_DEFAULTS]
    if "scale" not in fd:
        fd["scale"] = _default_scale_view()
    if "segments_per_page" not in fd:
        fd["segments_per_page"] = 3
    if "eval_mode" not in fd:
        fd["eval_mode"] = "likert"
    if "preferences" not in fd:
        fd["preferences"] = list(Campaign.DEFAULT_PREFERENCES)
    if "ai_enabled" not in fd:
        fd["ai_enabled"] = False
    if "ai_provider" not in fd:
        fd["ai_provider"] = "anthropic"
    fd.setdefault("ai_model", "")
    fd.setdefault("ai_base_url", "")
    fd.setdefault("ai_has_key", False)
    return render_template("admin_campaign_new.html",
                           scripts=SCRIPT_OPTIONS,
                           scale_types=SCALE_TYPES,
                           scale_designs=SCALE_DESIGNS,
                           eval_modes=EVAL_MODES,
                           task_types=models.TASK_TYPES,
                           criterion_guides=models.CRITERION_GUIDES,
                           ai_providers=llm.PROVIDERS,
                           form_data=fd)


@app.route("/admin/campaign/<campaign_id>/edit", methods=["GET", "POST"])
@require_admin
def admin_campaign_edit(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        flash("This campaign is closed and can no longer be edited.", "error")
        return redirect(url_for("admin_campaign_detail", campaign_id=c.id))

    if request.method == "POST":
        config, _segments, errors, form_view = _collect_campaign_form(
            request.form, request.files, parse_segments=False,
            existing_segments=c.segments, existing_campaign=c)
        if errors:
            for e in errors:
                flash(e, "error")
            return _render_edit_campaign_form(c, form_data=form_view)
        # Apply every config field except segments (segments are fixed after creation).
        for k, v in config.items():
            setattr(c, k, v)
        db.session.commit()
        flash("Campaign configuration updated.", "success")
        return redirect(url_for("admin_campaign_detail", campaign_id=c.id))

    # GET -- prefill from the campaign
    crit = get_criteria_for(c)
    view = {
        "name": c.name,
        "task_type": c.task_type or "translation",
        "input_label": c.input_label or "",
        "output_label": c.output_label or "",
        "difficulty_method": c.difficulty_method or "auto",
        "difficulty_easy_max": c.difficulty_easy_max or "",
        "difficulty_hard_min": c.difficulty_hard_min or "",
        "served_difficulties": c.served_difficulties or "",
        "expertise_matching": c.expertise_matching,
        "rating_position": c.rating_position or "side",
        "span_position": c.span_position or "below",
        "source_language": c.source_language,
        "target_language": c.target_language,
        "script": c.script or "",
        "instructions": c.instructions or "",
        "span_instructions": c.span_instructions or "",
        "enable_spans": c.enable_spans if c.enable_spans is not None else True,
        "span_scope": c.span_scope or "target",
        "segments_per_page": c.segments_per_page or 3,
        "eval_mode": c.mode,
        "criteria": [{"name": x["name"], "desc": x.get("guide") or x.get("desc") or ""} for x in crit],
        "preferences": c.preferences,
        "ai_enabled": bool(c.ai_enabled),
        "ai_ab_enabled": bool(c.ai_ab_enabled),
        "ai_ab_fraction": c.ai_ab_fraction or 50,
        "ai_provider": c.ai_provider or "anthropic",
        "ai_model": c.ai_model or "",
        "ai_base_url": c.ai_base_url or "",
        "ai_has_key": bool(c.ai_api_key_enc),
        "scale": {
            "type": c.scale_type or "likert",
            "design": c.scale_design or "circles",
            "points": c.scale_points or 5,
            "min": c.scale_min if c.scale_min is not None else 0,
            "max": c.scale_max if c.scale_max is not None else 100,
            "labels": c.scale_labels,
        },
    }
    return _render_edit_campaign_form(c, form_data=view)


def _render_edit_campaign_form(campaign, form_data):
    return render_template("admin_campaign_edit.html",
                           campaign=campaign,
                           scripts=SCRIPT_OPTIONS,
                           scale_types=SCALE_TYPES,
                           scale_designs=SCALE_DESIGNS,
                           eval_modes=EVAL_MODES,
                           task_types=models.TASK_TYPES,
                           criterion_guides=models.CRITERION_GUIDES,
                           ai_providers=llm.PROVIDERS,
                           form_data=form_data)


@app.route("/admin/campaign/<campaign_id>")
@require_admin
def admin_campaign_detail(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    crit_ids = [cr["id"] for cr in get_criteria_for(c)]
    # Gather per-annotator progress
    rows = []
    annotator_ids = sorted({r.annotator_id for r in c.ratings})
    for aid in annotator_ids:
        ann = Annotator.query.get(aid)
        if not ann:
            continue
        rs = [r for r in c.ratings if r.annotator_id == aid]
        completed = sum(1 for r in rs if c.rating_is_complete(r, get_criteria_for(c)))
        last_update = max((r.updated_at for r in rs), default=None)
        rows.append({
            "annotator": ann,
            "completed": completed,
            "total": c.num_segments,
            "last_update": last_update,
        })
    rows.sort(key=lambda r: (-r["completed"], r["annotator"].email))

    share_url = url_for("annotator_login_view", campaign_id=c.id, _external=True)
    in_label, out_label = c.io_labels()
    mode_label = next((m["name"] for m in EVAL_MODES if m["id"] == c.mode), c.mode)
    diff_label = {"auto": "Automatic (composite heuristic)", "length": "By input length",
                  "manual": "Manual labels only", "none": "None"}.get(
                      c.difficulty_method or "auto", "Automatic")
    return render_template("admin_campaign_detail.html",
                           campaign=c, rows=rows, share_url=share_url,
                           criteria=get_criteria_for(c),
                           mode_label=mode_label, task_label=task_label(c),
                           input_label=in_label, output_label=out_label,
                           difficulty_label=diff_label,)


@app.route("/admin/campaign/<campaign_id>/progress.json")
@require_admin
def admin_campaign_progress(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    crit = get_criteria_for(c)
    rows = []
    annotator_ids = sorted({r.annotator_id for r in c.ratings})
    for aid in annotator_ids:
        ann = db.session.get(Annotator, aid)
        if not ann:
            continue
        rs = [r for r in c.ratings if r.annotator_id == aid]
        completed = sum(1 for r in rs if c.rating_is_complete(r, crit))
        last = max((r.updated_at for r in rs), default=None)
        rows.append({
            "name": ann.name, "email": ann.email,
            "expertise": ann.expertise or "",
            "completed": completed, "total": c.num_segments,
            "last_update": last.strftime("%Y-%m-%d %H:%M UTC") if last else None,
        })
    rows.sort(key=lambda r: (-r["completed"], r["email"]))
    return jsonify({"rows": rows, "n_annotators": len(rows)})


@app.route("/admin/campaign/<campaign_id>/close", methods=["POST"])
@require_admin
def admin_campaign_close(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        flash("Campaign is already closed.", "info")
    else:
        c.closed_at = datetime.utcnow()
        db.session.commit()
        # Freeze a results snapshot so the closed campaign keeps its final stats.
        try:
            crit = get_criteria_for(c)
            all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
            c.results_snapshot_json = json.dumps(
                results.compute_results(c, all_ratings, crit), ensure_ascii=False)
            db.session.commit()
        except Exception:
            logger.exception("Results snapshot failed for %s", c.id)
        # Build a complete results ZIP and email it to the campaign owner.
        try:
            crit = get_criteria_for(c)
            all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
            completed = [r for r in all_ratings if c.rating_is_complete(r, crit)]
            res = results.compute_results(c, all_ratings, crit)
            charts = results.build_charts(res)
            try:
                try:
                    with open(os.path.join(app.static_folder, "results.js"), encoding="utf-8") as fh:
                        _results_js = fh.read()
                except OSError:
                    _results_js = ""
                report_html = render_template(
                    "results_report.html", campaign=c, results=res, charts=charts,
                    frozen=True, results_js=_results_js,
                    generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
            except Exception:
                logger.exception("Report render for email failed for %s", c.id)
                report_html = None
            zip_bytes = exporter.build_results_zip(c, completed, crit, res, charts, report_html)
            to_email = (c.owner_email or ADMIN_EMAIL or "").strip()
            fname = exporter._safe_filename(c.name) + "_results.zip"
            subject = f"Aura results: {c.name}"
            body = (f"Your Aura campaign \"{c.name}\" has been closed.\n\n"
                    f"Attached is a ZIP with the full results: the per-rating CSV, a results "
                    f"summary, all charts as PNGs, the raw results JSON, and an HTML report.\n\n"
                    f"Completed annotations: {len(completed)}.")
            ok, detail = mailer.send_email(to_email, subject, body,
                                           attachments=[(fname, zip_bytes, "application/zip")])
            if ok:
                flash(f"Campaign closed. Results emailed to {to_email}.", "success")
            elif mailer.active_transport():
                flash(f"Campaign closed, but the results email failed: {detail} "
                      f"You can still download everything from the results page.", "error")
            else:
                flash("Campaign closed. Email isn't configured, so results weren't sent; "
                      "download them from the results page (or set up email to receive them).",
                      "info")
        except Exception:
            logger.exception("Building/sending results email failed for %s", c.id)
            flash("Campaign closed. Annotators can no longer edit. "
                  "(Preparing the results email failed; see the results page.)", "info")
    return redirect(url_for("admin_campaign_detail", campaign_id=c.id))


def _campaign_results(c):
    """Return (results_dict, is_frozen). Closed campaigns use the frozen snapshot
    if present; otherwise results are computed live."""
    if c.is_closed and c.results_snapshot_json:
        try:
            return json.loads(c.results_snapshot_json), True
        except json.JSONDecodeError:
            pass
    crit = get_criteria_for(c)
    all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
    return results.compute_results(c, all_ratings, crit), c.is_closed


@app.route("/admin/campaign/<campaign_id>/results")
@require_admin
def admin_campaign_results(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    return render_template("admin_campaign_results.html",
                           campaign=c, results=res, charts=charts, frozen=frozen,
                           results_version=_results_signature(c))


def _results_signature(c):
    """Cheap signature of the result-affecting state: rating count + latest edit + config."""
    ratings = list(c.ratings)
    n = len(ratings)
    last = max((r.updated_at for r in ratings), default=None)
    return f"{n}:{last.isoformat() if last else '-'}:{c.config_fingerprint()}:{'closed' if c.is_closed else 'open'}"


@app.route("/admin/campaign/<campaign_id>/results-version")
@require_admin
def admin_campaign_results_version(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    return jsonify({"ok": True, "version": _results_signature(c), "frozen": c.is_closed})


@app.route("/admin/campaign/<campaign_id>/results-fragment")
@require_admin
def admin_campaign_results_fragment(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    html = render_template("_results_body.html",
                           campaign=c, results=res, charts=charts, frozen=frozen)
    return jsonify({"ok": True, "version": _results_signature(c), "frozen": frozen,
                    "html": html, "results": res})


@app.route("/admin/campaign/<campaign_id>/results.json")
@require_admin
def admin_campaign_results_json(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    res, _ = _campaign_results(c)
    from flask import Response
    payload = json.dumps(res, ensure_ascii=False, indent=2)
    fname = exporter._safe_filename(c.name) + "_results.json"
    return Response(payload, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.route("/admin/campaign/<campaign_id>/results.html")
@require_admin
def admin_campaign_results_report(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    res, frozen = _campaign_results(c)
    charts = results.build_charts(res)
    from flask import Response
    # Inline the export JS so the downloaded report works offline.
    try:
        with open(os.path.join(app.static_folder, "results.js"), encoding="utf-8") as fh:
            results_js = fh.read()
    except OSError:
        results_js = ""
    html = render_template("results_report.html",
                           campaign=c, results=res, charts=charts, frozen=frozen,
                           results_js=results_js,
                           generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    fname = exporter._safe_filename(c.name) + "_results.html"
    return Response(html, mimetype="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.route("/admin/campaign/<campaign_id>/delete", methods=["POST"])
@require_admin
def admin_campaign_delete(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    # Require the admin to type the campaign name to confirm
    confirm = request.form.get("confirm_name", "").strip()
    if confirm != c.name:
        flash("Deletion cancelled: the name you typed didn't match.", "error")
        return redirect(url_for("admin_campaign_detail", campaign_id=c.id))
    name = c.name
    # Cascade deletes ratings (relationship cascade) -- delete the campaign row
    db.session.delete(c)
    db.session.commit()
    flash(f"Campaign '{name}' and all its ratings were deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/campaign/<campaign_id>/download_csv")
@require_admin
def admin_campaign_download_csv(campaign_id):
    """Download the master CSV directly from the server (alongside Drive)."""
    from flask import Response
    c = Campaign.query.get_or_404(campaign_id)
    crit = get_criteria_for(c)
    crit_ids = [cr["id"] for cr in crit]
    all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
    completed = [r for r in all_ratings if c.rating_is_complete(r, get_criteria_for(c))]
    data = exporter.build_master_csv(c, completed, crit)
    filename = exporter._safe_filename(c.name) + "_master.csv"
    return Response(
        data, mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================================
# Annotator auth (per-campaign landing, global account)
# ============================================================================

@app.route("/annotator/update-password", methods=["GET", "POST"])
@require_annotator
def annotator_update_password():
    ann = current_annotator()
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        errors = []
        if not ann.check_password(current_pw):
            errors.append("Current password is incorrect.")
        _np = password_problems(new_pw)
        if _np:
            errors.append("New password must contain " + ", ".join(_np) + ".")
        if new_pw != confirm_pw:
            errors.append("New passwords don't match.")
        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("annotator_update_password"))
        ann.set_password(new_pw)
        db.session.commit()
        flash("Password updated successfully.", "success")
        return redirect(url_for("annotator_dashboard"))
    return render_template(
        "update_password.html",
        role="annotator",
        action_url=url_for("annotator_update_password")
    )


@app.route("/campaign/<campaign_id>")
def annotator_landing_view(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

    if current_annotator():
        session["last_campaign_id"] = campaign_id
        return redirect(url_for("rate_view", campaign_id=campaign_id))

    return render_template(
        "annotator_landing.html",
        campaign=c,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(c)
    )

@app.route("/campaign/<campaign_id>/login")
def annotator_login_view(campaign_id):

    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

    if current_annotator():
        session["last_campaign_id"] = campaign_id
        return redirect(url_for("rate_view", campaign_id=campaign_id))

    return render_template(
        "annotator_login.html",
        campaign=c,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(c)
    )


@app.route("/campaign/<campaign_id>/forgot-password", methods=["GET", "POST"])
def annotator_forgot_password(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        user = Annotator.query.filter_by(email=email).first()

        if user:
            otp = generate_otp()
            user.set_otp(otp)
            db.session.commit()

            mailer.send_password_reset_otp(email, otp)

        flash(
            "If an account with that email exists, a verification code has been sent.",
            "success"
        )

        session["reset_email"] = email

        return redirect(
            url_for("annotator_verify_otp", campaign_id=campaign_id)
        )

    return render_template(
        "forget_password.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_forgot_password",
            campaign_id=campaign_id
        ),
        back_url=url_for(
            "annotator_login_view",
            campaign_id=campaign_id
        )
    )

@app.route("/campaign/<campaign_id>/verify-otp", methods=["GET", "POST"])
def annotator_verify_otp(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)

    email = session.get("reset_email")
    if not email:
        flash("Password reset session expired. Please start again.", "error")
        return redirect(url_for("annotator_forgot_password", campaign_id=campaign_id))

    user = Annotator.query.filter_by(email=email).first()

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not user or not user.check_otp(otp):
            flash("Invalid or expired verification code.", "error")
            return redirect(url_for("annotator_verify_otp", campaign_id=campaign_id))

        # OTP is valid
        user.clear_otp()
        db.session.commit()

        session["reset_verified"] = True

        flash("Verification successful. Please choose a new password.", "success")

        return redirect(
            url_for("annotator_reset_password", campaign_id=campaign_id)
        )

    return render_template(
        "verify_otp.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_verify_otp",
            campaign_id=campaign_id
        ),
        back_url=url_for(
            "annotator_forgot_password",
            campaign_id=campaign_id
        )
    )


@app.route("/campaign/<campaign_id>/reset-password", methods=["GET", "POST"])
def annotator_reset_password(campaign_id):

    campaign = Campaign.query.get_or_404(campaign_id)

    email = session.get("reset_email")

    if not email or not session.get("reset_verified"):
        flash("Please verify your identity first.", "error")
        return redirect(
            url_for(
                "annotator_forgot_password",
                campaign_id=campaign_id
            )
        )

    annotator = Annotator.query.filter_by(email=email).first()

    if not annotator:
        flash("Account not found.", "error")
        return redirect(
            url_for(
                "annotator_forgot_password",
                campaign_id=campaign_id
            )
        )

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")

        errors = []

        problems = password_problems(password)
        if problems:
            errors.append(
                "Password must contain " + ", ".join(problems) + "."
            )

        if password != confirm:
            errors.append("Passwords do not match.")

        if errors:
            for error in errors:
                flash(error, "error")

            return redirect(
                url_for(
                    "annotator_reset_password",
                    campaign_id=campaign_id
                )
            )

        annotator.set_password(password)
        annotator.clear_otp()

        db.session.commit()

        session.pop("reset_email", None)
        session.pop("reset_verified", None)

        flash("Password has been reset successfully.", "success")

        return redirect(
            url_for(
                "annotator_login_view",
                campaign_id=campaign_id
            )
        )

    return render_template(
        "reset_password.html",
        campaign=campaign,
        action_url=url_for(
            "annotator_reset_password",
            campaign_id=campaign_id
        )
    )


@app.route("/campaign/<campaign_id>/signup")
def annotator_signup_view(campaign_id):

    c = Campaign.query.get_or_404(campaign_id)

    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)

    return render_template(
        "annotator_signup.html",
        campaign=c,
        scripts=SCRIPT_OPTIONS,
        criteria=get_criteria_for(c)
    )

@app.route("/campaign/<campaign_id>/login", methods=["POST"])
def annotator_login_post(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("annotator_login_view", campaign_id=campaign_id))
    ann = Annotator.query.filter_by(email=email).first()
    if not ann or not ann.check_password(password):
        flash("Incorrect email or password.", "error")
        return redirect(url_for("annotator_login_view", campaign_id=campaign_id))
    session.permanent = True
    session["annotator_id"] = ann.id
    session["last_campaign_id"] = campaign_id
    return redirect(url_for("rate_view", campaign_id=campaign_id))


@app.route("/campaign/<campaign_id>/register", methods=["POST"])
def annotator_register(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    native_lang = request.form.get("native_language", "").strip()
    confirm = request.form.get("password_confirm", "")
    expertise = (request.form.get("expertise", "") or "").strip().lower()
    if expertise not in ("easy", "medium", "hard"):
        expertise = ""

    errors = []
    if not name: errors.append("Name is required.")
    if not email or not EMAIL_RE.match(email): errors.append("A valid email is required.")
    _pp = password_problems(password)
    if _pp: errors.append("Password must contain " + ", ".join(_pp) + ".")
    if password != confirm: errors.append("Passwords don't match.")
    if not request.form.get("consent"): errors.append("Please confirm the consent checkbox.")
    if c.difficulty_method != "none" and not expertise:
        errors.append("Please select your expertise level.")

    if not errors and Annotator.query.filter_by(email=email).first():
        errors.append("An account with that email already exists. Sign in instead.")

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("annotator_login_view", campaign_id=campaign_id))

    ann = Annotator(name=name, email=email, native_language=native_lang, expertise=expertise)
    ann.set_password(password)
    db.session.add(ann)
    db.session.commit()
    session.permanent = True
    session["annotator_id"] = ann.id
    session["last_campaign_id"] = campaign_id
    flash("Account created. Welcome!", "success")
    return redirect(url_for("rate_view", campaign_id=campaign_id))


@app.route("/annotator/logout", methods=["POST"])
def annotator_logout():
    last_cid = session.get("last_campaign_id")
    session.pop("annotator_id", None)
    flash("Signed out.", "info")
    # Return to the login page for the campaign they were working on, but only if
    # it still exists and is open; otherwise send them to the neutral landing page
    # (showing a closed/missing campaign here would look like an error).
    if last_cid:
        try:
            c = db.session.get(Campaign, last_cid)
        except Exception:
            c = None
        if c is not None and not c.is_closed:
            return redirect(url_for("annotator_login_view", campaign_id=last_cid))
    return redirect(url_for("landing"))


@app.route("/annotator/dashboard")
@require_annotator
def annotator_dashboard():
    ann = current_annotator()
    # Find all campaigns this annotator has ratings for
    campaign_ids = {r.campaign_id for r in
                    Rating.query.filter_by(annotator_id=ann.id).all()}
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
        crit_ids = [cr["id"] for cr in get_criteria_for(c)]
        ratings = Rating.query.filter_by(campaign_id=cid, annotator_id=ann.id).all()
        completed = sum(1 for r in ratings if c.rating_is_complete(r, get_criteria_for(c)))
        last_update = max((r.updated_at for r in ratings), default=None)
        rows.append({
            "campaign": c,
            "completed": completed,
            "total": c.num_segments,
            "last_update": last_update,
            "pct": int(completed / c.num_segments * 100) if c.num_segments else 0,
        })
    rows.sort(key=lambda r: (r["campaign"].is_closed, -r["pct"], r["campaign"].name))
    return render_template("annotator_dashboard.html", annotator=ann, rows=rows)


# ============================================================================
# Rating UI
# ============================================================================

@app.route("/campaign/<campaign_id>/rate")
@require_annotator
def rate_view(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    session["last_campaign_id"] = campaign_id
    ann = current_annotator()
    criteria = get_criteria_for(c)
    mode = c.mode
    # Load existing ratings for this (campaign, annotator)
    existing = {r.segment_id: r for r in
                Rating.query.filter_by(campaign_id=c.id, annotator_id=ann.id).all()}
    existing_payload = {
        seg_id: {"scores": r.scores_dict(), "spans": r.spans_dict(),
                 "comments": r.comments or "",
                 "preference": r.preference or "",
                 "reviewed": bool(r.reviewed),
                 "edited_text": (r.edited_text or "")}
        for seg_id, r in existing.items()
    }
    # Segments served to this annotator (after the difficulty-serving filter, if any).
    served = c.served_segments_for(ann)
    served_ids = {s.get("id") for s in served}
    completed_count = sum(1 for sid, r in existing.items()
                          if sid in served_ids and c.rating_is_complete(r, criteria))
    scale = get_scale_for(c)
    # Build a small legend for the instructions panel (works for likert + continuous).
    if scale["type"] == "likert":
        scale_legend = [{"value": v, "label": scale["labels"].get(str(v), "")}
                        for v in scale["values"]]
    else:
        scale_legend = [
            {"value": scale["min"], "label": scale.get("min_label") or "minimum"},
            {"value": scale["max"], "label": scale.get("max_label") or "maximum"},
        ]

    # For pairwise mode, attach normalized candidate info to each segment so the
    # client can render two panels without guessing the field names.
    segments = [dict(s) for s in served]
    # Difficulty is for the admin/results only — never shown to annotators, so we do not
    # attach it to the segment objects sent to the rating page.
    if mode == "pairwise":
        for s in segments:
            a, b, sa, sb = c.pairwise_candidates(s)
            s["_cand_a"], s["_cand_b"] = a, b
            s["_sys_a"], s["_sys_b"] = sa, sb

    in_label, out_label = c.io_labels()
    return render_template(
        "rate.html",
        campaign=c, annotator=ann,
        segments=segments, criteria=criteria,
        eval_mode=mode, preferences=c.preferences,
        input_label=in_label, output_label=out_label,
        is_bilingual=c.is_bilingual,
        scale=scale, scale_legend=scale_legend,
        existing=existing_payload, completed_count=completed_count,
        span_scope=c.span_scope or "target",
        enable_spans=(c.enable_spans if c.enable_spans is not None else True) if mode != "pairwise" else False,
        instructions=c.instructions or "",
        span_instructions=c.span_instructions or "",
        segments_per_page=c.segments_per_page or 3,
        ai_available=llm.is_configured(llm.effective_config(c, app.config["SECRET_KEY"])),
        ai_ab_enabled=bool(c.ai_ab_enabled),
        ai_ab_eligible={s["id"]: c.ai_ab_eligible(ann.id, s["id"]) for s in segments},
    )


@app.route("/campaign/<campaign_id>/api/config")
@require_annotator
def api_config(campaign_id):
    """Lightweight, pollable view of the annotator-visible configuration.
    The rating page polls this and re-renders itself when `version` changes,
    so admin edits show up without a manual refresh."""
    c = Campaign.query.get_or_404(campaign_id)
    ann = current_annotator()
    criteria = get_criteria_for(c)
    scale = get_scale_for(c)
    if scale["type"] == "likert":
        scale_legend = [{"value": v, "label": scale["labels"].get(str(v), "")}
                        for v in scale["values"]]
    else:
        scale_legend = [
            {"value": scale["min"], "label": scale.get("min_label") or "minimum"},
            {"value": scale["max"], "label": scale.get("max_label") or "maximum"},
        ]
    return jsonify({
        "ok": True,
        "version": c.config_fingerprint(),
        "closed": c.is_closed,
        "eval_mode": c.mode,
        "criteria": criteria,
        "scale": scale,
        "scale_legend": scale_legend,
        "preferences": c.preferences,
        "enable_spans": (c.enable_spans if c.enable_spans is not None else True) if c.mode != "pairwise" else False,
        "span_scope": c.span_scope or "target",
        "span_instructions": c.span_instructions or "",
        "segments_per_page": c.segments_per_page or 3,
        "instructions": c.instructions or "",
        "rating_position": c.rating_position or "side",
        "span_position": c.span_position or "below",
        "segments": [dict(s) for s in c.served_segments_for(ann)],
    })


@app.route("/campaign/<campaign_id>/api/submit", methods=["POST"])
@require_annotator
def api_submit(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return jsonify({"ok": False, "error": "Campaign is closed."}), 403
    ann = current_annotator()
    criteria = get_criteria_for(c)
    data = request.get_json(silent=True) or {}

    segment_id = (data.get("segment_id") or "").strip()
    scores = data.get("scores") or {}
    spans = data.get("spans") or {}
    comments = (data.get("comments") or "").strip()
    preference = (data.get("preference") or "").strip()
    reviewed = bool(data.get("reviewed"))
    edited_text = data.get("edited_text")
    if edited_text is not None:
        edited_text = str(edited_text)
    time_spent = data.get("time_spent_seconds", 0)

    seg = c.segment_by_id(segment_id)
    if not seg:
        return jsonify({"ok": False, "error": "Unknown segment_id."}), 400

    mode = c.mode
    clean_scores = {}
    cleaned = {}
    clean_pref = ""

    if mode == "likert":
        # Validate scores against this campaign's scale bounds
        lo, hi = c.score_bounds
        for crit in criteria:
            v = scores.get(crit["id"])
            if v is None or not isinstance(v, int) or v < lo or v > hi:
                return jsonify({"ok": False, "error": f"Missing or invalid rating for {crit['name']}."}), 400
            clean_scores[crit["id"]] = v

    if mode == "pairwise":
        valid_pref_ids = {p["id"] for p in c.preferences}
        if preference not in valid_pref_ids:
            return jsonify({"ok": False, "error": "Please choose a preference option."}), 400
        clean_pref = preference

    # Spans apply in likert (if enabled) and span_only modes.
    span_enabled = (c.enable_spans if c.enable_spans is not None else True)
    if mode == "pairwise":
        span_enabled = False
    if mode == "span_only":
        span_enabled = True

    if span_enabled:
        # A span item is [start, end] (target) OR [start, end, "source"/"target"] in "both" mode.
        target_len = len(seg.get("target", ""))
        source_len = len(seg.get("source", ""))
        span_scope = c.span_scope or "target"
        for crit in criteria:
            raw = spans.get(crit["id"], [])
            clean = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, list) or len(item) < 2:
                        continue
                    s, e = item[0], item[1]
                    if not (isinstance(s, int) and isinstance(e, int)):
                        continue
                    which = item[2] if (len(item) >= 3 and span_scope == "both") else "target"
                    if which == "source" and span_scope == "both":
                        if 0 <= s < e <= source_len:
                            clean.append([s, e, "source"])
                    else:
                        if 0 <= s < e <= target_len:
                            if span_scope == "both":
                                clean.append([s, e, "target"])
                            else:
                                clean.append([s, e])
            cleaned[crit["id"]] = clean

    # Upsert
    rating = Rating.query.filter_by(
        campaign_id=c.id, annotator_id=ann.id, segment_id=segment_id
    ).first()
    if not rating:
        rating = Rating(campaign_id=c.id, annotator_id=ann.id, segment_id=segment_id)
        db.session.add(rating)
    rating.scores_json = json.dumps(clean_scores)
    rating.spans_json = json.dumps(cleaned, ensure_ascii=False)
    rating.comments = comments
    rating.preference = clean_pref
    if mode == "span_only":
        rating.reviewed = reviewed
    if mode == "post_edit" and edited_text is not None:
        rating.edited_text = edited_text[:20000]
    if isinstance(time_spent, (int, float)) and time_spent > 0:
        rating.time_spent_seconds = (rating.time_spent_seconds or 0) + int(time_spent)
    rating.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"ok": True})


# ============================================================================
# AI guideline assistant (grounded, advisory-only, logged)
# ============================================================================

def task_label(campaign):
    return models.task_defaults(getattr(campaign, "task_type", "translation"))["name"]


def _assist_system_prompt():
    return (
        "You are a helpful assistant embedded in a HUMAN evaluation platform where annotators "
        "judge the quality of system outputs (such as translations, summaries, or answers). "
        "You support the annotator in two ways:\n"
        "1) COMPREHENSION: explaining the meaning of words, phrases, or whole sentences in the "
        "input or output text, clarifying terminology, idioms, grammar, register, or nuance. For "
        "this you may freely use your general knowledge of the languages involved; you are not "
        "limited to the campaign guidelines here. Translate or gloss text when asked.\n"
        "2) GUIDELINES: explaining the campaign's instructions, the evaluation criteria and their "
        "definitions, and how the rating interface works. Ground these answers in the provided "
        "GUIDELINES and INTERFACE notes, and if they don't cover something, say so rather than inventing rules.\n"
        "Boundaries: never tell the annotator which score, rating, preference, or span to choose, "
        "and never declare the output definitively correct or incorrect or whether it contains "
        "an error; explaining what the text means is fine, but the quality judgment is the human's. "
        "Be concise and clear."
    )


def _interface_help(campaign):
    mode = campaign.mode
    per = campaign.segments_per_page or 3
    in_label, out_label = campaign.io_labels()
    L = [f"The annotator works through segments page by page ({per} per page); "
         "use \"Save & next\" to continue, and progress is saved automatically."]
    if mode == "likert":
        how = {"circles": "click the numbered circle for each criterion",
               "buttons": "click the labelled button for each criterion",
               "radio": "select the radio option for each criterion",
               "stars": "click the star rating for each criterion",
               "slider": "drag the slider (or click a number) for each criterion"}.get(
                   campaign.scale_design or "circles", "record a rating for each criterion")
        L.append("This is a Likert-rating task: " + how + ".")
        if campaign.enable_spans:
            L.append("Error spans can also be marked: pick an error type, then select the words in the text.")
    elif mode == "pairwise":
        L.append(f"This is a pairwise task: two candidate {out_label.lower()} outputs (A and B) "
                 "are shown; the annotator picks one preference option comparing them.")
    elif mode == "span_only":
        L.append("This is a span-annotation task: pick an error type, select the words that "
                 "contain the error, then tick \"reviewed\" when done (even if there are no errors).")
    elif mode == "post_edit":
        L.append("This is a post-editing task: the annotator edits the output text directly to "
                 "correct any errors (leaving it unchanged is fine if it is already correct).")
    if campaign.is_bilingual:
        L.append("The human reference is hidden until the annotator chooses to reveal it, "
                 "to avoid biasing their own judgment.")
    return " ".join(L)


def _assist_user_prompt(campaign, segctx, criteria, question):
    """segctx: ordered list of {"n": int, "seg": dict, "judgment": str} for the
    segments the annotator can currently see (referenced by their number)."""
    in_label, out_label = campaign.io_labels()
    L = []
    L.append("GUIDELINES")
    if campaign.is_bilingual and campaign.source_language and campaign.target_language:
        L.append(f"Task: evaluate {campaign.source_language} → {campaign.target_language} "
                 f"{out_label.lower()} outputs ({task_label(campaign)}).")
    else:
        L.append(f"Task: {task_label(campaign)}, evaluate the {out_label.lower()} produced "
                 f"for each {in_label.lower()}.")
    if campaign.instructions:
        L.append("Instructions: " + campaign.instructions)
    mode = campaign.mode
    if mode in ("likert", "span_only") and criteria:
        L.append(("Criteria:" if mode == "likert" else "Error types:"))
        for c in criteria:
            d = c.get("guide") or c.get("desc") or models.criterion_guide(c["name"]) or ""
            L.append(f"- {c['name']}" + (f": {d}" if d else ""))
    if mode == "likert":
        sc = campaign.scale_config
        if sc.get("labels"):
            L.append("Scale labels: " + json.dumps(sc["labels"], ensure_ascii=False))
        L.append(f"Score range: {campaign.score_bounds[0]}–{campaign.score_bounds[1]}")
    if mode == "pairwise":
        L.append("Preference options: " + ", ".join(p["label"] for p in campaign.preferences))
    if campaign.span_instructions:
        L.append("Span guidance: " + campaign.span_instructions)
    L.append("")
    L.append("INTERFACE")
    L.append(_interface_help(campaign))
    L.append("")
    if segctx:
        L.append("SEGMENTS THE ANNOTATOR CAN SEE (refer to them by their number, "
                 "e.g. \"segment %d\"):" % segctx[0]["n"])
        for item in segctx:
            seg = item["seg"]
            L.append(f"--- Segment {item['n']} ---")
            L.append(f"{in_label}: " + (seg.get("source") or ""))
            if mode == "pairwise":
                a, b, _sa, _sb = campaign.pairwise_candidates(seg)
                L.append(f"{out_label} A: " + (a or ""))
                L.append(f"{out_label} B: " + (b or ""))
            else:
                L.append(f"{out_label}: " + (seg.get("target") or ""))
            if seg.get("reference"):
                L.append("Reference: " + seg.get("reference"))
            if item.get("judgment"):
                L.append("Annotator's current judgment (their own, do not override): " + item["judgment"])
        L.append("")
    L.append("QUESTION FROM THE ANNOTATOR")
    L.append(question)
    return "\n".join(L)


@app.route("/campaign/<campaign_id>/api/assist", methods=["POST"])
@require_annotator
def api_assist(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return jsonify({"ok": False, "error": "Campaign is closed."}), 403
    cfg = llm.effective_config(c, app.config["SECRET_KEY"])
    if not llm.is_configured(cfg):
        return jsonify({"ok": False, "error": "The guideline assistant isn't configured for this campaign."}), 400
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "Please enter a question."}), 400
    if len(question) > 1000:
        question = question[:1000]

    # Map every segment id to its global 1-based number (matches the UI labels).
    id_to_n = {}
    seg_by_id = {}
    for i, s in enumerate(c.segments):
        sid = s.get("id")
        if sid is not None:
            id_to_n[str(sid)] = i + 1
            seg_by_id[str(sid)] = s

    # The client tells us which segments the annotator can currently see (+ their
    # in-progress judgments). Fall back to a single focused segment_id.
    raw_segs = data.get("segments")
    if not isinstance(raw_segs, list):
        sid = (data.get("segment_id") or "").strip()
        raw_segs = [{"id": sid, "judgment": data.get("judgment") or ""}] if sid else []
    segctx = []
    seen = set()
    for item in raw_segs[:25]:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "")
        if sid not in seg_by_id or sid in seen:
            continue
        seen.add(sid)
        segctx.append({"n": id_to_n[sid], "seg": seg_by_id[sid],
                       "judgment": (str(item.get("judgment") or ""))[:300]})
    segctx.sort(key=lambda x: x["n"])
    focused_id = (data.get("segment_id") or (segctx[0]["seg"].get("id") if segctx else "")) or ""

    # Randomised AI experiment: refuse help on segments in the no-AI arm for this annotator.
    if c.ai_ab_enabled:
        ann = current_annotator()
        if focused_id and not c.ai_ab_eligible(ann.id, focused_id):
            return jsonify({"ok": False,
                            "error": "For this study, the AI assistant is turned off on this item. "
                                     "Please use your own judgment here."}), 403
        segctx = [s for s in segctx
                  if c.ai_ab_eligible(ann.id, str(s["seg"].get("id") or ""))]

    # Recent conversation turns for follow-up continuity.
    raw_hist = data.get("history") if isinstance(data.get("history"), list) else []
    history = []
    for h in raw_hist[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
            history.append({"role": h["role"], "content": str(h["content"])[:2000]})

    criteria = get_criteria_for(c)
    try:
        answer = llm.call_llm(cfg, _assist_system_prompt(),
                              _assist_user_prompt(c, segctx, criteria, question),
                              history=history, max_tokens=500)
    except llm.LLMError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    if not answer:
        return jsonify({"ok": False, "error": "The model returned an empty response."}), 502
    ann = current_annotator()
    log = AssistantLog(campaign_id=c.id, annotator_id=ann.id, segment_id=str(focused_id),
                       role="annotator", provider=cfg["provider"], model=cfg["model"],
                       question=question, answer=answer)
    db.session.add(log)
    db.session.commit()
    return jsonify({"ok": True, "answer": answer, "log_id": log.id})


@app.route("/campaign/<campaign_id>/api/assist/feedback", methods=["POST"])
@require_annotator
def api_assist_feedback(campaign_id):
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip()
    if action not in ("helpful", "dismissed", "reconsidered"):
        return jsonify({"ok": False, "error": "Unknown action."}), 400
    log = AssistantLog.query.filter_by(id=data.get("log_id"), campaign_id=campaign_id).first()
    if log:
        log.action = action
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/campaign/ai/test", methods=["POST"])
@require_admin
def admin_ai_test():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip()
    meta = llm.provider_by_id(provider)
    if not meta:
        return jsonify({"ok": False, "message": "Choose a provider first."})
    key = (data.get("api_key") or "").strip()
    # If no fresh key was typed, fall back to the campaign's stored key.
    if not key and data.get("campaign_id"):
        c = Campaign.query.get(data["campaign_id"])
        if c:
            key = llm.decrypt_secret(c.ai_api_key_enc or "", app.config["SECRET_KEY"])
    cfg = {
        "provider": provider,
        "model": (data.get("model") or "").strip() or meta["default_model"],
        "base_url": (data.get("base_url") or "").strip() or meta["default_base_url"],
        "api_key": key,
    }
    if not llm.is_configured(cfg):
        missing = []
        if not cfg["model"]:
            missing.append("model")
        if meta["needs_key"] and not cfg["api_key"]:
            missing.append("API key")
        if meta["needs_base_url"] and not cfg["base_url"]:
            missing.append("endpoint URL")
        return jsonify({"ok": False, "message": "Missing: " + ", ".join(missing)})
    ok, msg = llm.test_config(cfg)
    return jsonify({"ok": ok, "message": msg})


# ============================================================================
# Healthcheck
# ============================================================================

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "admin_configured": ADMIN_EMAIL != "admin@example.com"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
