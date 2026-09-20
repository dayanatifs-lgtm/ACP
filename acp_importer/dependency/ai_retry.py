"""AI-assisted retry after Direct ACP Clone failures.

Uses Gemini to read IFS error text and ACP XML, then import missing
dependencies / publish unpublished packages before retrying the failed ACP.
Does not change the original deterministic deployment plan.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

from ..client import IfsAcpClient
from .failures import failure_analyzer
from .gemini import GeminiError, generate_json, gemini_configured

LOG = logging.getLogger("AI Retry")

INSTRUCTIONS = """You are an IFS Application Configuration Package (ACP) import assistant.
A deterministic clone already ran. Some ACPs failed. Recommend the next actions.

Rules:
- Only choose package files from CATALOG. Never invent ACP names.
- Use the IFS ERROR MESSAGE plus FAILED ACP XML ITEMS to find missing providers
  (Custom LU, projection, enumeration, unpublished package, missing object).
- If an object is already in IFS but unpublished, put its ACP name in publishFirst.
- If a provider ACP is in the catalog and likely missing in IFS, put its file in importFirst.
- importFirst is the order to import BEFORE retrying the failed ACP. Keep it short (max 8).
- Return JSON only, no markdown:
{
  "summary": "one sentence",
  "importFirst": ["NAME.zip"],
  "publishFirst": ["PackageName"],
  "retryFailed": true,
  "reason": "why these packages",
  "missingObjects": ["ObjectName"]
}
"""


def acp_xml_snapshot(path: Path, *, limit: int = 12000) -> str:
    if not path.exists():
        return f"{path.name} was not found"
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name.replace("\\", "/") for name in archive.namelist()]
            roots = [name for name in names if name.lower().endswith(".xml") and "/" not in name]
            chunks: list[str] = []
            if roots:
                manifest = archive.read(roots[0]).decode("utf-8", errors="replace")
                chunks.append(f"ROOT {roots[0]}:\n{manifest[:4000]}")
                try:
                    root = ET.fromstring(manifest)
                    rows = []
                    for row in list(root.findall("./ITEMS/ITEMS_ROW")) + list(root.findall("./ADDITIONAL_ITEMS/ADDITIONAL_ITEM")):
                        rows.append(
                            f"{(row.findtext('TYPE') or '').strip()} | {(row.findtext('NAME') or '').strip()} | {(row.findtext('FILENAME') or '').strip()}"
                        )
                    if rows:
                        chunks.append("ITEMS:\n" + "\n".join(rows[:80]))
                except ET.ParseError:
                    pass
            for name in names:
                if not name.lower().startswith("items/") or not name.lower().endswith(".xml"):
                    continue
                text = archive.read(name).decode("utf-8", errors="replace")
                chunks.append(f"FILE {name}:\n{text[:1500]}")
                if sum(len(chunk) for chunk in chunks) > limit:
                    break
            return "\n\n".join(chunks)[:limit]
    except (OSError, zipfile.BadZipFile) as exc:
        return f"Could not read {path.name}: {exc}"


def catalog_from_analysis(analysis: dict[str, Any] | None, folder: Path) -> list[dict[str, Any]]:
    rows = []
    if analysis:
        for package in analysis.get("packages") or []:
            rows.append({
                "name": package.get("name"),
                "file": package.get("file"),
                "itemTypes": package.get("itemTypes") or [],
                "items": package.get("items"),
            })
        if rows:
            return rows
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".acp", ".zip"}):
        rows.append({"name": path.stem, "file": path.name, "itemTypes": [], "items": None})
    return rows


def _file_index(folder: Path, catalog: list[dict[str, Any]]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in {".acp", ".zip"}:
            index[path.name.casefold()] = path
            index[path.stem.casefold()] = path
    for row in catalog:
        file_name = str(row.get("file") or "")
        name = str(row.get("name") or "")
        path = folder / file_name if file_name else None
        if path and path.exists():
            index[file_name.casefold()] = path
            index[name.casefold()] = path
            index[Path(file_name).stem.casefold()] = path
    return index


def _match_files(values: list[Any], index: dict[str, Path]) -> list[Path]:
    matched: list[Path] = []
    seen: set[str] = set()
    for raw in values or []:
        key = Path(str(raw)).name.casefold()
        path = index.get(key) or index.get(Path(str(raw)).stem.casefold())
        if path and path.name not in seen:
            seen.add(path.name)
            matched.append(path)
    return matched


def advise_failure(
    *,
    failed_file: str,
    error_message: str,
    xml_snapshot: str,
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt = (
        f"{INSTRUCTIONS}\n\nFAILED ACP FILE: {failed_file}\n\n"
        f"ERROR MESSAGE:\n{error_message[:4000]}\n\n"
        f"FAILED ACP XML ITEMS:\n{xml_snapshot}\n\n"
        f"CATALOG (allowed packages only):\n{json.dumps(catalog[:400])}\n"
    )
    advice = generate_json(prompt)
    advice.setdefault("importFirst", [])
    advice.setdefault("publishFirst", [])
    advice.setdefault("retryFailed", True)
    advice.setdefault("summary", "")
    advice.setdefault("reason", "")
    advice.setdefault("missingObjects", [])
    return advice


def retry_failed_with_ai(
    *,
    client: IfsAcpClient,
    folder: Path,
    analysis: dict[str, Any] | None,
    failed: list[dict[str, Any]],
    already_ok: set[str],
    progress: Callable[[int, int, str], bool | None] | None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if not gemini_configured():
        raise GeminiError("GEMINI_API_KEY is not configured.")
    catalog = catalog_from_analysis(analysis, folder)
    index = _file_index(folder, catalog)
    succeeded = {name.casefold() for name in already_ok}
    results: list[dict[str, Any]] = []

    for number, item in enumerate(failed, start=1):
        if progress and progress(number, len(failed), item.get("name") or "") is False:
            break
        file_name = str(item.get("name") or "")
        path = index.get(file_name.casefold()) or index.get(Path(file_name).stem.casefold())
        if path is None:
            row = {"name": file_name, "success": False, "ai": True, "message": "AI retry skipped: ACP file was not found in the folder"}
            results.append(row)
            if on_result:
                on_result(row)
            continue
        try:
            advice = advise_failure(
                failed_file=path.name,
                error_message=str(item.get("message") or ""),
                xml_snapshot=acp_xml_snapshot(path),
                catalog=catalog,
            )
        except GeminiError as exc:
            row = {"name": path.name, "success": False, "ai": True, "message": f"AI advice failed: {exc}"}
            results.append(row)
            if on_result:
                on_result(row)
            continue

        notes = [advice.get("summary") or "", advice.get("reason") or ""]
        for dep in _match_files(advice.get("importFirst") or [], index):
            if dep.stem.casefold() in succeeded or dep.name.casefold() in succeeded:
                notes.append(f"Already imported: {dep.name}")
                continue
            LOG.info("[AI Retry] Importing suggested dependency %s before %s", dep.name, path.name)
            try:
                success, message = client.import_acp(dep)
            except Exception as exc:
                success, message = False, str(exc)
            dep_row = {
                "name": dep.name,
                "success": success,
                "ai": True,
                "message": f"AI import-first: {message}",
            }
            results.append(dep_row)
            if on_result:
                on_result(dep_row)
            if success:
                succeeded.add(dep.stem.casefold())
                succeeded.add(dep.name.casefold())
            else:
                failure_analyzer.record(acp=dep.stem, file=dep.name, message=message)

        for package_name in advice.get("publishFirst") or []:
            name = str(package_name).strip()
            if not name:
                continue
            LOG.info("[AI Retry] Publishing %s before retrying %s", name, path.name)
            try:
                success, message = client.publish_existing(name)
            except Exception as exc:
                success, message = False, str(exc)
            pub_row = {
                "name": name,
                "success": success,
                "ai": True,
                "message": f"AI publish-first: {message}",
            }
            results.append(pub_row)
            if on_result:
                on_result(pub_row)

        retry = bool(advice.get("retryFailed", True))
        if retry:
            try:
                success, message = client.import_acp(path)
            except Exception as exc:
                success, message = False, str(exc)
        else:
            success, message = False, "AI recommended not retrying this ACP yet"
        row = {
            "name": path.name,
            "success": success,
            "ai": True,
            "message": "AI retry: " + "; ".join(part for part in notes + [message] if part),
        }
        results.append(row)
        if on_result:
            on_result(row)
        if success:
            succeeded.add(path.stem.casefold())
            succeeded.add(path.name.casefold())
        else:
            failure_analyzer.record(acp=path.stem, file=path.name, message=message)
    return results
