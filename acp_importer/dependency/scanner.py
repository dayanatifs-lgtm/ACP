"""ACP archive scanner and object/reference extraction (Level 1)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

from .models import ACPObject, ACPReference, CloneAnalysisCancelled, CloneAnalysisError, PackageInfo

LOG = logging.getLogger("DependencyAnalyzer")
_MAX_XML_BYTES = 2_000_000
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".acp_meta_cache"


def package_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in {".acp", ".zip"})


def _norm(symbol: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", symbol.casefold())


def _add_exact(target: set[str], *values: str) -> None:
    for value in values:
        cleaned = (value or "").strip()
        if len(cleaned) < 3:
            continue
        target.add(_norm(cleaned))


def _add_cf(target: set[str], attr: str) -> None:
    cleaned = (attr or "").strip()
    if not cleaned:
        return
    if cleaned.upper().startswith("CF$_"):
        cleaned = cleaned[4:]
    elif cleaned.upper().startswith("CF_"):
        cleaned = cleaned[3:]
    if len(cleaned) < 3:
        return
    _add_exact(target, cleaned, f"CF$_{cleaned}", f"CF_{cleaned}")


def _unescape(text: str) -> str:
    return (
        text.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def _manifest(archive: zipfile.ZipFile) -> tuple[str, str]:
    candidates = [entry for entry in archive.namelist() if entry.lower().endswith(".xml") and "/" not in entry.strip("/")]
    if not candidates:
        raise CloneAnalysisError("No root ACP manifest XML was found")
    for candidate in candidates:
        text = archive.read(candidate).decode("utf-8", errors="replace")
        if "APPLICATION_CONFIGURATION" in text:
            return candidate, text
    return candidates[0], archive.read(candidates[0]).decode("utf-8", errors="replace")


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(path.stat().st_size).encode())
    digest.update(str(int(path.stat().st_mtime_ns)).encode())
    with path.open("rb") as handle:
        chunk = handle.read(65_536)
        digest.update(chunk)
    return digest.hexdigest()


def _cache_path(path: Path, checksum: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{path.resolve()}|{checksum}".encode()).hexdigest()
    return _CACHE_DIR / f"{key}.json"


def _package_from_cache(path: Path, checksum: str) -> PackageInfo | None:
    cache = _cache_path(path, checksum)
    if not cache.exists():
        return None
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return PackageInfo(
        path=path,
        name=payload["name"],
        package_id=payload.get("package_id", ""),
        items=payload.get("items", 0),
        manifest_text=payload.get("manifest_text", ""),
        item_types=set(payload.get("item_types", [])),
        provides=set(payload.get("provides", [])),
        requires=set(payload.get("requires", [])),
        checksum=checksum,
        xml_files=list(payload.get("xml_files", [])),
        objects=[ACPObject(**item) for item in payload.get("objects", [])],
        references=[ACPReference(**item) for item in payload.get("references", [])],
    )


def _write_cache(package: PackageInfo) -> None:
    cache = _cache_path(package.path, package.checksum)
    cache.write_text(
        json.dumps(
            {
                "name": package.name,
                "package_id": package.package_id,
                "items": package.items,
                "manifest_text": package.manifest_text,
                "item_types": sorted(package.item_types),
                "provides": sorted(package.provides),
                "requires": sorted(package.requires),
                "xml_files": package.xml_files,
                "objects": [item.to_dict() for item in package.objects],
                "references": [item.to_dict() for item in package.references],
            }
        ),
        encoding="utf-8",
    )


def extract_from_xml(acp_id: str, source_file: str, text: str, *, is_manifest: bool) -> tuple[set[str], set[str], set[str], list[ACPObject], list[ACPReference]]:
    """Extract item types, provided symbols, required symbols, objects, and references."""
    item_types: set[str] = set()
    provides: set[str] = set()
    requires: set[str] = set()
    objects: list[ACPObject] = []
    references: list[ACPReference] = []
    body = _unescape(text)

    if is_manifest:
        for row in re.finditer(r"(?is)<ITEMS_ROW>(.*?)</ITEMS_ROW>", text):
            block = row.group(1)
            item_type = (re.search(r"(?is)<TYPE>\s*([^<]+)", block) or [None, ""])[1].strip().upper()
            item_name = (re.search(r"(?is)<NAME>\s*([^<]+)", block) or [None, ""])[1].strip()
            filename = (re.search(r"(?is)<FILENAME>\s*([^<]+)", block) or [None, ""])[1].strip()
            if item_type:
                item_types.add(item_type)
            objects.append(ACPObject(acp_id, item_type or "UNKNOWN", item_name or filename, filename or source_file, {"filename": filename}))
            stem = Path(filename).stem if filename else ""

            if item_type == "CUSTOM_LU":
                lu = ""
                if filename and "CustomLogicalUnit-" in filename:
                    lu = stem.split("CustomLogicalUnit-", 1)[-1]
                elif item_name:
                    lu = re.sub(r"[^A-Za-z0-9]", "", item_name)
                if lu:
                    _add_exact(provides, lu, lu + "Set")
                    objects.append(ACPObject(acp_id, "CUSTOM_LU", lu, filename or source_file, {}))
            elif item_type == "CUSTOM_EVENT":
                _add_exact(provides, item_name)
            elif item_type == "CUSTOM_EVENT_ACTION" and "^" in item_name:
                parts = item_name.split("^")
                if len(parts) >= 2:
                    _add_exact(requires, parts[1])
                    references.append(ACPReference(acp_id, "CUSTOM_EVENT", parts[1], filename or source_file, {"rule": "event_action_requires_event"}))
            elif item_type == "WORKFLOW":
                _add_exact(provides, item_name)
            elif item_type in {"CF_PERSISTENT", "CF_READ_ONLY", "CF_VIEW"} and "-" in stem:
                attr = stem.rsplit("-", 1)[-1]
                _add_cf(provides, attr)
                objects.append(ACPObject(acp_id, item_type, attr, filename or source_file, {}))
            elif item_type == "CUSTOM_ENUMERATION":
                _add_exact(provides, item_name)
            elif item_type in {"PROJECTION_CONFIGURATION", "PROJECTION_ACTION"}:
                if item_name:
                    _add_exact(provides, item_name)

        for required in re.findall(
            r"<(?:DEPENDENC(?:Y|IES)|REQUIR(?:ED|ES)?_?PACKAGE|PACKAGE_?REFERENCE)[^>]*>\s*([^<\s]+)",
            text,
            flags=re.I,
        ):
            references.append(ACPReference(acp_id, "PACKAGE", required.strip(), source_file, {"rule": "explicit_package_reference"}))

    for match in re.finditer(r"(?is)<LU>\s*([A-Za-z][A-Za-z0-9_]*)\s*</LU>", body):
        lu = match.group(1)
        _add_exact(provides, lu, lu + "Set")
        objects.append(ACPObject(acp_id, "CUSTOM_LU", lu, source_file, {"from": "LU"}))
    for match in re.finditer(r"(?is)<EVENT_ID>\s*([A-Za-z0-9_]+)\s*</EVENT_ID>", body):
        _add_exact(provides, match.group(1))
        objects.append(ACPObject(acp_id, "CUSTOM_EVENT", match.group(1), source_file, {}))

    for pattern, ref_type in (
        (r"(?is)ProjectionEntitySetName[^>]*>\s*([A-Za-z][A-Za-z0-9_]*)", "PROJECTION"),
        (r"(?is)<(?:PROJECTION_NAME|ProjectionName|PROJECTION)>\s*([A-Za-z][A-Za-z0-9_]*)", "PROJECTION"),
        (r"(?is)<(?:ENUMERATION|EnumerationName|CLIENT_ENUMERATION)>\s*([A-Za-z][A-Za-z0-9_]*)", "CUSTOM_ENUMERATION"),
        (r"(?is)(?:processDefinitionKey|processId|workflowId|PROCESS_KEY|BPMN_PROCESS_ID)[\"'=\s>:]+([A-Za-z][A-Za-z0-9_]{2,})", "WORKFLOW"),
    ):
        for match in re.finditer(pattern, body):
            name = match.group(1)
            _add_exact(requires, name)
            references.append(ACPReference(acp_id, ref_type, name, source_file, {"rule": "xml_reference"}))

    for match in re.finditer(r"CF\$_([A-Za-z0-9_]+)", body):
        _add_cf(requires, match.group(1))
        references.append(ACPReference(acp_id, "CUSTOM_FIELD", match.group(1), source_file, {"rule": "custom_field"}))
    for match in re.finditer(r"(?is)<MODIFIED_ATTRIBUTES>\s*([^<]+)", body):
        for attr in re.split(r"[;,\s]+", match.group(1)):
            if attr.upper().startswith("CF$_") or attr.upper().startswith("CF_"):
                _add_cf(requires, attr)
                references.append(ACPReference(acp_id, "CUSTOM_FIELD", attr, source_file, {"rule": "modified_attributes"}))

    requires -= provides
    return item_types, provides, requires, objects, references


def _scan_archive(path: Path, checksum: str) -> PackageInfo:
    with zipfile.ZipFile(path) as archive:
        manifest_name, manifest = _manifest(archive)
        root = ET.fromstring(manifest)
        name = (root.findtext("NAME") or path.stem).strip()
        package_id = (root.findtext("PACKAGE_ID") or "").strip()
        items = len(root.findall(".//ITEMS_ROW")) + len(root.findall(".//ADDITIONAL_ITEM"))
        item_types, provides, requires, objects, references = extract_from_xml(name, manifest_name, manifest, is_manifest=True)
        xml_files = [manifest_name]
        for entry in archive.infolist():
            filename = entry.filename
            if not filename.lower().endswith(".xml") or filename == manifest_name:
                continue
            if entry.file_size > _MAX_XML_BYTES:
                continue
            xml_files.append(filename)
            text = archive.read(entry).decode("utf-8", errors="replace")
            more_types, more_provides, more_requires, more_objects, more_refs = extract_from_xml(
                name, filename, text, is_manifest=False
            )
            item_types |= more_types
            provides |= more_provides
            requires |= more_requires
            objects.extend(more_objects)
            references.extend(more_refs)
        requires -= provides
        return PackageInfo(
            path=path,
            name=name,
            package_id=package_id,
            items=items,
            manifest_text=manifest,
            item_types=item_types,
            provides=provides,
            requires=requires,
            checksum=checksum,
            xml_files=xml_files,
            objects=objects,
            references=references,
        )


def inspect(folder: Path, progress: Callable[[int, int, str], bool | None] | None = None) -> tuple[list[PackageInfo], list[dict[str, str]]]:
    if not folder.exists() or not folder.is_dir():
        raise CloneAnalysisError(f"Folder does not exist: {folder}")
    packages: list[PackageInfo] = []
    invalid: list[dict[str, str]] = []
    files = package_files(folder)
    LOG.info("[DependencyAnalyzer] Scanning %s ACPs", len(files))
    for number, path in enumerate(files, 1):
        if progress and progress(number, len(files), path.name) is False:
            raise CloneAnalysisCancelled("Dependency analysis stopped by the user")
        try:
            checksum = _file_checksum(path)
            cached = _package_from_cache(path, checksum)
            package = cached or _scan_archive(path, checksum)
            if cached is None:
                try:
                    _write_cache(package)
                except OSError:
                    LOG.debug("Could not cache parsed metadata for %s", path.name)
            packages.append(package)
        except (OSError, zipfile.BadZipFile, ET.ParseError, CloneAnalysisError) as exc:
            invalid.append({"file": path.name, "reason": str(exc)})
    object_count = sum(len(package.objects) for package in packages)
    LOG.info("[DependencyAnalyzer] Extracted %s objects", object_count)
    return packages, invalid
