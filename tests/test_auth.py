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

    def test_register_verify_login_and_reset(self):
        registered = service.register("user@example.com")
        self.assertTrue(registered["ok"])
        self.assertIn("debug_link", registered)
        token = registered["debug_link"].split("token=")[1].split("&")[0]
        service.set_password_with_token(token=token, purpose="verify", password="Secret123", confirm="Secret123")
        logged = service.login("user@example.com", "Secret123")
        self.assertEqual(logged["email"], "user@example.com")

        reset = service.request_password_reset("user@example.com")
        reset_token = reset["debug_link"].split("token=")[1].split("&")[0]
        service.set_password_with_token(token=reset_token, purpose="reset", password="NewSecret1", confirm="NewSecret1")
        self.assertEqual(service.login("user@example.com", "NewSecret1")["email"], "user@example.com")
        with self.assertRaises(ValueError):
            service.login("user@example.com", "Secret123")


if __name__ == "__main__":
    unittest.main()
