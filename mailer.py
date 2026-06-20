"""Email helper for admin OTP verification.

Picks the first configured transport, in this order:
  1. Resend     : set RESEND_API_KEY (+ MT_EVAL_EMAIL_FROM)        [HTTP API]
  2. SendGrid   : set SENDGRID_API_KEY (+ MT_EVAL_EMAIL_FROM)      [HTTP API]
  3. SMTP       : set MT_EVAL_SMTP_HOST + MT_EVAL_SMTP_USER ...    [classic SMTP]
  4. (none)     : the message is written to the server log only.

HTTP email APIs are recommended for hosted deployments because many platforms (Render,
Railway, Fly.io, Heroku, etc.) block outbound SMTP ports. Set MT_EVAL_OTP_DEV_ECHO=1 to also
surface the code in the UI for local testing.

Env vars:
  RESEND_API_KEY                         (Resend)
  SENDGRID_API_KEY                       (SendGrid)
  MT_EVAL_EMAIL_FROM                     sender address, e.g. "Aura <noreply@yourdomain.com>"
  MT_EVAL_SMTP_HOST / _PORT (587) / _USER / _PASSWORD / _FROM / _USE_TLS (1)
"""

import os
import ssl
import json
import base64
import smtplib
import logging
import secrets
import urllib.request
import urllib.error
from email.message import EmailMessage

logger = logging.getLogger("aura.mailer")


def _from_address():
    return (os.environ.get("MT_EVAL_EMAIL_FROM")
            or os.environ.get("MT_EVAL_SMTP_FROM")
            or os.environ.get("MT_EVAL_SMTP_USER")
            or "Aura <onboarding@resend.dev>")


def smtp_configured():
    return bool(os.environ.get("MT_EVAL_SMTP_HOST") and os.environ.get("MT_EVAL_SMTP_USER"))


def active_transport():
    """Return the transport that will be used: 'resend' | 'sendgrid' | 'smtp' | None."""
    if os.environ.get("RESEND_API_KEY"):
        return "resend"
    if os.environ.get("SENDGRID_API_KEY"):
        return "sendgrid"
    if smtp_configured():
        return "smtp"
    return None


def dev_echo_enabled():
    return os.environ.get("MT_EVAL_OTP_DEV_ECHO", "").strip().lower() in ("1", "true", "yes")


def generate_otp(n_digits=6):
    return "".join(secrets.choice("0123456789") for _ in range(n_digits))


def _post_json(url, headers, payload, timeout=20):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    # Default urllib User-Agent ("Python-urllib/x.y") is frequently blocked by the
    # Cloudflare layer in front of email APIs (HTTP 403, "error code: 1010"). Send a
    # normal client UA so the request is accepted.
    req.add_header("User-Agent",
                   "Mozilla/5.0 (compatible; Aura-Mailer/1.0; +https://www.anthropic.com)")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _send_resend(to_email, subject, body, attachments=None):
    payload = {"from": _from_address(), "to": [to_email], "subject": subject, "text": body}
    if attachments:
        payload["attachments"] = [
            {"filename": fn, "content": base64.b64encode(data).decode("ascii")}
            for (fn, data, _mt) in attachments]
    status, text = _post_json(
        "https://api.resend.com/emails",
        {"Authorization": "Bearer " + os.environ["RESEND_API_KEY"]}, payload)
    if 200 <= status < 300:
        return True, "sent via Resend"
    return False, f"Resend error {status}: {text[:200]}"


def _send_sendgrid(to_email, subject, body, attachments=None):
    frm = _from_address()
    # Accept either "Name <addr>" or bare "addr"
    if "<" in frm and ">" in frm:
        name = frm.split("<")[0].strip().strip('"') or None
        addr = frm.split("<")[1].split(">")[0].strip()
    else:
        name, addr = None, frm
    sender = {"email": addr}
    if name:
        sender["name"] = name
    payload = {"personalizations": [{"to": [{"email": to_email}]}],
               "from": sender, "subject": subject,
               "content": [{"type": "text/plain", "value": body}]}
    if attachments:
        payload["attachments"] = [
            {"content": base64.b64encode(data).decode("ascii"), "filename": fn,
             "type": mt, "disposition": "attachment"}
            for (fn, data, mt) in attachments]
    status, text = _post_json(
        "https://api.sendgrid.com/v3/mail/send",
        {"Authorization": "Bearer " + os.environ["SENDGRID_API_KEY"]}, payload)
    if 200 <= status < 300:
        return True, "sent via SendGrid"
    return False, f"SendGrid error {status}: {text[:200]}"


def _send_smtp(to_email, subject, body, attachments=None):
    host = os.environ["MT_EVAL_SMTP_HOST"]
    port = int(os.environ.get("MT_EVAL_SMTP_PORT", "587"))
    user = os.environ["MT_EVAL_SMTP_USER"]
    password = os.environ.get("MT_EVAL_SMTP_PASSWORD", "")
    sender = os.environ.get("MT_EVAL_SMTP_FROM", user)
    use_tls = os.environ.get("MT_EVAL_SMTP_USE_TLS", "1").strip().lower() in ("1", "true", "yes")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body)
    for (fn, data, mt) in (attachments or []):
        maintype, _, subtype = (mt or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=fn)
    if use_tls:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            if password:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            if password:
                s.login(user, password)
            s.send_message(msg)
    return True, f"sent via SMTP ({host}:{port})"


def send_email(to_email, subject, body, attachments=None):
    """Return (ok: bool, detail: str). attachments = [(filename, bytes, mimetype), ...]."""
    transport = active_transport()
    try:
        if transport == "resend":
            return _send_resend(to_email, subject, body, attachments)
        if transport == "sendgrid":
            return _send_sendgrid(to_email, subject, body, attachments)
        if transport == "smtp":
            return _send_smtp(to_email, subject, body, attachments)
    except urllib.error.HTTPError as e:
        detail = f"{transport} HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
        logger.error("Email send failed: %s", detail)
        return False, detail
    except Exception as e:
        logger.exception("Email send failed via %s", transport)
        return False, f"{transport} error: {e}"

    logger.warning("No email transport configured; email to %s NOT sent. Subject=%r",
                   to_email, subject)
    return False, "no email transport configured (message logged to server only)"


def send_otp(to_email, code):
    subject = "Your Aura admin verification code"
    body = (f"Your Aura admin verification code is: {code}\n\n"
            "Enter this code to finish creating your admin account. "
            "It expires in 15 minutes. If you didn't request this, you can ignore this email.")
    ok, detail = send_email(to_email, subject, body)
    if not ok:
        logger.info("OTP for %s not delivered (%s)", to_email, detail)
    return ok, detail
