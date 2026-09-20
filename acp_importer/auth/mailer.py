"""SMTP mailer with file fallback when SMTP is unavailable."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .settings import AUTH_LOG, AuthSettings

LOG = logging.getLogger("Auth")


def _write_log(to_email: str, subject: str, body: str) -> str:
    AUTH_LOG.parent.mkdir(parents=True, exist_ok=True)
    previous = AUTH_LOG.read_text(encoding="utf-8") if AUTH_LOG.exists() else ""
    AUTH_LOG.write_text(previous + f"\n---\nTO: {to_email}\nSUBJECT: {subject}\n{body}\n", encoding="utf-8")
    return str(AUTH_LOG)


def send_email(settings: AuthSettings, *, to_email: str, subject: str, body: str) -> dict[str, str]:
    if not settings.smtp_host:
        path = _write_log(to_email, subject, body)
        LOG.warning("SMTP_HOST not set; wrote auth link for %s to %s", to_email, path)
        return {"delivery": "log", "path": path, "reason": "SMTP_HOST is not set"}

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            if settings.smtp_use_tls:
                smtp.starttls()
                smtp.ehlo()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
        return {"delivery": "smtp"}
    except Exception as exc:
        path = _write_log(to_email, subject, body)
        LOG.warning("SMTP send failed (%s); wrote auth link for %s to %s", exc, to_email, path)
        return {
            "delivery": "log",
            "path": path,
            "reason": str(exc),
        }
