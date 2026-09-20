"""Validate generated .zip files as IFS ACP ZIP packages."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

LOG = logging.getLogger("Package Validator")

REQUIRED_ROOT_TAGS = (
    "EXPORT_DEF_VERSION",
    "PACKAGE_ID",
    "NAME",
    "DESCRIPTION",
    "AUTHOR",
    "VERSION",
    "VERSION_TIME_STAMP",
    "ORIGIN",
    "LAST_MODIFIED_DATE",
    "ITEMS",
    "ADDITIONAL_ITEMS",
)


def validate_generated_package(path: Path) -> dict[str, object]:
    """Return ok=True only when the .zip is a ZIP with a complete ACP structure."""
    errors: list[str] = []
    counts = {"items": 0, "additionalItems": 0, "itemFiles": 0}

    if path.suffix.lower() != ".zip":
        errors.append("Package file extension must be .zip")
    if not path.exists():
        return {"ok": False, "errors": ["Package file does not exist"], "package": path.name, "counts": counts}
    if not zipfile.is_zipfile(path):
        return {
            "ok": False,
            "errors": ["File is not a valid ZIP archive."],
            "package": path.name,
            "counts": counts,
        }

    try:
        with zipfile.ZipFile(path) as archive:
            # Extractability check: every member must decompress.
            try:
                bad = archive.testzip()
            except Exception as exc:  # noqa: BLE001 - report as validation failure
                return {"ok": False, "errors": [f"ZIP extract test failed: {exc}"], "package": path.name, "counts": counts}
            if bad:
                errors.append(f"Corrupt ZIP member: {bad}")

            names = [name.replace("\\", "/") for name in archive.namelist()]
            if any(name.startswith("/") or name.startswith("\\") or ".." in name.split("/") for name in names):
                errors.append("ZIP contains unsafe paths")

            roots = [
                name for name in names
                if name.lower().endswith(".xml") and "/" not in name and not name.startswith("Items/")
            ]
            if len(roots) != 1:
                errors.append("Generated package must contain exactly one root APPLICATION_CONFIGURATION XML")
                return {"ok": False, "errors": errors, "package": path.name, "counts": counts}

            try:
                root_bytes = archive.read(roots[0])
                root = ET.fromstring(root_bytes)
            except ET.ParseError as exc:
                errors.append(f"Root XML is not valid XML: {exc}")
                return {"ok": False, "errors": errors, "package": path.name, "counts": counts}

            if root.tag != "APPLICATION_CONFIGURATION":
                errors.append("Root element must be APPLICATION_CONFIGURATION")
            for tag in REQUIRED_ROOT_TAGS:
                if root.find(tag) is None:
                    errors.append(f"Missing {tag}")

            package_name = (root.findtext("NAME") or "").strip()
            if not package_name:
                errors.append("APPLICATION_CONFIGURATION/NAME is empty")
            elif f"{package_name}.xml" != roots[0]:
                errors.append(f"Root XML filename {roots[0]} does not match NAME {package_name}")

            item_rows = list(root.findall("./ITEMS/ITEMS_ROW")) or list(root.findall(".//ITEMS_ROW"))
            additional_rows = list(root.findall("./ADDITIONAL_ITEMS/ADDITIONAL_ITEM")) or list(root.findall(".//ADDITIONAL_ITEM"))
            counts["items"] = len(item_rows)
            counts["additionalItems"] = len(additional_rows)

            referenced: list[str] = []
            for row in item_rows + additional_rows:
                filename = (row.findtext("FILENAME") or "").strip().replace("\\", "/")
                type_name = (row.findtext("TYPE") or "").strip()
                item_name = (row.findtext("NAME") or "").strip()
                if not filename:
                    errors.append(f"Manifest entry '{item_name}' is missing FILENAME")
                    continue
                if "/" in filename:
                    errors.append(f"FILENAME must be a basename under Items/, got {filename}")
                if not type_name:
                    errors.append(f"Manifest entry '{item_name or filename}' is missing TYPE")
                if not item_name:
                    errors.append(f"Manifest entry for {filename} is missing NAME")
                referenced.append(filename)
                entry = f"Items/{Path(filename).name}"
                if entry not in names:
                    errors.append(f"Referenced file is missing: {entry}")
                else:
                    try:
                        payload = archive.read(entry)
                        ET.fromstring(payload)
                    except ET.ParseError as exc:
                        errors.append(f"Item XML is invalid ({entry}): {exc}")
                    except KeyError:
                        errors.append(f"Referenced file cannot be read: {entry}")

            item_files = [name for name in names if name.startswith("Items/") and not name.endswith("/")]
            counts["itemFiles"] = len(item_files)
            referenced_set = set(referenced)
            for entry in item_files:
                basename = entry.split("/")[-1]
                if basename not in referenced_set:
                    errors.append(f"Extra item file is not referenced by the manifest: {entry}")
            if len(referenced) != len(set(referenced)):
                errors.append("Duplicate FILENAME values in APPLICATION_CONFIGURATION")
            if not item_rows and not additional_rows:
                errors.append("Package contains no ITEMS or ADDITIONAL_ITEMS")
    except zipfile.BadZipFile as exc:
        errors.append(f"Bad ZIP archive: {exc}")

    ok = not errors
    LOG.info("[Package Validator] %s %s", path.name, "PASS" if ok else "FAIL")
    return {"ok": ok, "errors": errors, "package": path.name, "counts": counts}
