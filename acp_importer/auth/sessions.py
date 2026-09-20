"""Signed cookie sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Request, Response

from .settings import auth_settings


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def set_session_cookie(response: Response, email: str) -> None:
    settings = auth_settings()
    body = {
        "email": email,
        "exp": int(time.time()) + settings.session_days * 86400,
    }
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii")
    token = f"{raw}.{_sign(raw, settings.secret)}"
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_days * 86400,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = auth_settings()
    response.delete_cookie(settings.cookie_name, path="/")


def current_user(request: Request) -> dict[str, Any] | None:
    settings = auth_settings()
    token = request.cookies.get(settings.cookie_name)
    if not token or "." not in token:
        return None
    raw, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(raw, settings.secret)):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(body.get("exp") or 0) < int(time.time()):
        return None
    email = str(body.get("email") or "").strip().lower()
    if not email:
        return None
    return {"email": email}
