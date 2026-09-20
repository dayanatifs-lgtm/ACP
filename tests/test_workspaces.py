"""Per-user workspace isolation tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acp_importer import workspaces as ws


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name) / "workspaces"
        self.patcher = patch.dict("os.environ", {"ACP_WORKSPACES_ROOT": str(self.root)})
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_users_get_isolated_folders(self) -> None:
        a = ws.user_workspace("alice@example.com")
        b = ws.user_workspace("bob@example.com")
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())

    def test_upload_and_list(self) -> None:
        result = ws.save_uploads("alice@example.com", [("demo.acp", b"abc"), ("notes.txt", b"x")])
        self.assertEqual(result["saved"], ["demo.acp"])
        self.assertEqual(result["skipped"], ["notes.txt"])
        self.assertEqual(result["fileCount"], 1)

    def test_non_admin_cannot_escape_workspace(self) -> None:
        with self.assertRaises(ValueError):
            ws.resolve_folder_for_user("alice@example.com", r"C:\Windows", is_super_admin=False)

    def test_workspace_token_resolves(self) -> None:
        path = ws.resolve_folder_for_user("alice@example.com", "__workspace__")
        self.assertEqual(path, ws.user_workspace("alice@example.com"))


if __name__ == "__main__":
    unittest.main()
