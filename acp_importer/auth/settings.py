"""Auth configuration from .env."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
USERS_DB = PROJECT_ROOT / "users.local.sqlite"
AUTH_LOG = PROJECT_ROOT / "logs" / "auth-links.log"


def _load() -> None:
    load_dotenv(ENV_FILE)


def _as_bool(value: str, default: bool = False) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool
    secret: str
    base_url: str
    cookie_name: str
    session_days: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_use_tls: bool
    allow_signup: bool


def auth_settings() -> AuthSettings:
    _load()
    secret = (os.getenv("AUTH_SECRET") or "").strip()
    if not secret:
        # Stable-enough fallback for local use; set AUTH_SECRET in production.
        secret = "acp-importer-dev-secret-change-me"
    base = (os.getenv("APP_BASE_URL") or "http://127.0.0.1:8766").strip().rstrip("/")
    return AuthSettings(
        enabled=_as_bool(os.getenv("AUTH_ENABLED", "true"), True),
        secret=secret,
        base_url=base,
        cookie_name=(os.getenv("AUTH_COOKIE_NAME") or "acp_session").strip(),
        session_days=max(1, int(os.getenv("AUTH_SESSION_DAYS", "14") or 14)),
        smtp_host=(os.getenv("SMTP_HOST") or "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587") or 587),
        smtp_user=(os.getenv("SMTP_USER") or "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD") or "",
        smtp_from=(os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "noreply@localhost").strip(),
        smtp_use_tls=_as_bool(os.getenv("SMTP_USE_TLS", "true"), True),
        allow_signup=_as_bool(os.getenv("AUTH_ALLOW_SIGNUP", "true"), True),
    )


def auth_enabled() -> bool:
    return auth_settings().enabled


def new_token() -> str:
    return secrets.token_urlsafe(32)
