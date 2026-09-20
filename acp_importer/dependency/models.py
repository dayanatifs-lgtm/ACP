"""Shared models for ACP dependency analysis.

Level 1 = STATIC_ANALYSIS (XML extraction)
Level 2 = RULE_ENGINE (known IFS relationships)
Level 3 = FUTURE_AI (interface only; must not change the deployment graph)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class CloneAnalysisError(RuntimeError):
    """An ACP folder could not be analysed."""


class CloneAnalysisCancelled(CloneAnalysisError):
    """Dependency analysis was stopped by the user."""


STATIC_ANALYSIS = "STATIC_ANALYSIS"
RULE_ENGINE = "RULE_ENGINE"
FUTURE_AI = "FUTURE_AI"

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

CONFIRMED = "CONFIRMED"
AMBIGUOUS = "AMBIGUOUS"
MISSING = "MISSING"
REJECTED = "REJECTED"


@dataclass
class ACPObject:
    acp_id: str
    type: str
    name: str
    source_file: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ACPReference:
    acp_id: str
    reference_type: str
    reference_name: str
    source_file: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackageInfo:
    path: Path
    name: str
    package_id: str
    items: int
    manifest_text: str
    item_types: set[str] = field(default_factory=set)
    provides: set[str] = field(default_factory=set)
    requires: set[str] = field(default_factory=set)
    checksum: str = ""
    xml_files: list[str] = field(default_factory=list)
    objects: list[ACPObject] = field(default_factory=list)
    references: list[ACPReference] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.name


@dataclass
class DependencyEdge:
    consumer_acp: str
    provider_acp: str | None
    dependency_type: str
    object_name: str
    source_file: str
    confidence: str
    detection_method: str
    status: str
    evidence: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "consumerAcp": self.consumer_acp,
            "providerAcp": self.provider_acp,
            "dependencyType": self.dependency_type,
            "objectName": self.object_name,
            "sourceFile": self.source_file,
            "confidence": self.confidence,
            "detectionMethod": self.detection_method,
            "status": self.status,
            "evidence": self.evidence,
            "candidates": self.candidates,
        }
