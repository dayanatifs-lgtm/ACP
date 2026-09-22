"""SQLite persistence for permission sets, grants, and user assignments."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import store
from .permissions_catalog import all_grants, is_valid_grant
from .settings import USERS_DB
from .store import _db, init_db, normalize_email


FULL_ACCESS_SET_NAME = "Full Access"
DEFAULT_PERMISSION_SET_NAME = "Default"


def init_permissions_schema(db_path: Path | None = None) -> None:
    init_db(db_path)
    with _db(db_path) as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_super_admin" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_super_admin INTEGER NOT NULL DEFAULT 0")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS permission_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                created_by TEXT,
                updated_by TEXT
            );
            CREATE TABLE IF NOT EXISTS permission_set_grants (
                permission_set_id INTEGER NOT NULL,
                page_key TEXT NOT NULL,
                function_key TEXT NOT NULL,
                PRIMARY KEY (permission_set_id, page_key, function_key),
                FOREIGN KEY (permission_set_id) REFERENCES permission_sets(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_permission_sets (
                user_id INTEGER NOT NULL,
                permission_set_id INTEGER NOT NULL,
                assigned_at REAL NOT NULL,
                assigned_by TEXT,
                PRIMARY KEY (user_id, permission_set_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (permission_set_id) REFERENCES permission_sets(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS permission_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_email TEXT,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                detail TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ps_grants_set ON permission_set_grants(permission_set_id);
            CREATE INDEX IF NOT EXISTS idx_ups_user ON user_permission_sets(user_id);
            CREATE INDEX IF NOT EXISTS idx_ups_set ON user_permission_sets(permission_set_id);
            """
        )
    _bootstrap(db_path)


def _audit(
    conn,
    *,
    actor_email: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO permission_audit (actor_email, action, entity_type, entity_id, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            normalize_email(actor_email) if actor_email else None,
            action,
            entity_type,
            entity_id,
            json.dumps(detail or {}, separators=(",", ":")),
            time.time(),
        ),
    )


def _bootstrap(db_path: Path | None = None) -> None:
    """Ensure Full Access set exists and existing users are not locked out."""
    now = time.time()
    with _db(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM permission_sets WHERE name = ? COLLATE NOCASE",
            (FULL_ACCESS_SET_NAME,),
        ).fetchone()
        created_full_access = False
        if row is None:
            created_full_access = True
            cur = conn.execute(
                """
                INSERT INTO permission_sets (name, description, is_active, created_at, updated_at, created_by, updated_by)
                VALUES (?, ?, 1, ?, ?, 'system', 'system')
                """,
                (
                    FULL_ACCESS_SET_NAME,
                    "All pages and functions. Assigned automatically to existing users during migration.",
                    now,
                    now,
                ),
            )
            set_id = int(cur.lastrowid)
            for page_key, function_key in all_grants():
                conn.execute(
                    "INSERT INTO permission_set_grants (permission_set_id, page_key, function_key) VALUES (?, ?, ?)",
                    (set_id, page_key, function_key),
                )
            _audit(
                conn,
                actor_email="system",
                action="create",
                entity_type="permission_set",
                entity_id=str(set_id),
                detail={"name": FULL_ACCESS_SET_NAME, "bootstrap": True},
            )
        else:
            set_id = int(row["id"])
            # Keep Full Access grants in sync with catalog additions.
            existing = {
                (r["page_key"], r["function_key"])
                for r in conn.execute(
                    "SELECT page_key, function_key FROM permission_set_grants WHERE permission_set_id = ?",
                    (set_id,),
                ).fetchall()
            }
            for page_key, function_key in all_grants():
                if (page_key, function_key) not in existing:
                    conn.execute(
                        "INSERT INTO permission_set_grants (permission_set_id, page_key, function_key) VALUES (?, ?, ?)",
                        (set_id, page_key, function_key),
                    )

        verified = conn.execute(
            "SELECT id, email, is_super_admin FROM users WHERE is_verified = 1 AND password_hash IS NOT NULL"
        ).fetchall()
        if not verified:
            return

        # Promote / assign only during the initial permission-system migration.
        if not created_full_access:
            return

        has_super = any(int(u["is_super_admin"] or 0) for u in verified)
        if not has_super:
            first = verified[0]
            conn.execute("UPDATE users SET is_super_admin = 1, updated_at = ? WHERE id = ?", (now, first["id"]))
            _audit(
                conn,
                actor_email="system",
                action="promote_super_admin",
                entity_type="user",
                entity_id=str(first["id"]),
                detail={"email": first["email"]},
            )

        for user in verified:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_permission_sets (user_id, permission_set_id, assigned_at, assigned_by)
                VALUES (?, ?, ?, 'system')
                """,
                (user["id"], set_id, now),
            )
            _audit(
                conn,
                actor_email="system",
                action="assign",
                entity_type="user_permission_set",
                entity_id=f"{user['id']}:{set_id}",
                detail={"email": user["email"], "permission_set_id": set_id, "bootstrap": True},
            )


def ensure_bootstrap_admin(email: str, db_path: Path | None = None) -> None:
    """If no super-admin exists yet, promote this user and grant Full Access."""
    init_permissions_schema(db_path)
    email = normalize_email(email)
    user = store.get_user(email, db_path)
    if not user or not user.get("is_verified"):
        return
    now = time.time()
    with _db(db_path) as conn:
        super_row = conn.execute(
            "SELECT id FROM users WHERE is_super_admin = 1 LIMIT 1"
        ).fetchone()
        if super_row:
            return
        conn.execute(
            "UPDATE users SET is_super_admin = 1, updated_at = ? WHERE id = ?",
            (now, user["id"]),
        )
        full = conn.execute(
            "SELECT id FROM permission_sets WHERE name = ? COLLATE NOCASE",
            (FULL_ACCESS_SET_NAME,),
        ).fetchone()
        if full:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_permission_sets (user_id, permission_set_id, assigned_at, assigned_by)
                VALUES (?, ?, ?, 'system')
                """,
                (user["id"], full["id"], now),
            )
        _audit(
            conn,
            actor_email="system",
            action="promote_super_admin",
            entity_type="user",
            entity_id=str(user["id"]),
            detail={"email": email, "reason": "first_verified_user"},
        )


def ensure_default_permissions(email: str, db_path: Path | None = None) -> bool:
    """Assign the active 'Default' set when the user has no permission sets yet.

    Returns True if Default was assigned (or already present after ensure).
    """
    init_permissions_schema(db_path)
    email = normalize_email(email)
    user = store.get_user(email, db_path)
    if not user or not user.get("is_verified"):
        return False
    now = time.time()
    with _db(db_path) as conn:
        assigned = conn.execute(
            "SELECT COUNT(*) AS c FROM user_permission_sets WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
        if assigned and int(assigned["c"] or 0) > 0:
            return False
        default_row = conn.execute(
            """
            SELECT id FROM permission_sets
            WHERE name = ? COLLATE NOCASE AND is_active = 1
            """,
            (DEFAULT_PERMISSION_SET_NAME,),
        ).fetchone()
        if not default_row:
            return False
        set_id = int(default_row["id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO user_permission_sets (user_id, permission_set_id, assigned_at, assigned_by)
            VALUES (?, ?, ?, 'system')
            """,
            (user["id"], set_id, now),
        )
        _audit(
            conn,
            actor_email="system",
            action="assign",
            entity_type="user_permission_set",
            entity_id=f"{user['id']}:{set_id}",
            detail={
                "email": email,
                "permission_set_id": set_id,
                "reason": "default_on_first_access",
            },
        )
        return True


def list_permission_sets(db_path: Path | None = None) -> list[dict[str, Any]]:
    init_permissions_schema(db_path)
    with _db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ps.*,
                   (SELECT COUNT(*) FROM permission_set_grants g WHERE g.permission_set_id = ps.id) AS grant_count,
                   (SELECT COUNT(*) FROM user_permission_sets u WHERE u.permission_set_id = ps.id) AS user_count
            FROM permission_sets ps
            ORDER BY ps.name COLLATE NOCASE
            """
        ).fetchall()
        return [_set_row(r) for r in rows]


def get_permission_set(set_id: int, db_path: Path | None = None) -> dict[str, Any] | None:
    init_permissions_schema(db_path)
    with _db(db_path) as conn:
        row = conn.execute("SELECT * FROM permission_sets WHERE id = ?", (set_id,)).fetchone()
        if not row:
            return None
        grants = conn.execute(
            "SELECT page_key, function_key FROM permission_set_grants WHERE permission_set_id = ? ORDER BY page_key, function_key",
            (set_id,),
        ).fetchall()
        data = _set_row(row)
        data["grants"] = [{"pageKey": g["page_key"], "functionKey": g["function_key"]} for g in grants]
        return data


def create_permission_set(
    *,
    name: str,
    description: str = "",
    is_active: bool = True,
    grants: list[dict[str, str]] | None = None,
    actor_email: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_permissions_schema(db_path)
    name = (name or "").strip()
    if not name:
        raise ValueError("Permission set name is required.")
    now = time.time()
    clean_grants = _normalize_grants(grants or [])
    with _db(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM permission_sets WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if existing:
            raise ValueError("A permission set with this name already exists.")
        cur = conn.execute(
            """
            INSERT INTO permission_sets (name, description, is_active, created_at, updated_at, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, description.strip(), 1 if is_active else 0, now, now, actor_email, actor_email),
        )
        set_id = int(cur.lastrowid)
        _replace_grants(conn, set_id, clean_grants)
        _audit(
            conn,
            actor_email=actor_email,
            action="create",
            entity_type="permission_set",
            entity_id=str(set_id),
            detail={"name": name, "grants": clean_grants},
        )
    return get_permission_set(set_id, db_path)  # type: ignore[return-value]


def update_permission_set(
    set_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
    grants: list[dict[str, str]] | None = None,
    actor_email: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_permissions_schema(db_path)
    current = get_permission_set(set_id, db_path)
    if not current:
        raise ValueError("Permission set not found.")
    now = time.time()
    new_name = current["name"] if name is None else name.strip()
    if not new_name:
        raise ValueError("Permission set name is required.")
    new_description = current["description"] if description is None else description.strip()
    new_active = current["isActive"] if is_active is None else bool(is_active)
    with _db(db_path) as conn:
        clash = conn.execute(
            "SELECT id FROM permission_sets WHERE name = ? COLLATE NOCASE AND id != ?",
            (new_name, set_id),
        ).fetchone()
        if clash:
            raise ValueError("A permission set with this name already exists.")
        conn.execute(
            """
            UPDATE permission_sets
            SET name = ?, description = ?, is_active = ?, updated_at = ?, updated_by = ?
            WHERE id = ?
            """,
            (new_name, new_description, 1 if new_active else 0, now, actor_email, set_id),
        )
        if grants is not None:
            clean_grants = _normalize_grants(grants)
            _replace_grants(conn, set_id, clean_grants)
        else:
            clean_grants = [
                {"pageKey": g["pageKey"], "functionKey": g["functionKey"]} for g in current["grants"]
            ]
        _audit(
            conn,
            actor_email=actor_email,
            action="update",
            entity_type="permission_set",
            entity_id=str(set_id),
            detail={"name": new_name, "isActive": new_active, "grants": clean_grants},
        )
    return get_permission_set(set_id, db_path)  # type: ignore[return-value]


def delete_permission_set(set_id: int, *, actor_email: str | None = None, db_path: Path | None = None) -> None:
    init_permissions_schema(db_path)
    current = get_permission_set(set_id, db_path)
    if not current:
        raise ValueError("Permission set not found.")
    if current["name"].lower() == FULL_ACCESS_SET_NAME.lower():
        raise ValueError("The system Full Access permission set cannot be deleted.")
    with _db(db_path) as conn:
        conn.execute("DELETE FROM permission_set_grants WHERE permission_set_id = ?", (set_id,))
        conn.execute("DELETE FROM user_permission_sets WHERE permission_set_id = ?", (set_id,))
        conn.execute("DELETE FROM permission_sets WHERE id = ?", (set_id,))
        _audit(
            conn,
            actor_email=actor_email,
            action="delete",
            entity_type="permission_set",
            entity_id=str(set_id),
            detail={"name": current["name"]},
        )


def set_permission_set_active(
    set_id: int,
    is_active: bool,
    *,
    actor_email: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    return update_permission_set(set_id, is_active=is_active, actor_email=actor_email, db_path=db_path)


def list_users_with_sets(db_path: Path | None = None) -> list[dict[str, Any]]:
    init_permissions_schema(db_path)
    with _db(db_path) as conn:
        users = conn.execute(
            """
            SELECT id, email, is_verified, is_super_admin, created_at, verified_at, updated_at
            FROM users
            ORDER BY email COLLATE NOCASE
            """
        ).fetchall()
        result = []
        for user in users:
            sets = conn.execute(
                """
                SELECT ps.id, ps.name, ps.is_active
                FROM user_permission_sets ups
                JOIN permission_sets ps ON ps.id = ups.permission_set_id
                WHERE ups.user_id = ?
                ORDER BY ps.name COLLATE NOCASE
                """,
                (user["id"],),
            ).fetchall()
            result.append(
                {
                    "id": user["id"],
                    "email": user["email"],
                    "isVerified": bool(user["is_verified"]),
                    "isSuperAdmin": bool(user["is_super_admin"]),
                    "createdAt": user["created_at"],
                    "verifiedAt": user["verified_at"],
                    "updatedAt": user["updated_at"],
                    "permissionSets": [
                        {"id": s["id"], "name": s["name"], "isActive": bool(s["is_active"])} for s in sets
                    ],
                }
            )
        return result


def get_user_permission_detail(email: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_permissions_schema(db_path)
    user = store.get_user(email, db_path)
    if not user:
        return None
    with _db(db_path) as conn:
        sets = conn.execute(
            """
            SELECT ps.id, ps.name, ps.description, ps.is_active, ups.assigned_at, ups.assigned_by
            FROM user_permission_sets ups
            JOIN permission_sets ps ON ps.id = ups.permission_set_id
            WHERE ups.user_id = ?
            ORDER BY ps.name COLLATE NOCASE
            """,
            (user["id"],),
        ).fetchall()
    return {
        "id": user["id"],
        "email": user["email"],
        "isVerified": bool(user["is_verified"]),
        "isSuperAdmin": bool(user.get("is_super_admin")),
        "permissionSets": [
            {
                "id": s["id"],
                "name": s["name"],
                "description": s["description"],
                "isActive": bool(s["is_active"]),
                "assignedAt": s["assigned_at"],
                "assignedBy": s["assigned_by"],
            }
            for s in sets
        ],
    }


def assign_permission_set(
    email: str,
    set_id: int,
    *,
    actor_email: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_permissions_schema(db_path)
    user = store.get_user(email, db_path)
    if not user:
        raise ValueError("User not found.")
    if not get_permission_set(set_id, db_path):
        raise ValueError("Permission set not found.")
    now = time.time()
    with _db(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_permission_sets (user_id, permission_set_id, assigned_at, assigned_by)
            VALUES (?, ?, ?, ?)
            """,
            (user["id"], set_id, now, actor_email),
        )
        _audit(
            conn,
            actor_email=actor_email,
            action="assign",
            entity_type="user_permission_set",
            entity_id=f"{user['id']}:{set_id}",
            detail={"email": user["email"], "permission_set_id": set_id},
        )
    return get_user_permission_detail(email, db_path)  # type: ignore[return-value]


def unassign_permission_set(
    email: str,
    set_id: int,
    *,
    actor_email: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_permissions_schema(db_path)
    user = store.get_user(email, db_path)
    if not user:
        raise ValueError("User not found.")
    with _db(db_path) as conn:
        conn.execute(
            "DELETE FROM user_permission_sets WHERE user_id = ? AND permission_set_id = ?",
            (user["id"], set_id),
        )
        _audit(
            conn,
            actor_email=actor_email,
            action="unassign",
            entity_type="user_permission_set",
            entity_id=f"{user['id']}:{set_id}",
            detail={"email": user["email"], "permission_set_id": set_id},
        )
    return get_user_permission_detail(email, db_path)  # type: ignore[return-value]


def effective_grants_for_email(email: str, db_path: Path | None = None) -> dict[str, Any]:
    """Return effective permissions as union of active assigned sets (or all if super-admin)."""
    init_permissions_schema(db_path)
    user = store.get_user(email, db_path)
    if not user:
        return {"isSuperAdmin": False, "pages": {}, "grants": []}
    is_super = bool(user.get("is_super_admin"))
    grants: set[tuple[str, str]] = set()
    if is_super:
        grants = set(all_grants())
    else:
        with _db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT g.page_key, g.function_key
                FROM user_permission_sets ups
                JOIN permission_sets ps ON ps.id = ups.permission_set_id
                JOIN permission_set_grants g ON g.permission_set_id = ps.id
                WHERE ups.user_id = ? AND ps.is_active = 1
                """,
                (user["id"],),
            ).fetchall()
            grants = {(r["page_key"], r["function_key"]) for r in rows}

    pages: dict[str, list[str]] = {}
    for page_key, function_key in sorted(grants):
        pages.setdefault(page_key, []).append(function_key)
    return {
        "isSuperAdmin": is_super,
        "pages": pages,
        "grants": [{"pageKey": p, "functionKey": f} for p, f in sorted(grants)],
    }


def _normalize_grants(grants: list[dict[str, str]]) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in grants:
        page_key = str(item.get("pageKey") or item.get("page_key") or "").strip()
        function_key = str(item.get("functionKey") or item.get("function_key") or "").strip()
        if not page_key or not function_key:
            continue
        if not is_valid_grant(page_key, function_key):
            raise ValueError(f"Unknown permission: {page_key}.{function_key}")
        key = (page_key, function_key)
        if key in seen:
            continue
        seen.add(key)
        clean.append({"pageKey": page_key, "functionKey": function_key})
    return clean


def _replace_grants(conn, set_id: int, grants: list[dict[str, str]]) -> None:
    conn.execute("DELETE FROM permission_set_grants WHERE permission_set_id = ?", (set_id,))
    for grant in grants:
        conn.execute(
            "INSERT INTO permission_set_grants (permission_set_id, page_key, function_key) VALUES (?, ?, ?)",
            (set_id, grant["pageKey"], grant["functionKey"]),
        )


def _set_row(row) -> dict[str, Any]:
    data = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "isActive": bool(row["is_active"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "createdBy": row["created_by"],
        "updatedBy": row["updated_by"],
    }
    keys = row.keys()
    if "grant_count" in keys:
        data["grantCount"] = int(row["grant_count"])
    if "user_count" in keys:
        data["userCount"] = int(row["user_count"])
    return data
