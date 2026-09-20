"""Auth use-cases: register, verify, login, forgot/reset password."""

from __future__ import annotations

import re
from typing import Any

from . import store
from .mailer import send_email
from .passwords import hash_password, verify_password
from .settings import auth_settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str) -> str:
    cleaned = store.normalize_email(email)
    if not EMAIL_RE.match(cleaned):
        raise ValueError("Enter a valid email address")
    return cleaned


def _public_base(settings, public_base_url: str | None = None) -> str:
    base = (public_base_url or "").strip().rstrip("/")
    if base:
        return base
    return settings.base_url.rstrip("/")


def register(email: str, *, public_base_url: str | None = None) -> dict[str, Any]:
    settings = auth_settings()
    if not settings.allow_signup and store.user_count() > 0:
        raise ValueError("Self-signup is disabled. Ask an administrator to invite you.")
    email = _valid_email(email)
    store.create_unverified_user(email)
    token = store.create_token(email, "verify", hours=24)
    link = f"{_public_base(settings, public_base_url)}/set-password?token={token}&purpose=verify"
    delivery = send_email(
        settings,
        to_email=email,
        subject="Verify your ACP Importer email",
        body=(
            "Welcome to ACP Importer.\n\n"
            "Click this one-time link to verify your email and set your password:\n"
            f"{link}\n\n"
            "This link expires in 24 hours.\n"
        ),
    )
    result = {
        "ok": True,
        "message": "Check your email for a one-time verification link to set your password.",
        "email": email,
    }
    if delivery.get("delivery") == "log":
        reason = delivery.get("reason") or "SMTP is not configured"
        result["message"] = (
            "Could not send email via SMTP "
            f"({reason}). A one-time link was saved to {delivery['path']}."
        )
        result["debug_link"] = link
    return result


def request_password_reset(email: str, *, public_base_url: str | None = None) -> dict[str, Any]:
    settings = auth_settings()
    email = _valid_email(email)
    user = store.get_user(email)
    # Always return a generic message to avoid account enumeration.
    generic = {
        "ok": True,
        "message": "If that email exists, a one-time reset link has been sent.",
        "email": email,
    }
    if not user or not user.get("is_verified") or not user.get("password_hash"):
        return generic
    token = store.create_token(email, "reset", hours=2)
    link = f"{_public_base(settings, public_base_url)}/set-password?token={token}&purpose=reset"
    delivery = send_email(
        settings,
        to_email=email,
        subject="Reset your ACP Importer password",
        body=(
            "You requested a password reset for ACP Importer.\n\n"
            "Click this one-time link to choose a new password:\n"
            f"{link}\n\n"
            "This link expires in 2 hours. If you did not request this, ignore the email.\n"
        ),
    )
    if delivery.get("delivery") == "log":
        reason = delivery.get("reason") or "SMTP is not configured"
        generic["message"] = (
            "If that email exists, a reset link was prepared. "
            f"Email send failed ({reason}); link saved to {delivery['path']}."
        )
        generic["debug_link"] = link
    return generic


def set_password_with_token(*, token: str, purpose: str, password: str, confirm: str) -> dict[str, Any]:
    if purpose not in {"verify", "reset"}:
        raise ValueError("Invalid password link")
    if password != confirm:
        raise ValueError("Passwords do not match")
    email = store.consume_token(token, purpose)
    password_hash = hash_password(password)
    if purpose == "verify":
        store.create_unverified_user(email)
    elif not store.get_user(email):
        raise ValueError("Unknown account")
    store.set_password(email, password_hash, mark_verified=True)
    if purpose == "verify":
        from .permissions_store import ensure_bootstrap_admin

        ensure_bootstrap_admin(email)
    return {
        "ok": True,
        "email": email,
        "message": "Password saved. You can sign in now.",
    }


def login(email: str, password: str) -> dict[str, Any]:
    email = _valid_email(email)
    user = store.get_user(email)
    if not user or not user.get("password_hash") or not user.get("is_verified"):
        raise ValueError("Invalid email or password")
    if not verify_password(password, str(user["password_hash"])):
        raise ValueError("Invalid email or password")
    return {"ok": True, "email": email}


def peek_token(token: str, purpose: str) -> dict[str, Any]:
    import time

    row = store.get_token_row(token, purpose)
    if row is None:
        raise ValueError("This link is invalid or has already been used.")
    if row.get("used_at") is not None:
        raise ValueError("This link has already been used.")
    if float(row["expires_at"]) < time.time():
        raise ValueError("This link has expired. Please request a new one.")
    return {"email": str(row["email"]), "purpose": purpose}
