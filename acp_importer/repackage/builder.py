"""Build valid IFS ACP ZIP archives saved with a .zip extension.

A generated package is NEVER a plain XML file or a renamed folder.
It is always ZIP bytes containing:

    <NAME>.xml                 APPLICATION_CONFIGURATION manifest
    Items/<filename>.xml       copied item payloads

Structural reference packages:
    CDOC004.zip  (14 ITEMS)
    CESG005.zip  (2 ITEMS + 1 ADDITIONAL_ITEMS / CF_VIEWS)
"""

from __future__ import annotations

import logging
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .models import ACPItem, ADDITIONAL_TYPES, ParsedPackage

LOG = logging.getLogger("Package Planner")


def ifs_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H.%M.%S")


def new_package_id() -> str:
    return uuid.uuid4().hex.upper()


def unique_item_filenames(items: list[ACPItem]) -> list[tuple[ACPItem, str]]:
    used: set[str] = set()
    result: list[tuple[ACPItem, str]] = []
    for item in items:
        base = Path(item.source_filename.replace("\\", "/")).name
        filename = base
        if filename in used:
            filename = f"{item.source_acp}_{base}"
        used.add(filename)
        result.append((item, filename))
    return result


class ApplicationConfigurationBuilder:
    """Rebuild APPLICATION_CONFIGURATION for exactly the selected items."""

    def __init__(
        self,
        *,
        name: str,
        package_id: str,
        description: str = "",
        author: str = "ACP Repackager",
        origin: str = "ACP-REPACKAGE",
        export_def_version: str = "1",
        version: str = "",
        version_time_stamp: str | None = None,
        last_modified_date: str | None = None,
    ) -> None:
        self.name = name
        self.package_id = package_id
        self.description = description
        self.author = author
        self.origin = origin
        self.export_def_version = export_def_version or "1"
        self.version = version
        self.version_time_stamp = version_time_stamp or ifs_timestamp()
        self.last_modified_date = last_modified_date or self.version_time_stamp

    @staticmethod
    def _section_tag(item: ACPItem) -> str:
        # Preserve ADDITIONAL_ITEMS semantics observed in CESG005 (CF_VIEWS).
        if item.source_section == "ADDITIONAL_ITEMS" or item.object_type in ADDITIONAL_TYPES:
            return "ADDITIONAL_ITEM"
        return "ITEMS_ROW"

    @staticmethod
    def _leaf(tag: str, value: str, indent: str = "  ") -> str:
        # Match native IFS exports (test1.zip): empty fields are self-closing.
        if value == "":
            return f"{indent}<{tag}/>\n"
        return f"{indent}<{tag}>{escape(value)}</{tag}>\n"

    @classmethod
    def _row(cls, tag: str, item: ACPItem, filename: str) -> str:
        return (
            f"    <{tag}>\n"
            f"{cls._leaf('NAME', item.object_name, '      ')}"
            f"{cls._leaf('TYPE', item.object_type, '      ')}"
            f"{cls._leaf('DESCRIPTION', item.description, '      ')}"
            f"{cls._leaf('FILENAME', filename, '      ')}"
            f"    </{tag}>\n"
        )

    def build(self, items: list[tuple[ACPItem, str]]) -> str:
        body_items: list[str] = []
        additional: list[str] = []
        for item, filename in items:
            tag = self._section_tag(item)
            xml = self._row(tag, item, filename)
            if tag == "ADDITIONAL_ITEM":
                additional.append(xml)
            else:
                body_items.append(xml)
        additional_block = (
            f"  <ADDITIONAL_ITEMS>\n{''.join(additional)}  </ADDITIONAL_ITEMS>\n"
            if additional
            else "  <ADDITIONAL_ITEMS/>\n"
        )
        return (
            '<?xml version="1.0"?>\n'
            "<APPLICATION_CONFIGURATION>\n"
            f"{self._leaf('EXPORT_DEF_VERSION', self.export_def_version)}"
            f"{self._leaf('PACKAGE_ID', self.package_id)}"
            f"{self._leaf('NAME', self.name)}"
            f"{self._leaf('DESCRIPTION', self.description)}"
            f"{self._leaf('AUTHOR', self.author)}"
            f"{self._leaf('VERSION', self.version)}"
            f"{self._leaf('VERSION_TIME_STAMP', self.version_time_stamp)}"
            f"{self._leaf('ORIGIN', self.origin)}"
            f"{self._leaf('LAST_MODIFIED_DATE', self.last_modified_date)}"
            "  <ITEMS>\n"
            f"{''.join(body_items)}"
            "  </ITEMS>\n"
            f"{additional_block}"
            "</APPLICATION_CONFIGURATION>\n"
        )


def write_package(path: Path, name: str, root_xml: str, files: list[tuple[str, bytes]]) -> None:
    """Create a temporary package directory, then ZIP it to a .zip file.

    The resulting file must be openable with zipfile.ZipFile and contain the
    same layout as customer packages such as CDOC004 / CESG005.
    """
    if path.suffix.lower() != ".zip":
        raise ValueError(f"Generated package must use .zip extension, got {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    root_name = f"{name}.xml"
    with tempfile.TemporaryDirectory(prefix="acp_pkg_") as raw:
        staging = Path(raw)
        items_dir = staging / "Items"
        items_dir.mkdir(parents=True, exist_ok=True)
        (staging / root_name).write_bytes(root_xml.encode("utf-8"))
        for filename, payload in files:
            # FILENAME in the manifest is the basename under Items/.
            (items_dir / filename).write_bytes(payload)

        # Match native IFS order (test1.zip): Items/* first, then root XML.
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item_file in sorted(items_dir.iterdir()):
                info = zipfile.ZipInfo(f"Items/{item_file.name}")
                info.create_system = 0
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, item_file.read_bytes())
            info = zipfile.ZipInfo(root_name)
            info.create_system = 0
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (staging / root_name).read_bytes())

    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"{path.name} was written but is not a valid ZIP archive")
    LOG.info("[Package Planner] Wrote ZIP package %s (%s bytes)", path.name, path.stat().st_size)


def build_root_xml(
    *,
    name: str,
    package_id: str,
    description: str,
    author: str,
    origin: str,
    items: list[tuple[ACPItem, str]],
    export_def_version: str = "1",
    version: str = "",
    version_time_stamp: str | None = None,
    last_modified_date: str | None = None,
) -> str:
    return ApplicationConfigurationBuilder(
        name=name,
        package_id=package_id,
        description=description,
        author=author,
        origin=origin,
        export_def_version=export_def_version,
        version=version,
        version_time_stamp=version_time_stamp,
        last_modified_date=last_modified_date,
    ).build(items)


def reconstruct_package(parsed: ParsedPackage, output_path: Path) -> Path:
    """Round-trip an original ACP with a rebuilt APPLICATION_CONFIGURATION."""
    pairs = unique_item_filenames(parsed.items)
    root_xml = ApplicationConfigurationBuilder(
        name=parsed.name,
        package_id=parsed.package_id or new_package_id(),
        description=parsed.description,
        author=parsed.author or "thor",
        origin=parsed.origin,
        export_def_version=parsed.export_def_version,
        version=parsed.version,
        version_time_stamp=parsed.version_time_stamp or ifs_timestamp(),
        last_modified_date=parsed.last_modified_date or ifs_timestamp(),
    ).build(pairs)
    files = [
        (filename, item.xml_bytes or item.xml_content.encode("utf-8"))
        for item, filename in pairs
    ]
    write_package(output_path, parsed.name, root_xml, files)
    return output_path
