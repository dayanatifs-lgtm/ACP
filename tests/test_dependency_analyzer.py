"""Deterministic dependency-analysis tests using synthetic ACP zip archives."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from acp_importer.dependency.analyzer import DependencyAnalyzer
from acp_importer.dependency.failures import ImportFailureAnalyzer
from acp_importer.dependency.models import CONFIRMED, MISSING, AMBIGUOUS, REJECTED


def _acp_xml(name: str, extra: str = "", items: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<APPLICATION_CONFIGURATION>
  <NAME>{name}</NAME>
  <PACKAGE_ID>{name}</PACKAGE_ID>
  <ITEMS>{items}</ITEMS>
  {extra}
</APPLICATION_CONFIGURATION>
"""


def _lu_item(lu: str) -> str:
    return f"""<ITEMS_ROW><TYPE>CUSTOM_LU</TYPE><NAME>{lu}</NAME><FILENAME>CustomLogicalUnit-{lu}.xml</FILENAME></ITEMS_ROW>"""


def _write_acp(folder: Path, name: str, extra: str = "", items: str = "") -> None:
    path = folder / f"{name}.acp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}.xml", _acp_xml(name, extra=extra, items=items))


class DependencyAnalyzerTests(unittest.TestCase):
    def analyse(self, builder) -> dict:
        previous = Path(__file__).resolve().parents[1] / ".acp_meta_cache"
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            builder(folder)
            return DependencyAnalyzer().analyze_all(folder)

    def names_at(self, report, level: int) -> set[str]:
        plan = report["deploymentPlan"]
        if level >= len(plan):
            return set()
        return {pkg["name"] for pkg in plan[level]["packages"]}

    def test_empty_folder(self):
        report = self.analyse(lambda folder: None)
        self.assertEqual(report["summary"]["acpsScanned"], 0)
        self.assertFalse(report["canStart"])

    def test_no_dependencies(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-A", items=_lu_item("Alpha"))
            _write_acp(folder, "ACP-B", items=_lu_item("Beta"))
        report = self.analyse(build)
        self.assertEqual(len(report["deploymentPlan"]), 1)
        self.assertEqual(self.names_at(report, 0), {"ACP-A", "ACP-B"})
        self.assertEqual(report["summary"]["resolved"], 0)

    def test_one_dependency(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-A", items=_lu_item("Customer"))
            _write_acp(folder, "ACP-B", extra="<ProjectionEntitySetName>Customer</ProjectionEntitySetName>", items=_lu_item("Other"))
        report = self.analyse(build)
        self.assertEqual(self.names_at(report, 0), {"ACP-A"})
        self.assertEqual(self.names_at(report, 1), {"ACP-B"})
        edge = next(item for item in report["edges"] if item["status"] == CONFIRMED)
        self.assertEqual(edge["consumerAcp"], "ACP-B")
        self.assertEqual(edge["providerAcp"], "ACP-A")

    def test_multi_level_dependency(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-A", items=_lu_item("ObjA"))
            _write_acp(folder, "ACP-B", extra="<ProjectionEntitySetName>ObjA</ProjectionEntitySetName>", items=_lu_item("ObjB"))
            _write_acp(folder, "ACP-C", extra="<ProjectionEntitySetName>ObjB</ProjectionEntitySetName>", items=_lu_item("ObjC"))
        report = self.analyse(build)
        self.assertEqual(self.names_at(report, 0), {"ACP-A"})
        self.assertEqual(self.names_at(report, 1), {"ACP-B"})
        self.assertEqual(self.names_at(report, 2), {"ACP-C"})

    def test_independent_branches(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-A", items=_lu_item("Left"))
            _write_acp(folder, "ACP-B", extra="<ProjectionEntitySetName>Left</ProjectionEntitySetName>", items=_lu_item("LeftChild"))
            _write_acp(folder, "ACP-C", items=_lu_item("Right"))
            _write_acp(folder, "ACP-D", extra="<ProjectionEntitySetName>Right</ProjectionEntitySetName>", items=_lu_item("RightChild"))
        report = self.analyse(build)
        self.assertEqual(self.names_at(report, 0), {"ACP-A", "ACP-C"})
        self.assertEqual(self.names_at(report, 1), {"ACP-B", "ACP-D"})

    def test_missing_provider(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-500", extra="<ProjectionEntitySetName>CustomerHandling</ProjectionEntitySetName>", items=_lu_item("Local"))
        report = self.analyse(build)
        missing = report["missingDependencies"]
        self.assertTrue(any(item["objectName"] == "CustomerHandling" and item["status"] == MISSING for item in missing))
        self.assertTrue(report["canStart"])

    def test_ambiguous_and_duplicate_provider(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-100", items=_lu_item("CustomerHandling"))
            _write_acp(folder, "ACP-203", items=_lu_item("CustomerHandling"))
            _write_acp(folder, "ACP-500", extra="<ProjectionEntitySetName>CustomerHandling</ProjectionEntitySetName>", items=_lu_item("Consumer"))
        report = self.analyse(build)
        ambiguous = report["ambiguousDependencies"]
        self.assertTrue(any(item["status"] == AMBIGUOUS and set(item["candidates"]) == {"ACP-100", "ACP-203"} for item in ambiguous))
        self.assertFalse(any(item["status"] == CONFIRMED and item["objectName"] == "CustomerHandling" for item in report["edges"]))
        self.assertTrue(report["duplicateDefinitions"])

    def test_circular_dependency(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-A", extra="<ProjectionEntitySetName>ObjB</ProjectionEntitySetName>", items=_lu_item("ObjA"))
            _write_acp(folder, "ACP-B", extra="<ProjectionEntitySetName>ObjC</ProjectionEntitySetName>", items=_lu_item("ObjB"))
            _write_acp(folder, "ACP-C", extra="<ProjectionEntitySetName>ObjA</ProjectionEntitySetName>", items=_lu_item("ObjC"))
        report = self.analyse(build)
        self.assertTrue(report["cycles"])
        self.assertTrue(report["canStart"])
        self.assertEqual({pkg["name"] for pkg in report["packages"]}, {"ACP-A", "ACP-B", "ACP-C"})
        self.assertEqual(len(report["blockedPackages"]), 3)
        self.assertTrue(all(pkg["cyclic"] for pkg in report["packages"]))

    def test_self_dependency(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "ACP-A", extra="<DEPENDENCY>ACP-A</DEPENDENCY>", items=_lu_item("Solo"))
        report = self.analyse(build)
        self.assertTrue(any(item["status"] == REJECTED for item in report["edges"]))
        self.assertEqual(self.names_at(report, 0), {"ACP-A"})

    def test_invalid_package(self):
        def build(folder: Path) -> None:
            (folder / "broken.acp").write_bytes(b"not a zip")
            _write_acp(folder, "ACP-OK", items=_lu_item("Ok"))
        report = self.analyse(build)
        self.assertEqual(len(report["invalid"]), 1)
        self.assertEqual(self.names_at(report, 0), {"ACP-OK"})

    def test_family_rule_and_explicit_package(self):
        def build(folder: Path) -> None:
            _write_acp(folder, "CMKT046", items=_lu_item("Base"))
            _write_acp(folder, "CMKT046-1", extra="<DEPENDENCY>CMKT046</DEPENDENCY>", items=_lu_item("Child"))
        report = self.analyse(build)
        self.assertEqual(self.names_at(report, 0), {"CMKT046"})
        self.assertEqual(self.names_at(report, 1), {"CMKT046-1"})

    def test_large_collection_independent(self):
        def build(folder: Path) -> None:
            for index in range(40):
                _write_acp(folder, f"ACP{index:03d}", items=_lu_item(f"Lu{index:03d}"))
        report = self.analyse(build)
        self.assertEqual(report["summary"]["validPackages"], 40)
        self.assertEqual(len(report["deploymentPlan"][0]["packages"]), 40)

    def test_identical_items_are_accepted_as_already_present(self):
        from acp_importer.client import IfsAcpClient
        entity = {"Summary": "Items: 30 - Errors: 1 - Warnings: 8"}
        log = 'Importing Custom Lu "CGoalSettingRatings" is identical. Ignored.'
        self.assertTrue(IfsAcpClient._identical_items_only(entity, log, 1))
        self.assertFalse(IfsAcpClient._identical_items_only(entity, "A real validation error occurred", 1))
        self.assertFalse(IfsAcpClient._identical_items_only({"Summary": "Items: 2 - Errors: 2"}, log, 2))

    def test_already_exists_items_are_accepted_as_already_present(self):
        from acp_importer.client import IfsAcpClient
        entity = {"Summary": "Items: 10 - Errors: 2 - Warnings: 1"}
        log = (
            'Start importing package Creating the package "CUSTOM_LU_001" '
            'Importing Custom Lu "CBankLog" already exists in package MIT_Entity. Ignored. '
            'Importing Custom Lu "CCustomerInfo" already exists in another package.'
        )
        self.assertTrue(IfsAcpClient._identical_items_only(entity, log, 2))
        self.assertGreaterEqual(IfsAcpClient._already_present_item_count(log), 2)
        hint = IfsAcpClient._log_hint(log)
        self.assertIn("already exists", hint)
        self.assertTrue(IfsAcpClient._is_cache_lock_error(
            'DATABASE_ERROR FND_LOCKED: The update could not be performed since the Cache Management record is currently locked.'
        ))
        self.assertTrue(IfsAcpClient._is_not_found(
            "GET AppConfigPackageSet(PackageId='E35C6FAC') failed (404): ODP_RESOURCE_NOTFOUND Resource not found."
        ))

    def test_iam_timeout_is_shorter_than_import_timeout(self):
        from acp_importer.client import IfsAcpClient
        from acp_importer.config import Settings
        from pathlib import Path
        settings = Settings(
            base_url="https://example.ifs.cloud/proj",
            token_url="https://example.ifs.cloud/auth/realms/x/protocol/openid-connect/token",
            client_id="id",
            client_secret="secret",
            grant_type="password",
            token_client_auth="basic",
            scope=None,
            username="user",
            password="pass",
            acp_folder=Path("."),
            poll_seconds=2,
            timeout_seconds=300,
            request_timeout_seconds=300,
            verify_tls=False,
            xsrf_token=None,
            use_env_proxy=False,
        )
        client = IfsAcpClient(settings)
        connect, read = client._iam_timeout()
        self.assertEqual(connect, 10.0)
        self.assertEqual(read, 45.0)
        self.assertEqual(client._iam_host(), "example.ifs.cloud")

    def test_failure_analyzer_parses_missing_object(self):
        analyzer = ImportFailureAnalyzer()
        with tempfile.TemporaryDirectory() as raw:
            from acp_importer.dependency import failures
            original = failures.STORE_PATH
            failures.STORE_PATH = Path(raw) / "failures.json"
            try:
                record = analyzer.record(acp="ACP-500", file="ACP-500.acp", message="GET failed (400): projection 'CustomerHandling' was not found")
                self.assertEqual(record.http_status, 400)
                self.assertEqual(record.parsed_missing_object, "CustomerHandling")
            finally:
                failures.STORE_PATH = original

    def test_gemini_json_is_extracted_from_markdown(self):
        from acp_importer.dependency.gemini import extract_json_object
        payload = extract_json_object('```json\n{"importFirst":["CSAL190-1.zip"],"retryFailed":true}\n```')
        self.assertEqual(payload["importFirst"], ["CSAL190-1.zip"])
        self.assertTrue(payload["retryFailed"])

    def test_gemini_wraps_timeouts_as_gemini_error(self):
        from unittest.mock import patch
        import requests
        from acp_importer.dependency.gemini import GeminiError, generate_json

        with patch("acp_importer.dependency.gemini.gemini_settings", return_value={
            "api_key": "test-key",
            "model": "gemini-flash-lite-latest",
            "url": "",
            "ca_bundle": "",
            "verify_tls": "true",
            "env_file": "",
        }), patch("acp_importer.dependency.gemini.requests.post", side_effect=requests.exceptions.ReadTimeout("timed out")):
            with self.assertRaises(GeminiError) as raised:
                generate_json("Return JSON only: {\"ok\": true}", timeout=1)
        self.assertIn("timed out", str(raised.exception).lower())

    def test_gemini_ssl_error_mentions_ca_bundle(self):
        from unittest.mock import patch
        import requests
        from acp_importer.dependency.gemini import GeminiError, generate_json

        ssl_error = requests.exceptions.SSLError(
            "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
            "Max retries exceeded (Caused by SSLError(SSLCertVerificationError(1, "
            "'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed "
            "certificate in certificate chain')))"
        )
        with patch("acp_importer.dependency.gemini.gemini_settings", return_value={
            "api_key": "test-key",
            "model": "gemini-flash-lite-latest",
            "url": "",
            "ca_bundle": "",
            "verify_tls": "true",
            "env_file": "",
        }), patch("acp_importer.dependency.gemini.requests.post", side_effect=ssl_error):
            with self.assertRaises(GeminiError) as raised:
                generate_json("Return JSON only: {\"ok\": true}", timeout=1)
        message = str(raised.exception)
        self.assertIn("GEMINI_CA_BUNDLE", message)
        self.assertIn("GEMINI_VERIFY_TLS", message)

    def test_ai_retry_matches_catalog_files_only(self):
        from acp_importer.dependency.ai_retry import _file_index, _match_files
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "CSAL190-1.zip").write_bytes(b"PK\x03\x04")
            (folder / "OTHER.zip").write_bytes(b"PK\x03\x04")
            index = _file_index(folder, [{"name": "CSAL190-1", "file": "CSAL190-1.zip"}])
            matched = _match_files(["CSAL190-1", "InventedACP.zip", "OTHER.zip"], index)
            self.assertEqual([path.name for path in matched], ["CSAL190-1.zip", "OTHER.zip"])


if __name__ == "__main__":
    unittest.main()
