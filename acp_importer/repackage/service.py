"""RepackagingService: scan → plan → generate → manifest. Does not import to IFS."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .builder import ApplicationConfigurationBuilder, ifs_timestamp, new_package_id, unique_item_filenames, write_package
from .models import ACPItem
from .parser import scan_source_folder
from .planner import (
    ItemProviderIndex,
    analyze_item_dependencies,
    dedupe_items,
    identify_logical_groups,
    merge_cycles_into_groups,
    pack_groups,
)
from .validator import validate_generated_package

LOG = logging.getLogger("Package Planner")


def _package_dependencies(packages: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    item_to_package = {}
    for package in packages:
        for item in package["items"]:
            item_to_package[item.id] = package["name"]
    for package in packages:
        depends: set[str] = set()
        for edge in edges:
            if edge["status"] != "CONFIRMED":
                continue
            if edge["consumerItem"] in {item.id for item in package["items"]}:
                provider_pkg = item_to_package.get(str(edge.get("providerItem") or ""))
                if provider_pkg and provider_pkg != package["name"]:
                    depends.add(provider_pkg)
        package["dependsOnPackages"] = sorted(depends)


def _topo_packages(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = {package["name"]: set(package.get("dependsOnPackages") or []) for package in packages}
    by_name = {package["name"]: package for package in packages}
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [name for name, deps in remaining.items() if not deps]
        if not ready:
            ordered.extend(by_name[name] for name in remaining)
            break
        ready.sort(key=lambda name: (by_name[name]["category"], name))
        for name in ready:
            ordered.append(by_name[name])
            del remaining[name]
        ready_set = set(ready)
        for deps in remaining.values():
            deps.difference_update(ready_set)
    for index, package in enumerate(ordered, start=1):
        package["sequence"] = index
    return ordered


class RepackagingService:
    def analyse_and_build(
        self,
        source_folder: Path,
        output_folder: Path,
        max_items: int = 10,
        progress: Callable[[int, int, str], bool | None] | None = None,
    ) -> dict[str, Any]:
        source_folder = source_folder.resolve()
        output_folder = output_folder.resolve()
        output_folder.mkdir(parents=True, exist_ok=True)
        parsed, invalid = scan_source_folder(source_folder, progress)
        items: list[ACPItem] = [item for package in parsed for item in package.items]
        pre_index = ItemProviderIndex()
        for item in items:
            pre_index.add(item)
        duplicate_definitions = pre_index.duplicate_definitions()
        items, duplicate_warnings = dedupe_items(items)
        index = ItemProviderIndex()
        for item in items:
            index.add(item)
        edges = analyze_item_dependencies(items, index)
        groups = identify_logical_groups(items)
        groups, contained_cycles = merge_cycles_into_groups(groups, edges)
        planned, warnings = pack_groups(groups, max(1, int(max_items)))
        warnings = duplicate_warnings + warnings
        _package_dependencies(planned, edges)
        planned = _topo_packages(planned)

        generated = []
        for package in planned:
            pairs = unique_item_filenames(package["items"])
            sources = sorted({item.source_acp for item in package["items"]})
            first = package["items"][0]
            origins = [item.source_origin for item in package["items"] if item.source_origin]
            authors = [item.source_author for item in package["items"] if item.source_author]
            root_xml = ApplicationConfigurationBuilder(
                name=package["name"],
                package_id=new_package_id(),
                description="",
                author=authors[0] if authors else "ACP Repackager",
                origin=origins[0] if origins else first.source_origin or "ACP-REPACKAGE",
                version_time_stamp=ifs_timestamp(),
                last_modified_date=ifs_timestamp(),
            ).build(pairs)
            files = [
                (filename, item.xml_bytes or item.xml_content.encode("utf-8"))
                for item, filename in pairs
            ]
            relative = Path(package["category"]) / f"{package['name']}.zip"
            path = output_folder / relative
            write_package(path, package["name"], root_xml, files)
            validation = validate_generated_package(path)
            status = "READY" if validation["ok"] else "INVALID"
            generated.append({
                "sequence": package["sequence"],
                "category": package["category"],
                "package": f"{package['name']}.zip",
                "path": relative.as_posix(),
                "sourceAcps": sources,
                "items": [{"id": item.id, "type": item.object_type, "name": item.object_name, "sourceAcp": item.source_acp} for item in package["items"]],
                "dependsOnPackages": package.get("dependsOnPackages") or [],
                "status": status,
                "oversized": package.get("oversized", False),
                "validation": validation,
                "importStatus": "PENDING",
            })

        categories = []
        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in generated:
            by_cat[row["category"]].append(row)
        original_counts: dict[str, int] = defaultdict(int)
        for item in items:
            original_counts[item.object_type] += 1
        for name, rows in by_cat.items():
            categories.append({
                "name": name,
                "packageCount": len(rows),
                "items": sum(len(row["items"]) for row in rows),
                "originalItems": original_counts.get(name, 0),
                "ready": sum(1 for row in rows if row["status"] == "READY"),
                "imported": 0,
                "failed": 0,
            })
        categories.sort(key=lambda row: row["name"])
        report = {
            "sourceFolder": str(source_folder),
            "outputFolder": str(output_folder),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "maxItemsPerPackage": max_items,
            "summary": {
                "acpsScanned": len(parsed) + len(invalid),
                "validPackages": len(parsed),
                "itemsDiscovered": len(items),
                "dependenciesDetected": len(edges),
                "confirmed": sum(1 for edge in edges if edge["status"] == "CONFIRMED"),
                "ambiguous": sum(1 for edge in edges if edge["status"] == "AMBIGUOUS"),
                "missing": sum(1 for edge in edges if edge["status"] == "MISSING"),
                "circular": len(contained_cycles),
                "generatedPackages": len(generated),
                "deploymentReady": bool(generated) and all(row["status"] == "READY" for row in generated),
            },
            "categories": categories,
            "packages": generated,
            "edges": edges,
            "ambiguousDependencies": [edge for edge in edges if edge["status"] == "AMBIGUOUS"],
            "missingDependencies": [edge for edge in edges if edge["status"] == "MISSING"],
            "cyclePaths": contained_cycles,
            "duplicateDefinitions": duplicate_definitions,
            "invalid": invalid,
            "warnings": warnings,
            "notes": "Generated ACPs follow native IFS export layout (APPLICATION_CONFIGURATION + Items/). Custom LUs are one per package. Original source files were not modified.",
        }
        (output_folder / "deployment-manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        LOG.info("[Package Planner] Wrote deployment-manifest.json")
        return report
