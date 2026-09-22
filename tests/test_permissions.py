"""Permission set and effective-permission tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acp_importer.auth import passwords, permissions_store as ps
from acp_importer.auth.permissions import has_function_access, has_page_access
from acp_importer.auth.permissions_catalog import all_grants, page_key_for_path
from acp_importer.auth import store


class PermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "users.sqlite"
        store.init_db(self.db)
        ps.init_permissions_schema(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_user(self, email: str, *, super_admin: bool = False) -> None:
        store.create_unverified_user(email, self.db)
        store.set_password(email, passwords.hash_password("password123"), db_path=self.db)
        if super_admin:
            with store._db(self.db) as conn:
                conn.execute("UPDATE users SET is_super_admin = 1 WHERE email = ?", (email,))

    def test_page_key_mapping(self) -> None:
        self.assertEqual(page_key_for_path("/"), "dashboard")
        self.assertEqual(page_key_for_path("/clone"), "clone")
        self.assertEqual(page_key_for_path("/releases/ABC"), "releases")
        self.assertEqual(page_key_for_path("/admin/users"), "administration")
        self.assertIsNone(page_key_for_path("/login"))

    def test_union_of_active_sets(self) -> None:
        self._create_user("a@example.com")
        # Remove bootstrap Full Access assignment for this user
        user = store.get_user("a@example.com", self.db)
        with store._db(self.db) as conn:
            conn.execute("DELETE FROM user_permission_sets WHERE user_id = ?", (user["id"],))

        set_a = ps.create_permission_set(
            name="Finance",
            grants=[
                {"pageKey": "dashboard", "functionKey": "view"},
                {"pageKey": "connectors", "functionKey": "view"},
                {"pageKey": "connectors", "functionKey": "create"},
            ],
            actor_email="admin@example.com",
            db_path=self.db,
        )
        set_b = ps.create_permission_set(
            name="Reporting",
            grants=[
                {"pageKey": "connectors", "functionKey": "edit"},
                {"pageKey": "releases", "functionKey": "view"},
            ],
            actor_email="admin@example.com",
            db_path=self.db,
        )
        ps.assign_permission_set("a@example.com", set_a["id"], db_path=self.db)
        ps.assign_permission_set("a@example.com", set_b["id"], db_path=self.db)

        effective = ps.effective_grants_for_email("a@example.com", self.db)
        self.assertTrue(has_page_access(effective, "dashboard"))
        self.assertTrue(has_function_access(effective, "connectors", "view"))
        self.assertTrue(has_function_access(effective, "connectors", "create"))
        self.assertTrue(has_function_access(effective, "connectors", "edit"))
        self.assertTrue(has_function_access(effective, "releases", "view"))
        self.assertFalse(has_function_access(effective, "connectors", "delete"))

    def test_inactive_set_ignored(self) -> None:
        self._create_user("b@example.com")
        user = store.get_user("b@example.com", self.db)
        with store._db(self.db) as conn:
            conn.execute("DELETE FROM user_permission_sets WHERE user_id = ?", (user["id"],))

        set_a = ps.create_permission_set(
            name="Inactive Set",
            is_active=False,
            grants=[{"pageKey": "calendar", "functionKey": "view"}],
            db_path=self.db,
        )
        ps.assign_permission_set("b@example.com", set_a["id"], db_path=self.db)
        effective = ps.effective_grants_for_email("b@example.com", self.db)
        self.assertFalse(has_page_access(effective, "calendar"))

    def test_super_admin_has_all(self) -> None:
        self._create_user("admin@example.com", super_admin=True)
        effective = ps.effective_grants_for_email("admin@example.com", self.db)
        self.assertTrue(effective["isSuperAdmin"])
        self.assertEqual(len(effective["grants"]), len(all_grants()))

    def test_cannot_delete_full_access(self) -> None:
        sets = ps.list_permission_sets(self.db)
        full = next(s for s in sets if s["name"] == ps.FULL_ACCESS_SET_NAME)
        with self.assertRaises(ValueError):
            ps.delete_permission_set(full["id"], db_path=self.db)

    def test_default_set_assigned_when_user_has_none(self) -> None:
        self._create_user("new@example.com")
        user = store.get_user("new@example.com", self.db)
        with store._db(self.db) as conn:
            conn.execute("DELETE FROM user_permission_sets WHERE user_id = ?", (user["id"],))

        # Bootstrap already created Default; ensure assignment works.
        sets = {s["name"].lower(): s for s in ps.list_permission_sets(self.db)}
        self.assertIn("default", sets)
        assigned = ps.ensure_default_permissions("new@example.com", self.db)
        self.assertTrue(assigned)
        detail = ps.get_user_permission_detail("new@example.com", self.db)
        self.assertTrue(any(s["name"].lower() == "default" for s in detail["permissionSets"]))
        # Second call does nothing once a set is present
        self.assertFalse(ps.ensure_default_permissions("new@example.com", self.db))
        effective = ps.effective_grants_for_email("new@example.com", self.db)
        self.assertTrue(has_page_access(effective, "dashboard"))
        self.assertFalse(has_page_access(effective, "administration"))


if __name__ == "__main__":
    unittest.main()
