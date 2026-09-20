"""JSON/CSV export of a completed dependency analysis report."""

from __future__ import annotations

import csv
import io
import json
from typing import Any


def export_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2)


def export_csv(report: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "consumer", "provider", "object", "type", "status", "confidence", "detection", "evidence"])
    for edge in report.get("edges", []):
        writer.writerow([
            "dependency",
            edge.get("consumerAcp"),
            edge.get("providerAcp") or "",
            edge.get("objectName"),
            edge.get("dependencyType"),
            edge.get("status"),
            edge.get("confidence"),
            edge.get("detectionMethod"),
            " | ".join(edge.get("evidence") or []),
        ])
    for item in report.get("missingDependencies", []):
        writer.writerow(["missing", item.get("consumerAcp") or item.get("package"), "", item.get("objectName") or item.get("dependency"), item.get("dependencyType", ""), "MISSING", "", item.get("detectionMethod", "STATIC_ANALYSIS"), ""])
    for item in report.get("ambiguousDependencies", []):
        writer.writerow(["ambiguous", item.get("consumerAcp"), ";".join(item.get("candidates") or []), item.get("objectName"), item.get("dependencyType", ""), "AMBIGUOUS", "", item.get("detectionMethod", "STATIC_ANALYSIS"), ""])
    for cycle in report.get("cyclePaths", []) or report.get("cycles", []):
        path = " -> ".join(cycle) if isinstance(cycle, list) else str(cycle)
        writer.writerow(["cycle", path, "", "", "", "CYCLE", "", "", ""])
    for level in report.get("deploymentPlan", []):
        for package in level.get("packages", []):
            writer.writerow(["deployment", package.get("name"), "", "", f"level {level.get('level')}", "PLAN", "", "", package.get("file")])
    return buffer.getvalue()


def export_clone_results_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)


def export_clone_results_csv(payload: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["package", "status", "message", "environment", "folder"])
    environment = payload.get("environment") or ""
    folder = payload.get("folder") or ""
    for item in payload.get("results") or []:
        if item.get("skipped"):
            status = "SKIPPED"
        elif item.get("success"):
            status = "SUCCESS"
        else:
            status = "FAILED"
        writer.writerow([item.get("name"), status, item.get("message"), environment, folder])
    return buffer.getvalue()
