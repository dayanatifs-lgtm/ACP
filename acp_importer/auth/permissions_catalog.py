"""Extensible catalog of pages and functions for permission grants.

Page/function keys are stable identifiers used in the DB and APIs.
Add new entries here when new screens or actions are introduced.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FunctionDef:
    key: str
    label: str


@dataclass(frozen=True)
class PageDef:
    key: str
    label: str
    path_prefixes: tuple[str, ...]
    functions: tuple[FunctionDef, ...]


PAGES: tuple[PageDef, ...] = (
    PageDef(
        key="dashboard",
        label="ACP Importer",
        path_prefixes=("/",),
        functions=(
            FunctionDef("view", "View"),
            FunctionDef("import", "Run import"),
            FunctionDef("stop", "Stop import"),
        ),
    ),
    PageDef(
        key="clone",
        label="ACP Deploy",
        path_prefixes=("/clone",),
        functions=(
            FunctionDef("view", "View"),
            FunctionDef("analyse", "Analyse"),
            FunctionDef("import", "Import"),
            FunctionDef("export", "Export"),
            FunctionDef("ai_retry", "AI retry"),
            FunctionDef("repackage", "Repackage"),
        ),
    ),
    PageDef(
        key="calendar",
        label="Delivery Calendar",
        path_prefixes=("/calendar",),
        functions=(
            FunctionDef("view", "View"),
            FunctionDef("create", "Create"),
            FunctionDef("edit", "Edit"),
            FunctionDef("delete", "Delete"),
        ),
    ),
    PageDef(
        key="releases",
        label="Jira Releases",
        path_prefixes=("/releases",),
        functions=(
            FunctionDef("view", "View"),
        ),
    ),
    PageDef(
        key="connectors",
        label="Connectors",
        path_prefixes=("/connectors",),
        functions=(
            FunctionDef("view", "View"),
            FunctionDef("create", "Create"),
            FunctionDef("edit", "Edit"),
            FunctionDef("delete", "Delete"),
            FunctionDef("test", "Test connection"),
        ),
    ),
    PageDef(
        key="db_sync",
        label="DB Sync",
        path_prefixes=("/db-sync",),
        functions=(
            FunctionDef("view", "View"),
            FunctionDef("compare", "Compare schemas"),
            FunctionDef("sync", "Run sync"),
            FunctionDef("alter_schema", "Add missing columns"),
        ),
    ),
    PageDef(
        key="administration",
        label="Administration",
        path_prefixes=("/admin",),
        functions=(
            FunctionDef("view", "View"),
            FunctionDef("manage_permission_sets", "Manage permission sets"),
            FunctionDef("manage_user_permissions", "Manage user permissions"),
        ),
    ),
)

PAGE_BY_KEY = {page.key: page for page in PAGES}


def catalog_as_dict() -> list[dict]:
    return [
        {
            "key": page.key,
            "label": page.label,
            "pathPrefixes": list(page.path_prefixes),
            "functions": [{"key": fn.key, "label": fn.label} for fn in page.functions],
        }
        for page in PAGES
    ]


def all_grants() -> list[tuple[str, str]]:
    return [(page.key, fn.key) for page in PAGES for fn in page.functions]


def page_key_for_path(path: str) -> str | None:
    """Map a request path to a page key. Longest prefix wins; `/` is exact-only."""
    if not path:
        return None
    # Exact root
    if path == "/":
        return "dashboard"
    best: tuple[int, str] | None = None
    for page in PAGES:
        for prefix in page.path_prefixes:
            if prefix == "/":
                continue
            if path == prefix or path.startswith(prefix + "/"):
                score = len(prefix)
                if best is None or score > best[0]:
                    best = (score, page.key)
    return best[1] if best else None


def is_valid_grant(page_key: str, function_key: str) -> bool:
    page = PAGE_BY_KEY.get(page_key)
    if not page:
        return False
    return any(fn.key == function_key for fn in page.functions)
