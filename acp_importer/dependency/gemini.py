"""Gemini client for Direct ACP Clone AI retry. Deterministic clone analysis stays unchanged."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

LOG = logging.getLogger("AI Retry")

DEFAULT_MODEL = "gemini-flash-lite-latest"
DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FALLBACK_MODEL = "gemini-flash-lite-latest"
# These aliases currently hang on generateContent for new AI Studio keys.
UNRELIABLE_MODELS = {"gemini-flash-latest", "gemini-3.6-flash"}
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _read_dotenv_value(name: str) -> str:
    if _ENV_FILE.exists():
        try:
            text = _ENV_FILE.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw = stripped.partition("=")
            if key.strip() == name:
                value = raw.strip().strip('"').strip("'")
                if value:
                    return value
    load_dotenv(_ENV_FILE)
    return (os.getenv(name) or "").strip()


def _as_bool(value: str, default: bool = True) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def gemini_settings() -> dict[str, str]:
    return {
        "api_key": _read_dotenv_value("GEMINI_API_KEY"),
        "model": _read_dotenv_value("GEMINI_MODEL") or DEFAULT_MODEL,
        "url": _read_dotenv_value("GEMINI_API_URL"),
        "ca_bundle": _read_dotenv_value("GEMINI_CA_BUNDLE"),
        "verify_tls": "true" if _as_bool(_read_dotenv_value("GEMINI_VERIFY_TLS"), True) else "false",
        "env_file": str(_ENV_FILE),
    }


def gemini_configured() -> bool:
    return bool(gemini_settings()["api_key"])


class GeminiError(RuntimeError):
    """The Gemini request failed or returned unusable content."""


def _ssl_verify(settings: dict[str, str]) -> bool | str:
    """Match Jira: trust Windows CA store, optional company CA, or disable verify."""
    ca_bundle = (settings.get("ca_bundle") or "").strip()
    if ca_bundle:
        if not Path(ca_bundle).is_file():
            raise GeminiError(
                "GEMINI_CA_BUNDLE does not point to a certificate file. "
                "Clear it, or provide your company CA .pem path."
            )
        return ca_bundle
    if not _as_bool(settings.get("verify_tls") or "", True):
        return False
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    return True


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        raise GeminiError("Gemini response did not contain a JSON object")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise GeminiError("Gemini JSON was not an object")
    return payload


def _request_error_message(model: str, exc: Exception) -> str:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "SSLCertVerificationError" in text:
        return (
            f"Gemini TLS verification failed ({model}). "
            "Corporate SSL inspection is likely. Set GEMINI_CA_BUNDLE to your company CA .pem, "
            "or temporarily GEMINI_VERIFY_TLS=false in .env, then restart the dashboard. "
            f"{exc}"
        )
    return (
        f"Gemini timed out or could not be reached ({model}). "
        f"If this continues, set GEMINI_MODEL={FALLBACK_MODEL} in .env. {exc}"
    )


def generate_json(prompt: str, *, timeout: float = 90) -> dict[str, Any]:
    settings = gemini_settings()
    if not settings["api_key"]:
        raise GeminiError("GEMINI_API_KEY is not configured. Add it to .env and restart the dashboard.")
    verify = _ssl_verify(settings)
    requested = settings["model"] or DEFAULT_MODEL
    if not settings["url"] and requested in UNRELIABLE_MODELS:
        LOG.warning("[AI Retry] %s hangs on generateContent; using %s", requested, FALLBACK_MODEL)
        requested = FALLBACK_MODEL
    models = [requested]
    if not settings["url"] and FALLBACK_MODEL not in models:
        models.append(FALLBACK_MODEL)
    last_error: GeminiError | None = None
    for model in models:
        url = settings["url"] or DEFAULT_URL.format(model=model)
        try:
            response = requests.post(
                url,
                headers={"Content-Type": "application/json", "X-goog-api-key": settings["api_key"]},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0, "maxOutputTokens": 2048},
                },
                timeout=(10, timeout),
                verify=verify,
            )
        except requests.RequestException as exc:
            last_error = GeminiError(_request_error_message(model, exc))
            LOG.warning("%s", last_error)
            continue
        if response.status_code >= 400:
            last_error = GeminiError(
                f"Gemini request failed ({response.status_code}) for {model}: {response.text[:500]}"
            )
            LOG.warning("%s", last_error)
            continue
        body = response.json()
        parts = (((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "".join(str(part.get("text") or "") for part in parts)
        if not text.strip():
            last_error = GeminiError(f"Gemini returned an empty response ({model})")
            continue
        if model != settings["model"]:
            LOG.info("[AI Retry] Fell back to Gemini model %s", model)
        else:
            LOG.info("[AI Retry] Gemini suggestion received from %s", model)
        return extract_json_object(text)
    raise last_error or GeminiError("Gemini request failed")
