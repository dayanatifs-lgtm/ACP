"""Repackage & Deploy tests. Direct ACP Clone paths are not used here."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from acp_importer.repackage.builder import reconstruct_package
from acp_importer.repackage.importer import unsatisfied_dependencies
from acp_importer.repackage.parser import parse_acp_package
from acp_importer.repackage.service import RepackagingService
from acp_importer.repackage.validator import validate_generated_package


def _root(name: str, items: str, additional: str = "") -> str:
    return f"""<?xml version="1.0"?>
<APPLICATION_CONFIGURATION>
  <EXPORT_DEF_VERSION>1</EXPORT_DEF_VERSION>
  <PACKAGE_ID>ABC123DEF456</PACKAGE_ID>
  <NAME>{name}</NAME>
  <DESCRIPTION>test</DESCRIPTION>
  <AUTHOR>thor</AUTHOR>
  <VERSION/>
  <VERSION_TIME_STAMP>2026-01-01-00.00.00</VERSION_TIME_STAMP>
  <ORIGIN>TEST</ORIGIN>
  <LAST_MODIFIED_DATE>2026-01-01-00.00.00</LAST_MODIFIED_DATE>
  <ITEMS>
{items}
  </ITEMS>
  <ADDITIONAL_ITEMS>
{additional}
  </ADDITIONAL_ITEMS>
</APPLICATION_CONFIGURATION>
"""


def _row(name: str, type_name: str, filename: str) -> str:
    return f"""    <ITEMS_ROW>
      <NAME>{name}</NAME>
      <TYPE>{type_name}</TYPE>
      <DESCRIPTION/>
      <FILENAME>{filename}</FILENAME>
    </ITEMS_ROW>
"""


def _addl(name: str, type_name: str, filename: str) -> str:
    return f"""    <ADDITIONAL_ITEM>
      <NAME>{name}</NAME>
      <TYPE>{type_name}</TYPE>
      <DESCRIPTION/>
      <FILENAME>{filename}</FILENAME>
    </ADDITIONAL_ITEM>
"""


def _item_xml(type_name: str, name: str, extra: str = "") -> str:
    return f"""<?xml version="1.0"?>
<CUSTOM_OBJECT>
  <NAME>{name}</NAME>
  <TYPE>{type_name}</TYPE>
  {extra}
</CUSTOM_OBJECT>
"""


def _write_zip(folder: Path, zip_name: str, xml_name: str, root: str, files: dict[str, str]) -> Path:
    path = folder / zip_name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(xml_name, root)
        for name, body in files.items():
            archive.writestr(f"Items/{name}", body)
    return path


class SamplePackageReconstructionTests(unittest.TestCase):
    """Critical safety tests against real customer ACP samples."""

    SAMPLE_DIR = Path(r"C:\newcode\appconfig\appconfig_new\appconfig")

    def _require(self, name: str) -> Path:
        path = self.SAMPLE_DIR / name
        if not path.exists():
            self.skipTest(f"Sample ACP not found: {path}")
        return path

    def test_cdoc004_rebuild_preserves_14_items_as_zip_acp(self):
        source = self._require("CDOC004.zip")
        source_bytes = source.read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "CDOC004.zip"
            parsed = parse_acp_package(source)
            self.assertEqual(parsed.name, "CDOC004")
            self.assertEqual(parsed.root_xml_name, "CDOC004.xml")
            self.assertEqual(len(parsed.items), 14)
            self.assertEqual(sum(1 for item in parsed.items if item.source_section == "ITEMS"), 14)
            self.assertEqual(sum(1 for item in parsed.items if item.source_section == "ADDITIONAL_ITEMS"), 0)
            reconstruct_package(parsed, out)
            self.assertTrue(out.exists())
            self.assertEqual(out.suffix.lower(), ".zip")
            self.assertTrue(zipfile.is_zipfile(out))
            validation = validate_generated_package(out)
            self.assertTrue(validation["ok"], validation["errors"])
            self.assertEqual(validation["counts"]["items"], 14)
            self.assertEqual(validation["counts"]["additionalItems"], 0)
            self.assertEqual(validation["counts"]["itemFiles"], 14)
            with zipfile.ZipFile(out) as archive:
                names = archive.namelist()
                self.assertIn("CDOC004.xml", names)
                self.assertEqual(sum(1 for name in names if name.startswith("Items/")), 14)
                root = ET.fromstring(archive.read("CDOC004.xml"))
                self.assertEqual(root.tag, "APPLICATION_CONFIGURATION")
                self.assertEqual(root.findtext("NAME"), "CDOC004")
                self.assertEqual(len(root.findall(".//ITEMS_ROW")), 14)
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_cesg005_rebuild_preserves_items_and_additional_items(self):
        source = self._require("CESG005.zip")
        source_bytes = source.read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "CESG005.zip"
            parsed = parse_acp_package(source)
            self.assertEqual(parsed.name, "CESG005")
            self.assertEqual(sum(1 for item in parsed.items if item.source_section == "ITEMS"), 2)
            self.assertEqual(sum(1 for item in parsed.items if item.source_section == "ADDITIONAL_ITEMS"), 1)
            reconstruct_package(parsed, out)
            self.assertTrue(zipfile.is_zipfile(out))
            validation = validate_generated_package(out)
            self.assertTrue(validation["ok"], validation["errors"])
            self.assertEqual(validation["counts"]["items"], 2)
            self.assertEqual(validation["counts"]["additionalItems"], 1)
            with zipfile.ZipFile(out) as archive:
                root = ET.fromstring(archive.read("CESG005.xml"))
                self.assertEqual(len(root.findall(".//ITEMS_ROW")), 2)
                self.assertEqual(len(root.findall(".//ADDITIONAL_ITEM")), 1)
                self.assertEqual(root.find(".//ADDITIONAL_ITEM/TYPE").text, "CF_VIEWS")
                self.assertIn("Items/CustomFieldViews-EmEmissions.xml", archive.namelist())
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_plain_xml_file_is_rejected_as_invalid_acp(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = Path(raw) / "FAKE.zip"
            fake.write_text("<APPLICATION_CONFIGURATION><NAME>FAKE</NAME></APPLICATION_CONFIGURATION>", encoding="utf-8")
            validation = validate_generated_package(fake)
            self.assertFalse(validation["ok"])
            self.assertTrue(any("ZIP" in error for error in validation["errors"]))


class RepackageTests(unittest.TestCase):
    def test_one_item_and_rebuild(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            original = _write_zip(
                src,
                "ONE.zip",
                "ONE.xml",
                _root("ONE", _row("Customer", "CUSTOM_LU", "CustomLogicalUnit-Customer.xml")),
                {"CustomLogicalUnit-Customer.xml": _item_xml("CUSTOM_LU", "Customer", "<LU>Customer</LU>")},
            )
            before = original.read_bytes()
            parsed = parse_acp_package(original)
            reconstructed = reconstruct_package(parsed, out / "ONE.zip")
            self.assertTrue(validate_generated_package(reconstructed)["ok"])
            self.assertEqual(original.read_bytes(), before)
            report = RepackagingService().analyse_and_build(src, out / "gen", max_items=10)
            self.assertEqual(report["summary"]["itemsDiscovered"], 1)
            self.assertEqual(report["summary"]["generatedPackages"], 1)
            self.assertTrue((out / "gen" / "CUSTOM_LU" / "CUSTOM_LU_001.zip").exists())

    def test_zip_name_differs_from_root_xml(self):
        with tempfile.TemporaryDirectory() as raw:
            src = Path(raw) / "src"
            src.mkdir()
            path = _write_zip(
                src,
                "CHUM098.zip",
                "OSCA13722.xml",
                _root("OSCA13722", _row("Event", "CUSTOM_EVENT", "CustomEvent-Event.xml")),
                {"CustomEvent-Event.xml": _item_xml("CUSTOM_EVENT", "Event", "<EVENT_ID>Event</EVENT_ID>")},
            )
            parsed = parse_acp_package(path)
            self.assertEqual(parsed.name, "OSCA13722")
            self.assertEqual(parsed.root_xml_name, "OSCA13722.xml")

    def test_additional_items_and_companion_group(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            _write_zip(
                src,
                "CFIN004.zip",
                "CFIN004.xml",
                _root(
                    "CFIN004",
                    _row("GSA Information", "CF_PERSISTENT", "CustomFieldPersistent-IdentityInvoiceInfo-C_GSA.xml"),
                    _addl("Views-IdentityInvoiceInfo", "CF_VIEWS", "CustomFieldViews-IdentityInvoiceInfo.xml"),
                ),
                {
                    "CustomFieldPersistent-IdentityInvoiceInfo-C_GSA.xml": _item_xml("CF_PERSISTENT", "GSA Information"),
                    "CustomFieldViews-IdentityInvoiceInfo.xml": _item_xml("CF_VIEWS", "Views-IdentityInvoiceInfo"),
                },
            )
            report = RepackagingService().analyse_and_build(src, out, max_items=10)
            self.assertEqual(report["summary"]["generatedPackages"], 1)
            pkg = report["packages"][0]
            types = {item["type"] for item in pkg["items"]}
            self.assertEqual(types, {"CF_PERSISTENT", "CF_VIEWS"})

    def test_max_items_creates_multiple_packages(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            rows, files = [], {}
            for index in range(12):
                name = f"Lu{index:02d}"
                filename = f"CustomLogicalUnit-{name}.xml"
                rows.append(_row(name, "CUSTOM_LU", filename))
                files[filename] = _item_xml("CUSTOM_LU", name, f"<LU>{name}</LU>")
            _write_zip(src, "MANY.zip", "MANY.xml", _root("MANY", "".join(rows)), files)
            report = RepackagingService().analyse_and_build(src, out, max_items=10)
            lu_packages = [pkg for pkg in report["packages"] if pkg["category"] == "CUSTOM_LU"]
            self.assertEqual(len(lu_packages), 12)
            self.assertTrue(all(len(pkg["items"]) == 1 for pkg in lu_packages))
            self.assertEqual(sum(len(pkg["items"]) for pkg in lu_packages), 12)

    def test_dependency_across_packages(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            _write_zip(
                src, "A.zip", "A.xml",
                _root("A", _row("Customer", "CUSTOM_LU", "CustomLogicalUnit-Customer.xml")),
                {"CustomLogicalUnit-Customer.xml": _item_xml("CUSTOM_LU", "Customer", "<LU>Customer</LU>")},
            )
            _write_zip(
                src, "B.zip", "B.xml",
                _root("B", _row("CustomerPage", "CUSTOM_PAGE", "CustomPage-Customer.xml")),
                {"CustomPage-Customer.xml": _item_xml("CUSTOM_PAGE", "CustomerPage", "<LU>Customer</LU><PAGE_NAME>CustomerPage</PAGE_NAME>")},
            )
            report = RepackagingService().analyse_and_build(src, out, max_items=10)
            self.assertGreaterEqual(report["summary"]["confirmed"], 1)
            page = next(pkg for pkg in report["packages"] if pkg["category"] == "CUSTOM_PAGE")
            self.assertTrue(page["dependsOnPackages"])

    def test_missing_and_ambiguous(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            _write_zip(
                src, "C1.zip", "C1.xml",
                _root("C1", _row("Same", "CUSTOM_LU", "CustomLogicalUnit-Same.xml")),
                {"CustomLogicalUnit-Same.xml": _item_xml("CUSTOM_LU", "Same", "<LU>Same</LU>")},
            )
            _write_zip(
                src, "C2.zip", "C2.xml",
                _root("C2", _row("Same", "CUSTOM_LU", "CustomLogicalUnit-Same.xml")),
                {"CustomLogicalUnit-Same.xml": _item_xml("CUSTOM_LU", "Same", "<LU>Same</LU>")},
            )
            _write_zip(
                src, "P.zip", "P.xml",
                _root("P", _row("Page", "CUSTOM_PAGE", "CustomPage-Page.xml")),
                {"CustomPage-Page.xml": _item_xml("CUSTOM_PAGE", "Page", "<LU>Same</LU><ProjectionEntitySetName>MissingThing</ProjectionEntitySetName>")},
            )
            report = RepackagingService().analyse_and_build(src, out, max_items=10)
            self.assertTrue(report["ambiguousDependencies"] or report["duplicateDefinitions"])
            self.assertTrue(report["missingDependencies"])

    def test_circular_contained_and_invalid_archive(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            (src / "broken.zip").write_bytes(b"not-a-zip")
            _write_zip(
                src, "A.zip", "A.xml",
                _root("A", _row("AObj", "CUSTOM_LU", "CustomLogicalUnit-AObj.xml")),
                {"CustomLogicalUnit-AObj.xml": _item_xml("CUSTOM_LU", "AObj", "<LU>AObj</LU><ProjectionEntitySetName>BObj</ProjectionEntitySetName>")},
            )
            _write_zip(
                src, "B.zip", "B.xml",
                _root("B", _row("BObj", "CUSTOM_LU", "CustomLogicalUnit-BObj.xml")),
                {"CustomLogicalUnit-BObj.xml": _item_xml("CUSTOM_LU", "BObj", "<LU>BObj</LU><ProjectionEntitySetName>AObj</ProjectionEntitySetName>")},
            )
            report = RepackagingService().analyse_and_build(src, out, max_items=10)
            self.assertEqual(len(report["invalid"]), 1)
            self.assertTrue(report["cyclePaths"])

    def test_generated_root_and_filenames(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            _write_zip(
                src, "ONE.zip", "ONE.xml",
                _root("ONE", _row("Customer", "CUSTOM_LU", "CustomLogicalUnit-Customer.xml")),
                {"CustomLogicalUnit-Customer.xml": _item_xml("CUSTOM_LU", "Customer", "<LU>Customer</LU>")},
            )
            report = RepackagingService().analyse_and_build(src, out, max_items=10)
            pkg = out / report["packages"][0]["path"]
            with zipfile.ZipFile(pkg) as archive:
                root_name = [name for name in archive.namelist() if "/" not in name][0]
                self.assertTrue(root_name.endswith(".xml"))
                text = archive.read(root_name).decode()
                self.assertIn("<APPLICATION_CONFIGURATION>", text)
                self.assertIn("<FILENAME>CustomLogicalUnit-Customer.xml</FILENAME>", text)
                self.assertIn("Items/CustomLogicalUnit-Customer.xml", archive.namelist())
            self.assertTrue((out / "deployment-manifest.json").exists())
            manifest = json.loads((out / "deployment-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["packages"][0]["status"], "READY")
            self.assertIn("<DESCRIPTION/>", text)
            self.assertIn("<VERSION/>", text)
            self.assertIn("<ADDITIONAL_ITEMS/>", text)
            self.assertIn("<ORIGIN>TEST</ORIGIN>", text)
            self.assertIn("<AUTHOR>thor</AUTHOR>", text)

    def test_duplicate_custom_lu_is_kept_once(self):
        with tempfile.TemporaryDirectory() as raw:
            src, out = Path(raw) / "src", Path(raw) / "out"
            src.mkdir()
            _write_zip(
                src, "CSAL170.zip", "CSAL170.xml",
                _root("CSAL170", _row("Same", "CUSTOM_LU", "CustomLogicalUnit-Same.xml")),
                {"CustomLogicalUnit-Same.xml": _item_xml("CUSTOM_LU", "Same", "<LU>Same</LU>")},
            )
            _write_zip(
                src, "CSAL170-4.zip", "CSAL170-4.xml",
                _root("CSAL170-4", _row("Same", "CUSTOM_LU", "CustomLogicalUnit-Same.xml")),
                {"CustomLogicalUnit-Same.xml": _item_xml("CUSTOM_LU", "Same", "<LU>Same</LU>")},
            )
            report = RepackagingService().analyse_and_build(src, out, max_items=10)
            lu_packages = [pkg for pkg in report["packages"] if pkg["category"] == "CUSTOM_LU"]
            self.assertEqual(len(lu_packages), 1)
            self.assertEqual(len(lu_packages[0]["items"]), 1)
            self.assertTrue(any("Duplicate CUSTOM_LU" in warning for warning in report["warnings"]))

    def test_import_dependency_gate(self):
        manifest = {
            "packages": [
                {"package": "CUSTOM_LU_001.zip", "importStatus": "PENDING", "dependsOnPackages": []},
                {"package": "CUSTOM_PAGE_001.zip", "importStatus": "PENDING", "dependsOnPackages": ["CUSTOM_LU_001"]},
            ]
        }
        page = manifest["packages"][1]
        self.assertEqual(unsatisfied_dependencies(page, manifest), ["CUSTOM_LU_001"])
        manifest["packages"][0]["importStatus"] = "SUCCESS"
        self.assertEqual(unsatisfied_dependencies(page, manifest), [])


if __name__ == "__main__":
    unittest.main()
