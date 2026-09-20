"""Per-user ACP package workspaces on the server.

Users upload .acp/.zip files from their computers into an isolated
folder on the host. Import/Clone then read that server-side path.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .auth.settings import PROJECT_ROOT

ALLOWED_SUFFIXES = {".acp", ".zip"}
WORKSPACE_TOKEN = "__workspace__"


def workspaces_root() -> Path:
    raw = (os.getenv("ACP_WORKSPACES_ROOT") or "").strip()
    root = Path(raw) if raw else (PROJECT_ROOT / "workspaces")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def sanitize_user_key(email: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9._-]+", "_", (email or "").strip().lower())
    return key or "anonymous"


def user_workspace(email: str) -> Path:
    path = workspaces_root() / sanitize_user_key(email)
    path.mkdir(parents=True, exist_ok=True)
    (path / "repackaged").mkdir(parents=True, exist_ok=True)
    return path.resolve()


def is_workspace_token(folder: str | None) -> bool:
    text = (folder or "").strip()
    return not text or text in {WORKSPACE_TOKEN, "@workspace"}


def path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def enforce_user_path(email: str, path: Path, *, is_super_admin: bool = False) -> Path:
    """Ensure non-admin users only use paths inside their own workspace."""
    resolved = path.expanduser().resolve()
    if is_super_admin:
        return resolved
    root = user_workspace(email)
    if resolved == root or path_is_within(resolved, root):
        return resolved
    raise ValueError(
        "That folder is outside your server workspace. "
        "Upload packages here, or ask an admin to use a shared server path."
    )


def resolve_folder_for_user(
    email: str | None,
    folder: str | None,
    *,
    is_super_admin: bool = False,
) -> Path:
    if not email:
        if folder and not is_workspace_token(folder):
            return Path(folder).expanduser().resolve()
        return Path(os.getenv("IFS_ACP_FOLDER", r"C:\UpdaClones")).expanduser().resolve()

    if is_workspace_token(folder):
        return user_workspace(email)

    return enforce_user_path(email, Path(folder.strip()), is_super_admin=is_super_admin)


def list_workspace_files(email: str) -> dict[str, Any]:
    root = user_workspace(email)
    files = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        files.append({"name": path.name, "bytes": path.stat().st_size})
    return {
        "path": str(root),
        "repackageOutput": str(root / "repackaged"),
        "files": files,
        "fileCount": len(files),
    }


def save_uploads(email: str, uploads: list[tuple[str, bytes]]) -> dict[str, Any]:
    root = user_workspace(email)
    saved: list[str] = []
    skipped: list[str] = []
    for filename, data in uploads:
        name = Path(filename or "").name
        if not name:
            continue
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            skipped.append(name)
            continue
        target = root / name
        target.write_bytes(data)
        saved.append(name)
    info = list_workspace_files(email)
    info["saved"] = saved
    info["skipped"] = skipped
    return info


def delete_workspace_file(email: str, filename: str) -> dict[str, Any]:
    root = user_workspace(email)
    name = Path(filename or "").name
    if not name:
        raise ValueError("File name is required.")
    target = (root / name).resolve()
    if not path_is_within(target, root) and target != root / name:
        raise ValueError("Invalid file name.")
    if not target.exists() or not target.is_file():
        raise ValueError("File not found in your workspace.")
    target.unlink()
    return list_workspace_files(email)
