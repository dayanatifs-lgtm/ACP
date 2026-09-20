"""ACP ZIP parser for Repackage & Deploy. Does not change Direct Clone scanning."""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from ..dependency.scanner import package_files
from .models import ACPItem, ParsedPackage, canonical_type

LOG = logging.getLogger("ACP Scanner")


def _text(node: ET.Element | None, tag: str) -> str:
    if node is None:
        return ""
    return (node.findtext(tag) or "").strip()


def _find_root_xml(archive: zipfile.ZipFile) -> tuple[str, str]:
    xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
    xml_names.sort(key=lambda name: (name.count("/"), name.lower()))
    for name in xml_names:
        payload = archive.read(name)
        head = payload[:4000].decode("utf-8", errors="replace")
        if "<APPLICATION_CONFIGURATION" in head:
            return name, payload.decode("utf-8", errors="replace")
    raise ValueError("No APPLICATION_CONFIGURATION root XML was found")


def _locate_item(archive: zipfile.ZipFile, filename: str) -> str | None:
    filename = filename.replace("\\", "/").strip()
    names = [name.replace("\\", "/") for name in archive.namelist()]
    if filename in names:
        return filename
    matches = [name for name in names if name.split("/")[-1] == filename.split("/")[-1]]
    return matches[0] if matches else None


def _extract_item_metadata(xml_content: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for tag in ("LU", "PROJECTION_NAME", "PAGE_NAME", "ATTACHED_PAGE", "EVENT_ID", "TABLE_NAME"):
        match = re.search(rf"(?is)<{tag}[^>]*>\s*([^<]+)", xml_content)
        if match:
            meta[tag] = match.group(1).strip()
    return meta


def _item_references(object_type: str, package_name: str, source_file: str, xml_content: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    patterns = [
        (r"(?is)ProjectionEntitySetName[^>]*>\s*([A-Za-z][A-Za-z0-9_]*)", "PROJECTION"),
        (r"(?is)<(?:PROJECTION_NAME|ProjectionName)>\s*([A-Za-z][A-Za-z0-9_]*)", "CUSTOM_PROJECTION"),
        (r"(?is)<ATTACHED_PAGE>\s*([A-Za-z][A-Za-z0-9_]*)", "CUSTOM_PAGE"),
        (r"(?is)(?:processDefinitionKey|processId|workflowId|PROCESS_KEY|BPMN_PROCESS_ID)[\"'=\s>:]+([A-Za-z][A-Za-z0-9_]{2,})", "WORKFLOW"),
        (r"CF\$_([A-Za-z0-9_]+)", "CUSTOM_FIELD"),
    ]
    if object_type != "CUSTOM_LU":
        patterns.append((r"(?is)<LU>\s*([A-Za-z][A-Za-z0-9_]*)\s*</LU>", "CUSTOM_LU"))
    if object_type not in {"CUSTOM_PROJECTION", "CUSTOM_PROJCONFIG"}:
        # Projection name on a page/config consumer is a dependency; on the provider it is identity.
        pass
    else:
        patterns = [item for item in patterns if "PROJECTION_NAME" not in item[0]]
    for pattern, ref_type in patterns:
        for match in re.finditer(pattern, xml_content):
            refs.append({"referenceType": ref_type, "referenceName": match.group(1), "sourceFile": source_file})
    return refs


def parse_acp_package(path: Path) -> ParsedPackage:
    LOG.info("[ACP Scanner] Parsed package %s", path.name)
    with zipfile.ZipFile(path) as archive:
        root_name, root_xml = _find_root_xml(archive)
        root = ET.fromstring(root_xml)
        package_name = _text(root, "NAME") or path.stem
        items: list[ACPItem] = []
        errors: list[str] = []
        for section, tag in (("ITEMS", "ITEMS_ROW"), ("ADDITIONAL_ITEMS", "ADDITIONAL_ITEM")):
            parent = root.find(section)
            rows = parent.findall(tag) if parent is not None else root.findall(f".//{tag}")
            for row in rows:
                object_type = canonical_type(_text(row, "TYPE"))
                object_name = _text(row, "NAME")
                filename = _text(row, "FILENAME")
                if not object_type or not filename:
                    errors.append(f"{package_name}: {section} row is missing TYPE or FILENAME")
                    continue
                entry = _locate_item(archive, filename)
                if entry is None:
                    errors.append(f"{package_name}: missing item file {filename}")
                    continue
                payload = archive.read(entry)
                xml_content = payload.decode("utf-8", errors="replace")
                item_id = f"{package_name}:{object_type}:{object_name}"
                items.append(
                    ACPItem(
                        id=item_id,
                        source_acp=package_name,
                        source_acp_path=str(path),
                        package_name=package_name,
                        object_type=object_type,
                        object_name=object_name,
                        description=_text(row, "DESCRIPTION"),
                        source_filename=filename.split("/")[-1],
                        source_section=section,
                        xml_content=xml_content,
                        zip_entry=entry,
                        metadata=_extract_item_metadata(xml_content),
                        references=_item_references(object_type, package_name, filename, xml_content),
                        xml_bytes=payload,
                        source_author=_text(root, "AUTHOR"),
                        source_origin=_text(root, "ORIGIN"),
                    )
                )
        LOG.info("[ACP Scanner] Found %s items", len(items))
        return ParsedPackage(
            path=str(path),
            zip_name=path.name,
            root_xml_name=root_name,
            name=package_name,
            package_id=_text(root, "PACKAGE_ID"),
            author=_text(root, "AUTHOR"),
            description=_text(root, "DESCRIPTION"),
            version=_text(root, "VERSION"),
            version_time_stamp=_text(root, "VERSION_TIME_STAMP"),
            origin=_text(root, "ORIGIN"),
            last_modified_date=_text(root, "LAST_MODIFIED_DATE"),
            export_def_version=_text(root, "EXPORT_DEF_VERSION") or "1",
            items=items,
            errors=errors,
            raw_root_xml=root_xml,
        )


def scan_source_folder(
    folder: Path,
    progress: Callable[[int, int, str], bool | None] | None = None,
) -> tuple[list[ParsedPackage], list[dict[str, str]]]:
    files = package_files(folder)
    LOG.info("[ACP Scanner] Found %s ACP packages", len(files))
    packages: list[ParsedPackage] = []
    invalid: list[dict[str, str]] = []
    for index, path in enumerate(files, start=1):
        if progress and progress(index, len(files), path.name) is False:
            raise InterruptedError("Repackage analysis stopped by the user")
        try:
            packages.append(parse_acp_package(path))
        except (OSError, zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
            invalid.append({"file": path.name, "reason": str(exc)})
    return packages, invalid
