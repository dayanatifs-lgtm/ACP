"""Connector profile CRUD tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acp_importer import profiles


class ConnectorProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "environments.local.json"
        self.patcher = patch.object(profiles, "PROFILE_FILE", self.path)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_upsert_masks_secrets_and_lists_environment(self):
        saved = profiles.upsert_connector(
            "DevX",
            {
                "connectorType": "IFS",
                "base_url": "https://example/projection/v1",
                "token_url": "https://example/token",
                "client_id": "cid",
                "client_secret": "secret-value",
                "grant_type": "password",
                "username": "user",
                "password": "pass-value",
                "verify_tls": False,
            },
        )
        self.assertEqual(saved["name"], "DevX")
        self.assertEqual(saved["client_secret"], profiles.MASK)
        self.assertTrue(saved["has_password"])
        self.assertIn("DevX", profiles.environment_names())
        stored = json.loads(self.path.read_text(encoding="utf-8"))["environments"]["DevX"]
        self.assertEqual(stored["client_secret"], "secret-value")

    def test_masked_secret_keeps_existing_on_update(self):
        profiles.upsert_connector(
            "DevY",
            {
                "base_url": "https://example/projection/v1",
                "token_url": "https://example/token",
                "client_id": "cid",
                "client_secret": "keep-me",
                "grant_type": "client_credentials",
            },
        )
        profiles.upsert_connector(
            "DevY",
            {
                "base_url": "https://example/projection/v1",
                "token_url": "https://example/token",
                "client_id": "cid",
                "client_secret": profiles.MASK,
                "grant_type": "client_credentials",
            },
        )
        stored = json.loads(self.path.read_text(encoding="utf-8"))["environments"]["DevY"]
        self.assertEqual(stored["client_secret"], "keep-me")

    def test_delete_connector(self):
        profiles.upsert_connector(
            "Temp",
            {
                "base_url": "https://example/projection/v1",
                "token_url": "https://example/token",
                "client_id": "cid",
                "client_secret": "secret",
                "grant_type": "client_credentials",
            },
        )
        profiles.delete_connector("Temp")
        self.assertNotIn("Temp", profiles.environment_names())


if __name__ == "__main__":
    unittest.main()
