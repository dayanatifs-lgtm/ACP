"""Local email/password authentication for the dashboard."""

from __future__ import annotations

from . import service
from .sessions import clear_session_cookie, current_user, set_session_cookie
from .settings import auth_enabled, auth_settings

__all__ = [
    "auth_enabled",
    "auth_settings",
    "clear_session_cookie",
    "current_user",
    "service",
    "set_session_cookie",
]
