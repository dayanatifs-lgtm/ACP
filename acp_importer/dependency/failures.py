"""Capture ACP import failures for later AI-assisted retry. No inference yet."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STORE_PATH = Path(__file__).resolve().parent.parent.parent / "import_failures.json"
_lock = threading.Lock()

_HTTP_STATUS = re.compile(r"failed \((\d{3})\)")
_MISSING_OBJECT = re.compile(
    r"(?:projection|entity(?:set)?|logical unit|\blu\b|enumeration|attribute|package)\s+['\"]([A-Za-z][A-Za-z0-9_]*)['\"]",
    re.I,
)


@dataclass
class ImportFailure:
    acp: str
    attempt: int
    timestamp: str
    http_status: int | None
    error_code: str | None
    error_message: str
    raw_response: str | None
    parsed_missing_object: str | None
    file: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ImportFailureAnalyzer:
    """Parse and store IFS clone failures. Does not create dependency edges."""

    def record(
        self,
        *,
        acp: str,
        file: str,
        message: str,
        attempt: int = 1,
        raw_response: str | None = None,
    ) -> ImportFailure:
        status_match = _HTTP_STATUS.search(message)
        missing = _MISSING_OBJECT.search(message)
        failure = ImportFailure(
            acp=acp,
            attempt=attempt,
            timestamp=datetime.now(timezone.utc).isoformat(),
            http_status=int(status_match.group(1)) if status_match else None,
            error_code=None,
            error_message=message[:4000],
            raw_response=(raw_response or message)[:4000],
            parsed_missing_object=missing.group(1) if missing else None,
            file=file,
        )
        with _lock:
            items = []
            if STORE_PATH.exists():
                try:
                    loaded = json.loads(STORE_PATH.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        items = loaded
                except (OSError, json.JSONDecodeError):
                    items = []
            items.append(asdict(failure))
            STORE_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")
        return failure

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not STORE_PATH.exists():
            return []
        try:
            items = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return items[-limit:] if isinstance(items, list) else []


failure_analyzer = ImportFailureAnalyzer()
