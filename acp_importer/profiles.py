"""Local, named IFS environment profiles / connectors for the dashboard."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import Settings

DEFAULT_ENVIRONMENT = "Default (.env)"
PROJECT_ROOT = Path(__file__).parent.parent
PROFILE_FILE = PROJECT_ROOT / "environments.local.json"
SECRET_FIELDS = ("client_secret", "password", "xsrf_token")
MASK = "********"
PROFILE_FIELDS = (
    "base_url",
    "token_url",
    "client_id",
    "client_secret",
    "grant_type",
    "token_client_auth",
    "scope",
    "username",
    "password",
    "xsrf_token",
    "use_env_proxy",
    "verify_tls",
    "poll_seconds",
    "timeout_seconds",
    "request_timeout_seconds",
    "acp_folder",
)
NAME_RE = re.compile(r"^[\w .()\-]{1,80}$")


def _profiles() -> dict[str, dict[str, Any]]:
    if not PROFILE_FILE.exists():
        return {}
    try:
        document = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        environments = document.get("environments", {})
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {PROFILE_FILE.name}: {exc.msg}") from exc
    if not isinstance(environments, dict):
        raise ValueError(f"{PROFILE_FILE.name} must contain an 'environments' object")
    return {
        name: values
        for name, values in environments.items()
        if isinstance(name, str) and isinstance(values, dict)
    }


def _write_profiles(profiles: dict[str, dict[str, Any]]) -> None:
    PROFILE_FILE.write_text(
        json.dumps({"environments": profiles}, indent=2) + "\n",
        encoding="utf-8",
    )


def environment_names() -> list[str]:
    return [DEFAULT_ENVIRONMENT, *sorted(_profiles(), key=str.lower)]


def settings_for(environment: str | None, folder: str | None, *, require_auth: bool) -> Settings:
    settings = Settings.from_environment(require_auth=False)
    if environment and environment != DEFAULT_ENVIRONMENT:
        profile = _profiles().get(environment)
        if profile is None:
            raise ValueError(f"Unknown environment profile: {environment}")
        overrides: dict[str, Any] = {}
        for field in (
            "base_url",
            "token_url",
            "client_id",
            "client_secret",
            "grant_type",
            "token_client_auth",
            "username",
            "password",
            "xsrf_token",
            "use_env_proxy",
            "verify_tls",
            "poll_seconds",
            "timeout_seconds",
            "request_timeout_seconds",
        ):
            if field in profile:
                overrides[field] = profile[field]
        if "scope" in profile:
            overrides["scope"] = profile["scope"] or None
        if "acp_folder" in profile:
            overrides["acp_folder"] = Path(profile["acp_folder"])
        settings = replace(settings, **overrides)

    if folder and folder.strip():
        settings = replace(settings, acp_folder=Path(folder.strip()))

    if settings.grant_type not in {"client_credentials", "password"}:
        raise ValueError("grant_type must be client_credentials or password")
    if require_auth:
        for field in ("base_url", "token_url", "client_id", "client_secret"):
            if not getattr(settings, field):
                raise ValueError(f"Environment is missing {field}")
        if settings.grant_type == "password" and (not settings.username or not settings.password):
            raise ValueError("Password grant requires username and password")
    return settings


def _mask_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    row = {
        "name": name,
        "connectorType": str(profile.get("connector_type") or "IFS"),
        "base_url": str(profile.get("base_url") or ""),
        "token_url": str(profile.get("token_url") or ""),
        "client_id": str(profile.get("client_id") or ""),
        "grant_type": str(profile.get("grant_type") or "password"),
        "token_client_auth": str(profile.get("token_client_auth") or "basic"),
        "scope": str(profile.get("scope") or ""),
        "username": str(profile.get("username") or ""),
        "acp_folder": str(profile.get("acp_folder") or r"C:\UpdaClones"),
        "verify_tls": bool(profile.get("verify_tls", True)),
        "use_env_proxy": bool(profile.get("use_env_proxy", False)),
        "poll_seconds": float(profile.get("poll_seconds", 2)),
        "timeout_seconds": float(profile.get("timeout_seconds", 300)),
        "request_timeout_seconds": float(profile.get("request_timeout_seconds", 60)),
        "has_client_secret": bool(str(profile.get("client_secret") or "").strip()),
        "has_password": bool(str(profile.get("password") or "").strip()),
        "has_xsrf_token": bool(str(profile.get("xsrf_token") or "").strip()),
        "client_secret": MASK if str(profile.get("client_secret") or "").strip() else "",
        "password": MASK if str(profile.get("password") or "").strip() else "",
        "xsrf_token": MASK if str(profile.get("xsrf_token") or "").strip() else "",
    }
    return row


def list_connectors() -> list[dict[str, Any]]:
    profiles = _profiles()
    return [_mask_profile(name, profile) for name, profile in sorted(profiles.items(), key=lambda item: item[0].lower())]


def get_connector(name: str) -> dict[str, Any]:
    profile = _profiles().get(name)
    if profile is None:
        raise ValueError(f"Unknown connector: {name}")
    return _mask_profile(name, profile)


def _clean_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or not NAME_RE.match(cleaned):
        raise ValueError("Connector name must be 1–80 characters (letters, numbers, spaces, . _ - ()).")
    if cleaned == DEFAULT_ENVIRONMENT:
        raise ValueError(f'"{DEFAULT_ENVIRONMENT}" is reserved for the local .env profile.')
    return cleaned


def _secret_value(incoming: Any, existing: Any) -> str:
    text = "" if incoming is None else str(incoming)
    if not text.strip() or text.strip() == MASK:
        return "" if existing is None else str(existing)
    return text


def _normalize_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    grant_type = str(payload.get("grant_type") or existing.get("grant_type") or "password").strip()
    if grant_type not in {"client_credentials", "password"}:
        raise ValueError("grant_type must be client_credentials or password")
    token_client_auth = str(payload.get("token_client_auth") or existing.get("token_client_auth") or "basic").strip().lower()
    if token_client_auth not in {"basic", "body"}:
        raise ValueError("token_client_auth must be basic or body")

    base_url = str(payload.get("base_url") or "").strip().rstrip("/")
    token_url = str(payload.get("token_url") or "").strip()
    client_id = str(payload.get("client_id") or "").strip()
    if not base_url or not token_url or not client_id:
        raise ValueError("base_url, token_url, and client_id are required")

    client_secret = _secret_value(payload.get("client_secret"), existing.get("client_secret"))
    password = _secret_value(payload.get("password"), existing.get("password"))
    xsrf_token = _secret_value(payload.get("xsrf_token"), existing.get("xsrf_token"))
    if not client_secret:
        raise ValueError("client_secret is required")
    username = str(payload.get("username") or "").strip() or None
    if grant_type == "password" and (not username or not password):
        raise ValueError("Password grant requires username and password")

    def as_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    return {
        "connector_type": str(payload.get("connectorType") or payload.get("connector_type") or "IFS").strip() or "IFS",
        "base_url": base_url,
        "token_url": token_url,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": grant_type,
        "token_client_auth": token_client_auth,
        "scope": str(payload.get("scope") or "").strip(),
        "username": username or "",
        "password": password,
        "xsrf_token": xsrf_token,
        "acp_folder": str(payload.get("acp_folder") or existing.get("acp_folder") or r"C:\UpdaClones"),
        "verify_tls": as_bool(payload.get("verify_tls"), bool(existing.get("verify_tls", True))),
        "use_env_proxy": as_bool(payload.get("use_env_proxy"), bool(existing.get("use_env_proxy", False))),
        "poll_seconds": float(payload.get("poll_seconds", existing.get("poll_seconds", 2))),
        "timeout_seconds": float(payload.get("timeout_seconds", existing.get("timeout_seconds", 300))),
        "request_timeout_seconds": float(
            payload.get("request_timeout_seconds", existing.get("request_timeout_seconds", 60))
        ),
    }


def upsert_connector(name: str, payload: dict[str, Any], *, rename_from: str | None = None) -> dict[str, Any]:
    cleaned = _clean_name(name)
    profiles = _profiles()
    source_name = rename_from or cleaned
    existing = profiles.get(source_name) if source_name in profiles else profiles.get(cleaned)
    if rename_from and rename_from != cleaned:
        if cleaned in profiles:
            raise ValueError(f"A connector named {cleaned!r} already exists")
        if rename_from not in profiles:
            raise ValueError(f"Unknown connector: {rename_from}")
    profile = _normalize_payload(payload, existing=existing)
    if rename_from and rename_from in profiles and rename_from != cleaned:
        del profiles[rename_from]
    profiles[cleaned] = profile
    _write_profiles(profiles)
    return _mask_profile(cleaned, profile)


def delete_connector(name: str) -> None:
    profiles = _profiles()
    if name not in profiles:
        raise ValueError(f"Unknown connector: {name}")
    del profiles[name]
    _write_profiles(profiles)


def connector_settings(name: str) -> Settings:
    return settings_for(name, None, require_auth=True)


def test_connector_payload(payload: dict[str, Any], *, existing_name: str | None = None) -> dict[str, Any]:
    """Build settings from an unsaved form payload and return them for authentication testing."""
    existing = _profiles().get(existing_name or "") if existing_name else None
    profile = _normalize_payload(payload, existing=existing)
    settings = Settings.from_environment(require_auth=False)
    overrides = {
        "base_url": profile["base_url"],
        "token_url": profile["token_url"],
        "client_id": profile["client_id"],
        "client_secret": profile["client_secret"],
        "grant_type": profile["grant_type"],
        "token_client_auth": profile["token_client_auth"],
        "scope": profile["scope"] or None,
        "username": profile["username"] or None,
        "password": profile["password"] or None,
        "xsrf_token": profile["xsrf_token"] or None,
        "acp_folder": Path(profile["acp_folder"]),
        "verify_tls": profile["verify_tls"],
        "use_env_proxy": profile["use_env_proxy"],
        "poll_seconds": profile["poll_seconds"],
        "timeout_seconds": profile["timeout_seconds"],
        "request_timeout_seconds": profile["request_timeout_seconds"],
    }
    return replace(settings, **overrides)
