"""Authentication flow tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acp_importer.auth import passwords, service, store
from acp_importer.auth.settings import AuthSettings


class AuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "users.sqlite"
        self.settings = AuthSettings(
            enabled=True,
            secret="test-secret",
            base_url="http://127.0.0.1:8766",
            cookie_name="acp_session",
            session_days=1,
            smtp_host="",
            smtp_port=587,
            smtp_user="",
            smtp_password="",
            smtp_from="noreply@test",
            smtp_use_tls=True,
            allow_signup=True,
        )
        self.patchers = [
            patch.object(store, "USERS_DB", self.db),
            patch("acp_importer.auth.service.auth_settings", return_value=self.settings),
            patch("acp_importer.auth.mailer.AUTH_LOG", Path(self.tmp.name) / "auth-links.log"),
        ]
        for item in self.patchers:
            item.start()
        store.init_db(self.db)

    def tearDown(self) -> None:
        for item in self.patchers:
            item.stop()
        self.tmp.cleanup()

    def test_password_hash_roundtrip(self):
        hashed = passwords.hash_password("secret123")
        self.assertTrue(passwords.verify_password("secret123", hashed))
        self.assertFalse(passwords.verify_password("wrong", hashed))

    def test_register_uses_public_base_url(self):
        registered = service.register("other@example.com", public_base_url="http://dse1thorftp1:8088")
        self.assertTrue(registered["debug_link"].startswith("http://dse1thorftp1:8088/set-password?"))


if __name__ == "__main__":
    unittest.main()
