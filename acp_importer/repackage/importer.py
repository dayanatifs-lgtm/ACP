"""Import generated ACPs using the existing IFS client. Isolated from Direct Clone ordering."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from ..client import IfsAcpClient
from ..dependency.failures import failure_analyzer
from .validator import validate_generated_package

LOG = logging.getLogger("Deployment")


def load_manifest(output_folder: Path) -> dict[str, Any]:
    folder = Path(output_folder)
    path = folder / "deployment-manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"deployment-manifest.json was not found in {folder}. "
            "Analyse & Build Packages using this output folder first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(output_folder: Path, manifest: dict[str, Any]) -> None:
    (output_folder / "deployment-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def packages_for_category(manifest: dict[str, Any], category: str | None) -> list[dict[str, Any]]:
    rows = sorted(manifest.get("packages") or [], key=lambda row: int(row.get("sequence") or 0))
    if category:
        rows = [row for row in rows if row.get("category") == category]
    return rows


def unsatisfied_dependencies(package: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    success = {
        Path(row["package"]).stem
        for row in manifest.get("packages") or []
        if row.get("importStatus") == "SUCCESS"
    }
    missing: list[str] = []
    for dependency in package.get("dependsOnPackages") or []:
        stem = Path(str(dependency)).stem
        if stem not in success:
            missing.append(stem)
    return missing


def import_packages(
    *,
    client: IfsAcpClient,
    output_folder: Path,
    category: str | None,
    progress: Callable[[int, int, str], bool | None] | None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(output_folder)
    selected = packages_for_category(manifest, category)
    for index, package in enumerate(selected, start=1):
        if progress and progress(index, len(selected), package["package"]) is False:
            break
        path = output_folder / package["path"]
        validation = validate_generated_package(path)
        package["validation"] = validation
        if not validation.get("ok"):
            package["status"] = "INVALID"
            package["importStatus"] = "SKIPPED"
            package["importMessage"] = "Import blocked: package failed ZIP/ACP structure validation. " + "; ".join(
                validation.get("errors") or []
            )
            if on_result:
                on_result(package)
            continue
        package["status"] = "READY"
        missing = unsatisfied_dependencies(package, manifest)
        if missing:
            package["importStatus"] = "BLOCKED"
            package["importMessage"] = f"Cannot import yet. Required package: {', '.join(missing)}"
            if on_result:
                on_result(package)
            continue
        if package.get("importStatus") == "SUCCESS":
            continue
        package["importStatus"] = "IMPORTING"
        LOG.info("[Deployment] Importing %s", package["package"])
        try:
            success, message = client.import_acp(path)
        except Exception as exc:
            success, message = False, str(exc)
        package["importStatus"] = "SUCCESS" if success else "FAILED"
        package["importMessage"] = message
        if not success:
            failure_analyzer.record(acp=package["package"], file=package["package"], message=message)
        if on_result:
            on_result(package)
    save_manifest(output_folder, manifest)
    return manifest
