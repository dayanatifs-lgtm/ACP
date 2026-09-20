"""SQLite user and one-time token store."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .settings import USERS_DB


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or USERS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _db(db_path: Path | None = None):
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with _db(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT,
                is_verified INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                verified_at REAL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE,
                purpose TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL
            );
            """
        )


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_user(email: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    with _db(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
        return dict(row) if row else None


def create_unverified_user(email: str, db_path: Path | None = None) -> dict[str, Any]:
    init_db(db_path)
    now = time.time()
    email = normalize_email(email)
    with _db(db_path) as conn:
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            if existing["is_verified"] and existing["password_hash"]:
                raise ValueError("An account with this email already exists. Please sign in.")
            conn.execute("UPDATE users SET updated_at = ? WHERE email = ?", (now, email))
        else:
            conn.execute(
                "INSERT INTO users (email, password_hash, is_verified, created_at, updated_at) VALUES (?, NULL, 0, ?, ?)",
                (email, now, now),
            )
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row)


def set_password(email: str, password_hash: str, *, mark_verified: bool = True, db_path: Path | None = None) -> None:
    init_db(db_path)
    now = time.time()
    with _db(db_path) as conn:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, is_verified = ?, verified_at = COALESCE(verified_at, ?), updated_at = ?
            WHERE email = ?
            """,
            (password_hash, 1 if mark_verified else 0, now if mark_verified else None, now, normalize_email(email)),
        )


def create_token(email: str, purpose: str, *, hours: float = 24, db_path: Path | None = None) -> str:
    from .settings import new_token

    init_db(db_path)
    token = new_token()
    now = time.time()
    with _db(db_path) as conn:
        conn.execute(
            "UPDATE auth_tokens SET used_at = ? WHERE email = ? AND purpose = ? AND used_at IS NULL",
            (now, normalize_email(email), purpose),
        )
        conn.execute(
            "INSERT INTO auth_tokens (token, email, purpose, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token, normalize_email(email), purpose, now, now + hours * 3600),
        )
    return token


def consume_token(token: str, purpose: str, db_path: Path | None = None) -> str:
    init_db(db_path)
    now = time.time()
    with _db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM auth_tokens WHERE token = ? AND purpose = ?",
            (token, purpose),
        ).fetchone()
        if row is None:
            raise ValueError("This link is invalid or has already been used.")
        if row["used_at"] is not None:
            raise ValueError("This link has already been used.")
        if float(row["expires_at"]) < now:
            raise ValueError("This link has expired. Please request a new one.")
        email = str(row["email"])
        conn.execute("UPDATE auth_tokens SET used_at = ? WHERE token = ?", (now, token))
        return email


def user_count(db_path: Path | None = None) -> int:
    init_db(db_path)
    with _db(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"] if row else 0)


def get_token_row(token: str, purpose: str, db_path: Path | None = None):
    init_db(db_path)
    with _db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM auth_tokens WHERE token = ? AND purpose = ?",
            (token, purpose),
        ).fetchone()
        return dict(row) if row else None
