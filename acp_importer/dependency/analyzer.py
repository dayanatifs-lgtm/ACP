"""Deterministic ACP dependency analyzer (Levels 1–2, Level 3 hook only)."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from .ai import AIDependencyResolver, NullAIDependencyResolver
from .graph import adjacency, deployment_levels, detect_cycles, nodes_affected_by_cycles
from .index import ObjectProviderIndex
from .models import (
    AMBIGUOUS,
    CONFIRMED,
    HIGH,
    LOW,
    MEDIUM,
    MISSING,
    REJECTED,
    RULE_ENGINE,
    STATIC_ANALYSIS,
    DependencyEdge,
    PackageInfo,
)
from .scanner import inspect

LOG = logging.getLogger("DependencyAnalyzer")


def _norm(symbol: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", symbol.casefold())


def _family_key(name: str) -> str:
    match = re.match(r"^([A-Za-z]+\d+)", name.strip())
    return (match.group(1) if match else name).casefold()


def _family_rank(name: str) -> tuple[int, ...]:
    match = re.match(r"^[A-Za-z]+\d+(?:-(.+))?$", name.strip())
    if not match or not match.group(1):
        return (0,)
    parts = re.split(r"[.-]", match.group(1))
    return tuple(int(part) if part.isdigit() else 0 for part in parts) or (0,)


class DependencyAnalyzer:
    """Orchestrates scan → index → resolve → graph → levels.

    Deployment uses CONFIRMED edges only. Ambiguous/missing/AI suggestions
    are reported and never turned into silent graph edges.
    """

    def __init__(self, ai_resolver: AIDependencyResolver | None = None) -> None:
        self.ai_resolver = ai_resolver or NullAIDependencyResolver()

    def analyze_all(self, acp_folder: Path, progress: Callable[[int, int, str], bool | None] | None = None) -> dict[str, Any]:
        packages, invalid = inspect(acp_folder, progress)
        return self.generate_report(acp_folder, packages, invalid)

    def generate_report(self, folder: Path, packages: list[PackageInfo], invalid: list[dict[str, str]]) -> dict[str, Any]:
        if not packages and not invalid:
            return self._empty_report(folder, "No .acp or .zip files were found in the selected folder.")

        by_name: dict[str, PackageInfo] = {}
        duplicates: list[str] = []
        seen_stems: set[str] = set()
        unique: list[PackageInfo] = []
        for package in packages:
            stem_key = package.path.stem.casefold()
            if stem_key in seen_stems:
                duplicates.append(package.path.name)
                continue
            seen_stems.add(stem_key)
            name_key = package.name.casefold()
            if name_key in by_name:
                duplicates.append(f"{package.name} ({package.path.name})")
                package.name = package.path.stem
                name_key = package.name.casefold()
            by_name[name_key] = package
            unique.append(package)
        index = ObjectProviderIndex()
        for package in unique:
            index.add_package(package)
        LOG.info("[DependencyAnalyzer] Built provider index")

        edges = self._resolve(unique, by_name, index)
        confirmed = [edge for edge in edges if edge.status == CONFIRMED]
        graph = adjacency(confirmed)
        names = [package.name for package in unique]
        cycles = detect_cycles(names, graph)
        blocked = nodes_affected_by_cycles(names, graph, cycles)
        # Every valid ACP is deployed. Cycles are reported and ordered last
        # among tied packages, but they are not omitted from the plan.
        types = {package.name: package.item_types for package in unique}
        levels = deployment_levels(names, graph, types)
        placed = {name for level in levels for name in level}
        unplaced = {name for name in names if name not in placed}
        blocked |= unplaced

        missing = [edge for edge in edges if edge.status == MISSING]
        ambiguous = [edge for edge in edges if edge.status == AMBIGUOUS]
        for edge in confirmed:
            LOG.info(
                "[DependencyAnalyzer] Detected dependency: %s -> %s Object: %s Type: %s Confidence: %s",
                edge.consumer_acp,
                edge.provider_acp,
                edge.object_name,
                edge.dependency_type,
                edge.confidence,
            )

        plan = []
        rows = []
        order_number = 0
        deps_by_consumer: dict[str, list[DependencyEdge]] = defaultdict(list)
        for edge in confirmed:
            deps_by_consumer[edge.consumer_acp].append(edge)
        for level_index, level_names in enumerate(levels):
            level_packages = []
            for name in level_names:
                order_number += 1
                package = by_name[name.casefold()]
                confirmed_deps = deps_by_consumer[name]
                row = {
                    "order": order_number,
                    "level": level_index,
                    "name": name,
                    "file": package.path.name,
                    "packageId": package.package_id,
                    "items": package.items,
                    "itemTypes": sorted(package.item_types),
                    "dependencies": [
                        {"name": edge.provider_acp, "confidence": edge.confidence, "object": edge.object_name, "type": edge.dependency_type}
                        for edge in confirmed_deps
                        if edge.provider_acp
                    ],
                    "confidence": confirmed_deps[0].confidence if confirmed_deps else "NONE",
                    "cyclic": name in blocked,
                    "blocked": False,
                }
                rows.append(row)
                level_packages.append(row)
            plan.append({"level": level_index, "count": len(level_packages), "packages": level_packages})

        blocked_rows = []
        for name in names:
            if name not in blocked:
                continue
            package = by_name[name.casefold()]
            blocked_rows.append({
                "order": next((row["order"] for row in rows if row["name"] == name), None),
                "level": next((row["level"] for row in rows if row["name"] == name), None),
                "name": name,
                "file": package.path.name,
                "packageId": package.package_id,
                "items": package.items,
                "itemTypes": sorted(package.item_types),
                "dependencies": [
                    {"name": edge.provider_acp, "confidence": edge.confidence, "object": edge.object_name, "type": edge.dependency_type}
                    for edge in deps_by_consumer[name]
                    if edge.provider_acp
                ],
                "confidence": "CYCLE" if any(name in cycle for cycle in cycles) else "BLOCKED",
                "cyclic": True,
                "blocked": False,
            })

        cycle_labels = [" -> ".join(cycle) for cycle in cycles]
        can_start = bool(rows)
        notes = (
            "Deterministic Levels 1–2 analysis. Deployment uses CONFIRMED edges only. "
            "Ambiguous providers are not chosen. Missing providers are not invented. "
            "Every valid ACP is included in the deployment plan. Circular packages are "
            "still imported in a best-effort order and are marked CYCLE. "
            "Level 3 AI suggestions, if any, are informational only."
        )
        if not can_start:
            notes = "No valid ACP packages were found to deploy."
        LOG.info("[DependencyAnalyzer] Analysis complete")
        return {
            "folder": str(folder),
            "packages": rows,
            "blockedPackages": blocked_rows,
            "duplicates": sorted(set(duplicates)),
            "missing": [
                {"package": edge.consumer_acp, "dependency": edge.object_name, "type": edge.dependency_type}
                for edge in missing
            ],
            "cycles": cycle_labels,
            "cyclePaths": cycles,
            "invalid": invalid,
            "canStart": can_start,
            "notes": notes,
            "summary": {
                "acpsScanned": len(packages) + len(invalid),
                "validPackages": len(unique),
                "objectsDiscovered": sum(len(package.objects) for package in unique),
                "dependenciesFound": len(edges),
                "resolved": len(confirmed),
                "ambiguous": len(ambiguous),
                "missing": len(missing),
                "circular": len(cycles),
                "blocked": len(blocked_rows),
                "levels": len(plan),
            },
            "deploymentPlan": plan,
            "edges": [edge.to_dict() for edge in edges],
            "missingDependencies": [edge.to_dict() for edge in missing],
            "ambiguousDependencies": [edge.to_dict() for edge in ambiguous],
            "duplicateDefinitions": index.duplicate_definitions(),
            "providerIndexSample": {key: value for key, value in list(index.as_dict().items())[:50]},
        }

    def _resolve(self, packages: list[PackageInfo], by_name: dict[str, PackageInfo], index: ObjectProviderIndex) -> list[DependencyEdge]:
        edges: list[DependencyEdge] = []
        seen: set[tuple[str, str, str, str]] = set()

        def add(edge: DependencyEdge) -> None:
            key = (edge.consumer_acp, edge.provider_acp or "", edge.object_name, edge.status)
            if key in seen:
                return
            seen.add(key)
            if edge.status in {AMBIGUOUS, MISSING}:
                suggestion = self.ai_resolver.resolve_dependency(
                    edge.consumer_acp,
                    edge.object_name,
                    edge.candidates,
                    edge.evidence,
                )
                if suggestion is not None:
                    edge.evidence = list(edge.evidence) + [
                        f"AI suggestion (not used for deployment): {suggestion.provider} ({suggestion.confidence}) {suggestion.reason}"
                    ]
            edges.append(edge)

        for package in packages:
            for reference in package.references:
                if reference.reference_type == "PACKAGE":
                    required = reference.reference_name
                    target = by_name.get(required.casefold())
                    if target and target.name == package.name:
                        add(DependencyEdge(
                            package.name, package.name, "PACKAGE", required, reference.source_file,
                            LOW, STATIC_ANALYSIS, REJECTED,
                            [f"{package.name} referenced itself and was ignored"],
                        ))
                        continue
                    if target and target.name != package.name:
                        add(DependencyEdge(
                            package.name, target.name, "PACKAGE", required, reference.source_file,
                            HIGH, STATIC_ANALYSIS, CONFIRMED,
                            [f"{package.name} declares package reference {required}", f"{target.name} is present in the folder"],
                        ))
                    elif required.casefold() != package.name.casefold():
                        add(DependencyEdge(
                            package.name, None, "PACKAGE", required, reference.source_file,
                            HIGH, STATIC_ANALYSIS, MISSING,
                            [f"{package.name} declares package reference {required}", "No ACP with that name was found"],
                        ))
                    continue

                if any(candidate["acp"] == package.name for candidate in index.lookup(reference.reference_name)):
                    continue
                owners = index.unique_acps(reference.reference_name, exclude=package.name)
                evidence = [f"{package.name} references {reference.reference_name}", f"Reference type {reference.reference_type}"]
                if not owners:
                    add(DependencyEdge(
                        package.name, None, reference.reference_type, reference.reference_name, reference.source_file,
                        MEDIUM, STATIC_ANALYSIS, MISSING, evidence + ["No provider ACP exists in the object index"],
                    ))
                elif len(owners) == 1:
                    add(DependencyEdge(
                        package.name, owners[0], reference.reference_type, reference.reference_name, reference.source_file,
                        HIGH, STATIC_ANALYSIS, CONFIRMED,
                        evidence + [f"{owners[0]} provides {reference.reference_name}"],
                    ))
                else:
                    add(DependencyEdge(
                        package.name, None, reference.reference_type, reference.reference_name, reference.source_file,
                        LOW, STATIC_ANALYSIS, AMBIGUOUS,
                        evidence + [f"Candidates: {', '.join(owners)}"],
                        owners,
                    ))

            # Numbered family: CMKT046 before CMKT046-1 (IFS naming convention).
            rank = _family_rank(package.name)
            for other in packages:
                if other.name == package.name or _family_key(other.name) != _family_key(package.name):
                    continue
                if _family_rank(other.name) < rank:
                    add(DependencyEdge(
                        package.name, other.name, "PACKAGE", other.name, package.path.name,
                        MEDIUM, RULE_ENGINE, CONFIRMED,
                        [f"{package.name} is a later numbered sibling of {other.name}", "Known IFS ACP family naming rule"],
                    ))

        # Self-references are recorded as rejected so they never enter the graph.
        for package in packages:
            if _norm(package.name) in package.requires:
                add(DependencyEdge(
                    package.name, package.name, "PACKAGE", package.name, package.path.name,
                    LOW, STATIC_ANALYSIS, REJECTED,
                    [f"{package.name} referenced itself and was ignored"],
                ))
        return edges

    @staticmethod
    def _empty_report(folder: Path, notes: str) -> dict[str, Any]:
        return {
            "folder": str(folder),
            "packages": [],
            "blockedPackages": [],
            "duplicates": [],
            "missing": [],
            "cycles": [],
            "cyclePaths": [],
            "invalid": [],
            "canStart": False,
            "notes": notes,
            "summary": {
                "acpsScanned": 0, "validPackages": 0, "objectsDiscovered": 0, "dependenciesFound": 0,
                "resolved": 0, "ambiguous": 0, "missing": 0, "circular": 0, "blocked": 0, "levels": 0,
            },
            "deploymentPlan": [],
            "edges": [],
            "missingDependencies": [],
            "ambiguousDependencies": [],
            "duplicateDefinitions": [],
            "providerIndexSample": {},
        }


def analyse(folder: Path, progress: Callable[[int, int, str], bool | None] | None = None) -> dict[str, Any]:
    return DependencyAnalyzer().analyze_all(folder, progress)
