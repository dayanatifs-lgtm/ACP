"""Models for the Repackage & Deploy workflow. Isolated from Direct ACP Clone."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CATEGORY_ORDER: tuple[str, ...] = (
    "CUSTOM_LU",
    "CUSTOM_ENUMERATION",
    "CF_PERSISTENT",
    "CF_READ_ONLY",
    "CF_VIEWS",
    "CUSTOM_PROJECTION",
    "CUSTOM_PROJCONFIG",
    "PROJECTION_ACTION",
    "CUSTOM_EVENT",
    "CUSTOM_EVENT_ACTION",
    "WORKFLOW",
    "CUSTOM_PAGE",
    "CUSTOM_TAB",
    "AURENA_PAGE_GROUP",
    "CUSTOM_MENU",
    "NAV_CONFIG",
    "APPEARANCE_CONFIG",
    "CONFIG_CONTEXT",
    "QUERY_ARTIFACT",
    "QUICK_REPORT",
)

ADDITIONAL_TYPES = {"CF_VIEWS"}

# Custom LUs/enumerations create DB objects and lock IFS cache when batched.
# Native IFS exports (e.g. test1.zip) ship one CUSTOM_LU per package.
CATEGORY_ITEM_LIMITS = {
    "CUSTOM_LU": 1,
    "CUSTOM_ENUMERATION": 1,
}

TYPE_ALIASES = {
    "CF_VIEW": "CF_VIEWS",
    "PROJECTION_CONFIGURATION": "CUSTOM_PROJCONFIG",
    "APPEARANCE": "APPEARANCE_CONFIG",
    "NAVIGATOR": "NAV_CONFIG",
}


def canonical_type(value: str) -> str:
    cleaned = (value or "").strip().upper()
    return TYPE_ALIASES.get(cleaned, cleaned)


def category_rank(object_type: str) -> int:
    try:
        return CATEGORY_ORDER.index(canonical_type(object_type))
    except ValueError:
        return 100 + abs(hash(object_type)) % 50


@dataclass
class ACPItem:
    id: str
    source_acp: str
    source_acp_path: str
    package_name: str
    object_type: str
    object_name: str
    description: str
    source_filename: str
    source_section: str
    xml_content: str
    zip_entry: str
    metadata: dict[str, Any] = field(default_factory=dict)
    references: list[dict[str, str]] = field(default_factory=list)
    xml_bytes: bytes = field(default_factory=bytes)
    source_author: str = ""
    source_origin: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sourceAcp": self.source_acp,
            "packageName": self.package_name,
            "objectType": self.object_type,
            "objectName": self.object_name,
            "description": self.description,
            "sourceFilename": self.source_filename,
            "sourceSection": self.source_section,
            "references": self.references,
            "metadata": self.metadata,
        }


@dataclass
class ParsedPackage:
    path: str
    zip_name: str
    root_xml_name: str
    name: str
    package_id: str
    author: str
    description: str
    version: str
    version_time_stamp: str
    origin: str
    last_modified_date: str
    export_def_version: str
    items: list[ACPItem]
    errors: list[str] = field(default_factory=list)
    raw_root_xml: str = ""
