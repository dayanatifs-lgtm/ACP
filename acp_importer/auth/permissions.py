"""Central authorization helpers for page and function access."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from .permissions_catalog import page_key_for_path
from .permissions_store import effective_grants_for_email, init_permissions_schema
from .settings import auth_enabled
from .sessions import current_user


def load_user_permissions(email: str) -> dict[str, Any]:
    init_permissions_schema()
    return effective_grants_for_email(email)


def has_page_access(perms: dict[str, Any] | None, page_key: str) -> bool:
    if not perms:
        return False
    if perms.get("isSuperAdmin"):
        return True
    pages = perms.get("pages") or {}
    return bool(pages.get(page_key))


def has_function_access(perms: dict[str, Any] | None, page_key: str, function_key: str) -> bool:
    if not perms:
        return False
    if perms.get("isSuperAdmin"):
        return True
    pages = perms.get("pages") or {}
    return function_key in (pages.get(page_key) or [])


def permissions_for_request(request: Request) -> dict[str, Any] | None:
    cached = getattr(request.state, "permissions", None)
    if cached is not None:
        return cached
    user = getattr(request.state, "user", None) or current_user(request)
    if not user:
        return None
    perms = load_user_permissions(user["email"])
    request.state.permissions = perms
    return perms


def assert_function(request: Request, page_key: str, function_key: str) -> None:
    if not auth_enabled():
        return
    perms = permissions_for_request(request)
    if not has_function_access(perms, page_key, function_key):
        raise HTTPException(status_code=403, detail=f"Missing permission: {page_key}.{function_key}")


def assert_page(request: Request, page_key: str) -> None:
    if not auth_enabled():
        return
    perms = permissions_for_request(request)
    if not has_page_access(perms, page_key):
        raise HTTPException(status_code=403, detail=f"Missing page access: {page_key}")


def resolve_api_requirement(method: str, path: str) -> tuple[str, str] | None:
    """Map an API request to (page_key, function_key), or None if not permission-gated."""
    method = method.upper()
    path = path.rstrip("/") or path

    if path.startswith("/api/auth"):
        return None
    if path.startswith("/api/workspace"):
        if method == "GET":
            return ("dashboard", "view")
        return ("dashboard", "import")
    if path.startswith("/api/admin/permission-sets"):
        return ("administration", "manage_permission_sets")
    if path.startswith("/api/admin/users"):
        return ("administration", "manage_user_permissions")
    if path == "/api/admin/catalog":
        return ("administration", "view")

    if path == "/api/connectors/test":
        return ("connectors", "test")
    if path == "/api/connectors":
        return ("connectors", "view") if method == "GET" else None
    if path.startswith("/api/connectors/"):
        if method == "GET":
            return ("connectors", "view")
        if method == "PUT":
            return ("connectors", "edit")
        if method == "DELETE":
            return ("connectors", "delete")
        return ("connectors", "view")

    if path.startswith("/api/releases"):
        return ("releases", "view")

    if path == "/api/deliveries/preview-release":
        return ("calendar", "view")
    if path == "/api/deliveries":
        if method == "GET":
            return ("calendar", "view")
        if method == "POST":
            return ("calendar", "create")
    if path.startswith("/api/deliveries/"):
        if method == "GET":
            return ("calendar", "view")
        if method == "PUT":
            return ("calendar", "edit")
        if method == "DELETE":
            return ("calendar", "delete")

    if path.startswith("/api/repackage"):
        return ("clone", "repackage")
    if path == "/api/clone/analyse":
        return ("clone", "analyse")
    if path == "/api/clone/ai-retry":
        return ("clone", "ai_retry")
    if path == "/api/clone/imports":
        return ("clone", "import")
    if path in {"/api/clone/analysis/export", "/api/clone/results/export"}:
        return ("clone", "export")
    if path.startswith("/api/clone"):
        return ("clone", "view")

    if path == "/api/imports/stop":
        return ("dashboard", "stop")
    if path == "/api/imports":
        return ("dashboard", "import")
    if path in {"/api/packages", "/api/environments", "/api/status"}:
        return ("dashboard", "view")

    return None


def check_api_permission(method: str, path: str, perms: dict[str, Any] | None) -> str | None:
    # Workspace is shared by Import and Clone users.
    if path.startswith("/api/workspace"):
        if method.upper() == "GET":
            if has_function_access(perms, "dashboard", "view") or has_function_access(perms, "clone", "view"):
                return None
            return "Missing permission: dashboard.view or clone.view"
        if (
            has_function_access(perms, "dashboard", "import")
            or has_function_access(perms, "clone", "import")
            or has_function_access(perms, "clone", "analyse")
        ):
            return None
        return "Missing permission: workspace upload"

    required = resolve_api_requirement(method, path)
    if required is None:
        return None
    page_key, function_key = required
    # Connectors PUT is used for both create and edit in this app.
    if page_key == "connectors" and function_key == "edit":
        if has_function_access(perms, "connectors", "edit") or has_function_access(perms, "connectors", "create"):
            return None
        return "Missing permission: connectors.create or connectors.edit"
    if has_function_access(perms, page_key, function_key):
        return None
    return f"Missing permission: {page_key}.{function_key}"


def page_access_denied(path: str, perms: dict[str, Any] | None) -> bool:
    page_key = page_key_for_path(path)
    if page_key is None:
        return False
    return not has_page_access(perms, page_key)
