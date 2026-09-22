"""Per-user import / clone / repackage run state.

Previously a single shared clone_run meant every login saw the last
clone results on the server. State is now keyed by user email.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .workspaces import sanitize_user_key, workspaces_root


@dataclass
class ImportRun:
    running: bool = False
    cancel_requested: bool = False
    phase: str = "idle"
    completed: int = 0
    total: int = 0
    dry_run: bool = False
    message: str = "Ready"
    environment: str = "UAT"
    folder: str = r"C:\UpdaClones"
    results: list[dict[str, Any]] = field(default_factory=list)


class RunSessions:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._import: dict[str, ImportRun] = {}
        self._clone: dict[str, ImportRun] = {}
        self._repack: dict[str, ImportRun] = {}
        self._analysis: dict[str, dict[str, Any] | None] = {}
        self._repack_report: dict[str, dict[str, Any] | None] = {}

    def key(self, email: str | None) -> str:
        return sanitize_user_key(email or "anonymous")

    def _clone_path(self, key: str) -> Path:
        folder = workspaces_root() / key
        folder.mkdir(parents=True, exist_ok=True)
        return folder / ".clone_state.json"

    def import_run(self, email: str | None) -> ImportRun:
        key = self.key(email)
        with self.lock:
            if key not in self._import:
                self._import[key] = ImportRun()
            return self._import[key]

    def clone_run(self, email: str | None) -> ImportRun:
        key = self.key(email)
        with self.lock:
            if key not in self._clone:
                run = ImportRun(message="Ready for dependency analysis")
                self._load_clone(key, run)
                self._clone[key] = run
            return self._clone[key]

    def clone_analysis(self, email: str | None) -> dict[str, Any] | None:
        key = self.key(email)
        with self.lock:
            self.clone_run(email)  # ensure loaded
            return self._analysis.get(key)

    def set_clone_analysis(self, email: str | None, value: dict[str, Any] | None) -> None:
        key = self.key(email)
        with self.lock:
            self._analysis[key] = value

    def repack_run(self, email: str | None) -> ImportRun:
        key = self.key(email)
        with self.lock:
            if key not in self._repack:
                self._repack[key] = ImportRun(message="Ready to repackage")
            return self._repack[key]

    def repack_report(self, email: str | None) -> dict[str, Any] | None:
        key = self.key(email)
        with self.lock:
            return self._repack_report.get(key)

    def set_repack_report(self, email: str | None, value: dict[str, Any] | None) -> None:
        key = self.key(email)
        with self.lock:
            self._repack_report[key] = value

    def user_busy(self, email: str | None) -> bool:
        with self.lock:
            return (
                self.import_run(email).running
                or self.clone_run(email).running
                or self.repack_run(email).running
            )

    def save_clone(self, email: str | None) -> None:
        key = self.key(email)
        with self.lock:
            run = self.clone_run(email)
            payload = {
                "run": asdict(run),
                "analysis": self._analysis.get(key),
            }
            payload["run"]["running"] = False
            payload["run"]["cancel_requested"] = False
            if payload["run"].get("phase") in {"starting", "importing", "analysing", "ai_retry"}:
                payload["run"]["phase"] = "idle"
            path = self._clone_path(key)
            try:
                path.write_text(json.dumps(payload), encoding="utf-8")
            except OSError:
                pass

    def _load_clone(self, key: str, run: ImportRun) -> None:
        path = self._clone_path(key)
        if not path.exists():
            self._analysis[key] = None
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._analysis[key] = None
            return
        data = payload.get("run") or {}
        run.running = False
        run.cancel_requested = False
        run.phase = "idle"
        run.completed = int(data.get("completed") or 0)
        run.total = int(data.get("total") or 0)
        run.dry_run = bool(data.get("dry_run"))
        run.message = str(data.get("message") or run.message)
        run.environment = str(data.get("environment") or run.environment)
        run.folder = str(data.get("folder") or run.folder)
        run.results = list(data.get("results") or [])
        analysis = payload.get("analysis")
        self._analysis[key] = analysis if isinstance(analysis, dict) else None


sessions = RunSessions()
