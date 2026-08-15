"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_url: str
    token_url: str
    client_id: str
    client_secret: str
    grant_type: str
    token_client_auth: str
    scope: str | None
    username: str | None
    password: str | None
    acp_folder: Path
    poll_seconds: float
    timeout_seconds: float
    request_timeout_seconds: float
    verify_tls: bool
    xsrf_token: str | None

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv()
        grant_type = os.getenv("IFS_GRANT_TYPE", "client_credentials").strip()
        if grant_type not in {"client_credentials", "password"}:
            raise ValueError("IFS_GRANT_TYPE must be client_credentials or password")
        username = os.getenv("IFS_USERNAME", "").strip() or None
        password = os.getenv("IFS_PASSWORD", "") or None
        if grant_type == "password" and (not username or not password):
            raise ValueError("IFS_USERNAME and IFS_PASSWORD are required for password grant")
        return cls(
            base_url=_required("IFS_BASE_URL").rstrip("/"),
            token_url=_required("IFS_TOKEN_URL"),
            client_id=_required("IFS_CLIENT_ID"),
            client_secret=_required("IFS_CLIENT_SECRET"),
            grant_type=grant_type,
            token_client_auth=os.getenv("IFS_TOKEN_CLIENT_AUTH", "basic").strip().lower(),
            scope=os.getenv("IFS_SCOPE", "").strip() or None,
            username=username,
            password=password,
            acp_folder=Path(os.getenv("IFS_ACP_FOLDER", r"C:\UpdaClones")),
            poll_seconds=float(os.getenv("IFS_POLL_SECONDS", "2")),
            timeout_seconds=float(os.getenv("IFS_TIMEOUT_SECONDS", "300")),
            request_timeout_seconds=float(os.getenv("IFS_REQUEST_TIMEOUT_SECONDS", "60")),
            verify_tls=_as_bool(os.getenv("IFS_VERIFY_TLS", "true")),
            xsrf_token=os.getenv("IFS_XSRF_TOKEN", "").strip() or None,
        )
