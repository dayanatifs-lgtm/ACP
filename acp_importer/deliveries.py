"""Local persistence and Jira matching for Delivery Calendar entries."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import requests

from .jira import JiraApiError, JiraClient, JiraSettings

STORE_PATH = Path(__file__).resolve().parent.parent / "deliveries.json"
_lock = threading.Lock()


def _parse_iso_date(value: str | None) -> date | None:
    if not value or value in {"—", "-"}:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def match_jira_release(delivery_date: date, releases: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the latest Jira release whose release date is on or before the delivery date."""
    dated: list[tuple[date, dict[str, Any]]] = []
    for release in releases:
        release_date = _parse_iso_date(str(release.get("releaseDate") or ""))
        if release_date is None or release_date > delivery_date:
            continue
        dated.append((release_date, release))
    if not dated:
        return None
    dated.sort(key=lambda item: (item[0], int(item[1]["id"]) if str(item[1].get("id", "")).isdecimal() else -1))
    return dated[-1][1]


def associate_jira_release(delivery_date: date) -> tuple[dict[str, Any] | None, str]:
    """Reuse the Jira Releases client. Failures leave the delivery unlinked."""
    try:
        settings = JiraSettings.from_environment()
        releases = JiraClient(settings).releases()
    except (ValueError, JiraApiError, OSError, requests.RequestException) as exc:
        return None, f"Jira releases could not be checked ({exc}). Delivery saved without a Jira link."
    matched = match_jira_release(delivery_date, releases)
    if matched is None:
        return None, "No Jira release with a release date on or before this delivery date."
    return {
        "id": matched["id"],
        "name": matched["name"],
        "releaseDate": matched["releaseDate"],
        "released": matched.get("released", False),
        "archived": matched.get("archived", False),
        "description": matched.get("description") or "—",
    }, f"Linked to Jira release {matched['name']}."


def _load() -> list[dict[str, Any]]:
    if not STORE_PATH.exists():
        return []
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _save(items: list[dict[str, Any]]) -> None:
    STORE_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def list_deliveries() -> list[dict[str, Any]]:
    with _lock:
        items = _load()
    return sorted(items, key=lambda item: (item.get("date") or "", item.get("name") or ""))


def get_delivery(delivery_id: str) -> dict[str, Any] | None:
    with _lock:
        return next((item for item in _load() if item.get("id") == delivery_id), None)


def upsert_delivery(payload: dict[str, Any], *, delivery_id: str | None = None) -> dict[str, Any]:
    delivery_date = _parse_iso_date(payload["date"])
    if delivery_date is None:
        raise ValueError("Delivery date must be a valid ISO date (YYYY-MM-DD).")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Delivery name is required.")
    items = [str(item).strip() for item in payload.get("items") or [] if str(item).strip()]
    jira_release, match_note = associate_jira_release(delivery_date)
    record = {
        "id": delivery_id or str(uuid.uuid4()),
        "name": name,
        "date": delivery_date.isoformat(),
        "environment": str(payload.get("environment") or "").strip() or "UAT",
        "deliveryType": str(payload.get("deliveryType") or "").strip() or "Delivery",
        "description": str(payload.get("description") or "").strip(),
        "items": items,
        "releaseVersion": jira_release["name"] if jira_release else None,
        "jiraRelease": jira_release,
        "jiraMatchNote": match_note,
    }
    with _lock:
        stored = _load()
        if delivery_id:
            if not any(item.get("id") == delivery_id for item in stored):
                raise KeyError(delivery_id)
            stored = [record if item.get("id") == delivery_id else item for item in stored]
        else:
            stored.append(record)
        _save(stored)
    return record


def delete_delivery(delivery_id: str) -> bool:
    with _lock:
        stored = _load()
        remaining = [item for item in stored if item.get("id") != delivery_id]
        if len(remaining) == len(stored):
            return False
        _save(remaining)
        return True
