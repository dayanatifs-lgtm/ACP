"""Local web dashboard for running ACP imports without a terminal command."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import requests
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import clear_session_cookie, current_user, set_session_cookie
from .auth import service as auth_service
from .auth.permissions import (
    check_api_permission,
    first_allowed_path,
    page_access_denied,
    permissions_for_request,
)
from .auth.permissions_catalog import catalog_as_dict
from .auth.permissions_store import (
    assign_permission_set,
    create_permission_set,
    delete_permission_set,
    effective_grants_for_email,
    get_permission_set,
    get_user_permission_detail,
    init_permissions_schema,
    list_permission_sets,
    list_users_with_sets,
    unassign_permission_set,
    update_permission_set,
)
from .auth.settings import auth_enabled, auth_settings
from .auth.store import init_db as init_auth_db
from .client import IfsAcpClient
from .clone import CloneAnalysisCancelled, CloneAnalysisError, analyse
from .dependency.ai_retry import retry_failed_with_ai
from .dependency.gemini import GeminiError, gemini_configured, gemini_settings
from .repackage.importer import import_packages, load_manifest
from .repackage.service import RepackagingService
from .repackage.validator import validate_generated_package
from .dependency.export import export_clone_results_csv, export_clone_results_json, export_csv, export_json
from .dependency.failures import failure_analyzer
from .profiles import (
    DEFAULT_ENVIRONMENT,
    delete_connector,
    environment_names,
    get_connector,
    list_connectors,
    settings_for,
    test_connector_payload,
    upsert_connector,
)
from .deliveries import associate_jira_release, delete_delivery, get_delivery, list_deliveries, upsert_delivery
from .jira import JiraClient, JiraSettings, JiraApiError
from .run_sessions import ImportRun, sessions
from .db_sync import (
    OracleEndpoint,
    compare_tables,
    get_status as db_sync_status,
    list_schemas as db_list_schemas,
    list_tables as db_list_tables,
    request_stop as db_sync_stop,
    start_sync as db_sync_start,
    test_connection as db_test_connection,
)
from .workspaces import (
    WORKSPACE_TOKEN,
    delete_workspace_file,
    list_workspace_files,
    resolve_folder_for_user,
    save_uploads,
)

LOG = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "web_static"

class ImportRequest(BaseModel):
    environment: str = DEFAULT_ENVIRONMENT
    folder: str | None = None
    dry_run: bool = False


class CloneRequest(BaseModel):
    environment: str = DEFAULT_ENVIRONMENT
    folder: str | None = None
    order: list[str] = []


class RepackBuildRequest(BaseModel):
    source_folder: str
    output_folder: str
    max_items: int = 10


class RepackImportRequest(BaseModel):
    environment: str = DEFAULT_ENVIRONMENT
    output_folder: str
    category: str | None = None


class DeliveryRequest(BaseModel):
    name: str
    date: str
    environment: str = "UAT"
    deliveryType: str = "Delivery"
    description: str = ""
    items: list[str] = []


class ConnectorRequest(BaseModel):
    name: str
    connectorType: str = "IFS"
    base_url: str
    token_url: str
    client_id: str
    client_secret: str = ""
    grant_type: str = "password"
    token_client_auth: str = "basic"
    scope: str = ""
    username: str = ""
    password: str = ""
    xsrf_token: str = ""
    acp_folder: str = r"C:\UpdaClones"
    verify_tls: bool = True
    use_env_proxy: bool = False
    rename_from: str | None = None


class ConnectorTestRequest(ConnectorRequest):
    existing_name: str | None = None


class AuthEmailRequest(BaseModel):
    email: str


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class AuthSetPasswordRequest(BaseModel):
    token: str
    purpose: str = "verify"
    password: str
    confirm: str


class PermissionSetRequest(BaseModel):
    name: str
    description: str = ""
    isActive: bool = True
    grants: list[dict[str, str]] = []


class PermissionSetUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    isActive: bool | None = None
    grants: list[dict[str, str]] | None = None


class AssignPermissionSetRequest(BaseModel):
    permissionSetId: int


class OracleEndpointRequest(BaseModel):
    host: str
    port: int = 1521
    service: str
    user: str
    password: str = ""
    connectAs: str = "service"


class DbSyncEndpointBody(BaseModel):
    endpoint: OracleEndpointRequest


class DbSyncSchemaBody(BaseModel):
    endpoint: OracleEndpointRequest
    owner_schema: str | None = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}


class DbSyncCompareBody(BaseModel):
    source: OracleEndpointRequest
    target: OracleEndpointRequest
    owner_schema: str = Field(alias="schema")
    tables: list[str] = []

    model_config = {"populate_by_name": True}


class DbSyncStartBody(BaseModel):
    source: OracleEndpointRequest
    target: OracleEndpointRequest
    owner_schema: str = Field(alias="schema")
    tables: list[str]
    addMissingColumns: bool = True
    replaceData: bool = False

    model_config = {"populate_by_name": True}


run_lock = sessions.lock
app = FastAPI(title="IFS ACP Importer", docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
init_auth_db()
init_permissions_schema()

PUBLIC_PATHS = {
    "/splash",
    "/login",
    "/register",
    "/forgot-password",
    "/set-password",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/forgot-password",
    "/api/auth/set-password",
    "/api/auth/token",
    "/api/auth/status",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not auth_enabled():
            return await call_next(request)
        path = request.url.path
        if path.startswith("/assets") or path in PUBLIC_PATHS:
            return await call_next(request)
        user = current_user(request)
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Sign in required"}, status_code=401)
            if path in {"/splash", "/login", "/register", "/forgot-password", "/set-password"}:
                return await call_next(request)
            next_path = path if path.startswith("/") else "/"
            return RedirectResponse(f"/splash?next={next_path}", status_code=302)

        request.state.user = user
        perms = permissions_for_request(request)

        if path.startswith("/api/"):
            denied = check_api_permission(request.method, path, perms)
            if denied:
                return JSONResponse({"detail": denied}, status_code=403)
            return await call_next(request)

        # HTML page authorization (direct URL access)
        if path == "/forbidden":
            return await call_next(request)
        if page_access_denied(path, perms):
            alt = first_allowed_path(perms)
            if alt and alt != path:
                return RedirectResponse(alt, status_code=302)
            return RedirectResponse("/forbidden", status_code=302)
        return await call_next(request)


app.add_middleware(AuthMiddleware)




def package_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in {".acp", ".zip"})


def _user_ctx(request: Request) -> tuple[str | None, bool]:
    """Return (email, is_super_admin). When auth is disabled, treat as unrestricted."""
    if not auth_enabled():
        return None, True
    user = getattr(request.state, "user", None) or current_user(request)
    if not user:
        return None, False
    perms = permissions_for_request(request) or {}
    return user["email"], bool(perms.get("isSuperAdmin"))


def _resolve_folder(request: Request, folder: str | None) -> str:
    email, is_super = _user_ctx(request)
    try:
        return str(resolve_folder_for_user(email, folder, is_super_admin=is_super))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/workspace")

def _owner(request: Request) -> str | None:
    email, _ = _user_ctx(request)
    return email


def _busy(email: str | None) -> bool:
    return sessions.user_busy(email)


def get_workspace(request: Request) -> dict[str, Any]:
    email, _ = _user_ctx(request)
    if not email:
        path = resolve_folder_for_user(None, None, is_super_admin=True)
        path.mkdir(parents=True, exist_ok=True)
        files = [{"name": p.name, "bytes": p.stat().st_size} for p in package_files(path)]
        return {
            "path": str(path),
            "repackageOutput": str(path / "repackaged"),
            "files": files,
            "fileCount": len(files),
            "token": WORKSPACE_TOKEN,
        }
    return {**list_workspace_files(email), "token": WORKSPACE_TOKEN}


@app.post("/api/workspace/upload")
async def upload_workspace_files(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    email, _ = _user_ctx(request)
    key = email or "local"
    uploads: list[tuple[str, bytes]] = []
    for item in files:
        data = await item.read()
        uploads.append((item.filename or "", data))
    if not uploads:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    result = save_uploads(key, uploads)
    result["token"] = WORKSPACE_TOKEN
    result["message"] = (
        f"Uploaded {len(result.get('saved') or [])} file(s) to your server workspace."
        if result.get("saved")
        else "No valid .acp/.zip files were uploaded."
    )
    if result.get("skipped"):
        result["message"] += f" Skipped: {', '.join(result['skipped'])}."
    return result


@app.delete("/api/workspace/files/{filename}")
def remove_workspace_file(filename: str, request: Request) -> dict[str, Any]:
    email, _ = _user_ctx(request)
    try:
        result = delete_workspace_file(email or "local", filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["token"] = WORKSPACE_TOKEN
    return result


def run_imports(request: ImportRequest, owner: str | None) -> None:
    run = sessions.import_run(owner)
    try:
        settings = settings_for(request.environment, request.folder, require_auth=not request.dry_run)
        files = package_files(settings.acp_folder)
        if not files:
            with run_lock:
                run.message = f"No .acp or .zip files found in {settings.acp_folder}"
            return

        client = IfsAcpClient(settings)
        if not request.dry_run:
            with run_lock:
                run.message = f"Authenticating with IAM for {request.environment}…"
            client.authenticate()

        for index, package in enumerate(files, start=1):
            with run_lock:
                if run.cancel_requested:
                    run.message = f"Stopped by user after {len(run.results)} of {len(files)} packages"
                    break
                run.message = f"Processing {index} of {len(files)}: {package.name}"
            try:
                success, message = client.import_acp(package, dry_run=request.dry_run)
            except Exception as exc:
                LOG.exception("Import failed for %s", package.name)
                success, message = False, str(exc)
            with run_lock:
                run.results.append({"name": package.name, "success": success, "message": message})

        with run_lock:
            if not run.cancel_requested:
                successful = sum(item["success"] for item in run.results)
                run.message = f"Finished: {successful} successful, {len(run.results) - successful} failed"
    except Exception as exc:
        LOG.exception("Dashboard import run failed")
        with run_lock:
            run.message = f"Could not start import: {exc}"
    finally:
        with run_lock:
            run.running = False
            run.cancel_requested = False


def run_clone(request: CloneRequest, owner: str | None) -> None:
    clone_run = sessions.clone_run(owner)
    try:
        settings = settings_for(request.environment, request.folder, require_auth=True)
        with run_lock:
            cached = sessions.clone_analysis(owner)
        folder = settings.acp_folder
        if cached and Path(str(cached.get("folder", ""))).resolve() == folder.resolve():
            analysis = cached
        else:
            analysis = analyse(folder)
        plan_files = [pkg["file"] for level in analysis.get("deploymentPlan", []) for pkg in level.get("packages", [])]
        if not plan_files or not analysis.get("canStart"):
            raise ValueError(analysis.get("notes") or "ACP Deploy cannot start until circular dependency chains are resolved.")
        requested = [name for name in request.order if name in plan_files]
        ordered_files = requested if set(requested) == set(plan_files) else plan_files
        rows = {row["file"]: row for row in analysis["packages"]}
        ordered = [rows[name] for name in ordered_files if name in rows]
        client = IfsAcpClient(settings)
        with run_lock:
            clone_run.message = f"Authenticating with IAM for {request.environment}…"
            clone_run.phase = "importing"
        client.authenticate()
        failed: set[str] = set()
        for index, row in enumerate(ordered, start=1):
            with run_lock:
                if clone_run.cancel_requested:
                    clone_run.message = f"Stopped by user after {len(clone_run.results)} of {len(ordered)} packages"
                    break
                clone_run.message = f"Cloning {index} of {len(ordered)}: {row['file']}"
                clone_run.completed = index - 1
                clone_run.total = len(ordered)
            blocked = [dependency["name"] for dependency in row["dependencies"] if dependency["name"] in failed]
            if blocked and not row.get("cyclic"):
                clone_run.results.append({"name": row["file"], "success": False, "skipped": True,
                                          "message": f"Skipped: dependency failed ({', '.join(blocked)})"})
                failed.add(row["name"])
                continue
            try:
                success, message = client.import_acp(settings.acp_folder / row["file"])
            except Exception as exc:
                LOG.exception("ACP Clone failed for %s", row["file"])
                success, message = False, str(exc)
            clone_run.results.append({"name": row["file"], "success": success, "message": message})
            with run_lock:
                clone_run.completed = index
            if not success:
                failed.add(row["name"])
                failure_analyzer.record(acp=row["name"], file=row["file"], message=message)
        with run_lock:
            if not clone_run.cancel_requested:
                successful = sum(item["success"] for item in clone_run.results)
                clone_run.message = f"Finished: {successful} successful, {len(clone_run.results) - successful} not imported"
    except Exception as exc:
        LOG.exception("ACP Clone run failed")
        with run_lock:
            clone_run.message = f"Could not start ACP Deploy: {exc}"
    finally:
        with run_lock:
            clone_run.running = False
            clone_run.cancel_requested = False
            clone_run.phase = "idle"
            sessions.save_clone(owner)


def run_ai_retry(request: CloneRequest, owner: str | None) -> None:
    clone_run = sessions.clone_run(owner)
    try:
        settings = settings_for(request.environment, request.folder, require_auth=True)
        with run_lock:
            analysis = sessions.clone_analysis(owner)
            prior = list(clone_run.results)
        failed = [row for row in prior if not row.get("success") and not row.get("ai")]
        if not failed:
            with run_lock:
                clone_run.message = "No failed Direct Clone packages to retry with AI."
            return
        already_ok = {str(row.get("name") or "") for row in prior if row.get("success")}
        client = IfsAcpClient(settings)
        with run_lock:
            clone_run.message = f"Authenticating with IAM for {request.environment}…"
            clone_run.phase = "ai_retry"
        client.authenticate()

        def progress(completed: int, total: int, filename: str) -> bool:
            with run_lock:
                if clone_run.cancel_requested:
                    return False
                clone_run.completed = completed
                clone_run.total = total
                clone_run.message = f"AI retry {completed} of {total}: {filename}"
                return True

        def on_result(row: dict[str, Any]) -> None:
            with run_lock:
                clone_run.results.append(row)

        retry_failed_with_ai(
            client=client,
            folder=settings.acp_folder,
            analysis=analysis,
            failed=failed,
            already_ok=already_ok,
            progress=progress,
            on_result=on_result,
        )
        with run_lock:
            if not clone_run.cancel_requested:
                ai_rows = [row for row in clone_run.results if row.get("ai")]
                successful = sum(1 for row in ai_rows if row.get("success"))
                clone_run.message = f"AI retry finished: {successful} successful, {len(ai_rows) - successful} not imported"
    except GeminiError as exc:
        with run_lock:
            clone_run.message = str(exc)
    except Exception as exc:
        LOG.exception("AI retry failed")
        with run_lock:
            clone_run.message = f"Could not start AI retry: {exc}"
    finally:
        with run_lock:
            clone_run.running = False
            clone_run.cancel_requested = False
            clone_run.phase = "idle"
            sessions.save_clone(owner)


def run_repack_build(request: RepackBuildRequest, owner: str | None) -> None:
    repack_run = sessions.repack_run(owner)
    try:
        def progress(completed: int, total: int, filename: str) -> bool:
            with run_lock:
                if repack_run.cancel_requested:
                    return False
                repack_run.completed = completed
                repack_run.total = total
                repack_run.message = f"Scanning {completed} of {total}: {filename}"
                return True
        result = RepackagingService().analyse_and_build(
            Path(request.source_folder),
            Path(request.output_folder),
            max_items=max(1, int(request.max_items)),
            progress=progress,
        )
        with run_lock:
            sessions.set_repack_report(owner, result)
            repack_run.message = (
                f"Build complete: {result['summary']['generatedPackages']} packages, "
                f"{result['summary']['itemsDiscovered']} items"
            )
    except InterruptedError:
        with run_lock:
            repack_run.message = "Repackage analysis stopped by the user."
    except Exception as exc:
        LOG.exception("Repackage build failed")
        with run_lock:
            repack_run.message = f"Repackage build failed: {exc}"
    finally:
        with run_lock:
            repack_run.running = False
            repack_run.cancel_requested = False
            repack_run.phase = "idle"


def run_repack_import(request: RepackImportRequest, owner: str | None) -> None:
    repack_run = sessions.repack_run(owner)
    try:
        settings = settings_for(request.environment, None, require_auth=True)
        client = IfsAcpClient(settings)
        with run_lock:
            repack_run.message = f"Authenticating with IAM for {request.environment}…"
            repack_run.phase = "importing"
        try:
            client.authenticate()
        except Exception as exc:
            with run_lock:
                repack_run.message = (
                    f"Authentication with {request.environment} failed: {exc}"
                )
            return
        def progress(completed: int, total: int, filename: str) -> bool:
            with run_lock:
                if repack_run.cancel_requested:
                    return False
                repack_run.completed = completed
                repack_run.total = total
                repack_run.message = f"Importing {completed} of {total}: {filename}"
                return True
        def on_result(package: dict[str, Any]) -> None:
            with run_lock:
                status = package.get("importStatus")
                message = package.get("importMessage") or ""
                repack_run.results.append({
                    "name": package.get("package"),
                    "success": status == "SUCCESS",
                    "skipped": status in {"BLOCKED", "SKIPPED"},
                    "message": f"{status}: {message}",
                    "sourceAcps": package.get("sourceAcps"),
                    "items": package.get("items"),
                })
        manifest = import_packages(
            client=client,
            output_folder=Path(request.output_folder),
            category=request.category,
            progress=progress,
            on_result=on_result,
        )
        with run_lock:
            sessions.set_repack_report(owner, manifest)
            successful = sum(1 for item in repack_run.results if item.get("success"))
            repack_run.message = f"Repackage import finished: {successful} successful, {len(repack_run.results) - successful} not imported"
    except Exception as exc:
        LOG.exception("Repackage import failed")
        with run_lock:
            repack_run.message = f"Could not start repackage import: {exc}"
    finally:
        with run_lock:
            repack_run.running = False
            repack_run.cancel_requested = False
            repack_run.phase = "idle"


def run_clone_analysis(environment: str, folder: str | None, owner: str | None) -> None:
    clone_run = sessions.clone_run(owner)
    try:
        settings = settings_for(environment, folder, require_auth=False)
        def progress(completed: int, total: int, filename: str) -> bool:
            with run_lock:
                if clone_run.cancel_requested:
                    return False
                clone_run.completed = completed
                clone_run.total = total
                clone_run.message = f"Analysing {completed} of {total}: {filename}"
                return True
        result = analyse(settings.acp_folder, progress)
        with run_lock:
            sessions.set_clone_analysis(owner, result)
            clone_run.completed = clone_run.total
            clone_run.message = f"Dependency analysis complete: {len(result['packages'])} valid packages, {len(result['invalid'])} invalid archives"
    except CloneAnalysisCancelled:
        with run_lock:
            sessions.set_clone_analysis(owner, None)
            clone_run.message = "Dependency analysis stopped by user. No deployment order was created."
    except Exception as exc:
        LOG.exception("ACP Clone analysis failed")
        with run_lock:
            sessions.set_clone_analysis(owner, None)
            clone_run.message = f"Dependency analysis failed: {exc}"
    finally:
        with run_lock:
            clone_run.running = False
            clone_run.phase = "idle"
            sessions.save_clone(owner)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/clone", include_in_schema=False)
def clone_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "clone.html")


@app.get("/releases", include_in_schema=False)
def releases_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "releases.html")


@app.get("/releases/{release_id}", include_in_schema=False)
def release_details_page(release_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "release_details.html")


@app.get("/calendar", include_in_schema=False)
def calendar_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "calendar.html")


@app.get("/connectors", include_in_schema=False)
def connectors_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "connectors.html")


@app.get("/db-sync", include_in_schema=False)
def db_sync_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "db_sync.html")


@app.get("/splash", include_in_schema=False)
def splash_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "splash.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/register", include_in_schema=False)
def register_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "register.html")


@app.get("/forgot-password", include_in_schema=False)
def forgot_password_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "forgot_password.html")


@app.get("/set-password", include_in_schema=False)
def set_password_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "set_password.html")


@app.get("/forbidden", include_in_schema=False)
def forbidden_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "forbidden.html")


@app.get("/admin/permission-sets", include_in_schema=False)
def admin_permission_sets_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin_permission_sets.html")


@app.get("/admin/users", include_in_schema=False)
def admin_users_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin_users.html")


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    settings = auth_settings()
    user = current_user(request)
    payload: dict[str, Any] = {
        "enabled": settings.enabled,
        "allowSignup": settings.allow_signup,
        "authenticated": bool(user),
        "email": user["email"] if user else None,
        "smtpConfigured": bool(settings.smtp_host),
        "isSuperAdmin": False,
        "permissions": {"pages": {}, "grants": []},
        "catalog": catalog_as_dict(),
    }
    if user:
        from .auth.permissions_store import ensure_default_permissions

        ensure_default_permissions(user["email"])
        perms = effective_grants_for_email(user["email"])
        payload["isSuperAdmin"] = bool(perms.get("isSuperAdmin"))
        payload["permissions"] = {
            "pages": perms.get("pages") or {},
            "grants": perms.get("grants") or [],
        }
    return payload


@app.get("/api/admin/catalog")
def admin_catalog() -> dict[str, Any]:
    return {"pages": catalog_as_dict()}


@app.get("/api/admin/permission-sets")
def admin_list_permission_sets() -> dict[str, Any]:
    return {"permissionSets": list_permission_sets()}


@app.get("/api/admin/permission-sets/{set_id}")
def admin_get_permission_set(set_id: int) -> dict[str, Any]:
    data = get_permission_set(set_id)
    if not data:
        raise HTTPException(status_code=404, detail="Permission set not found")
    return data


@app.post("/api/admin/permission-sets")
def admin_create_permission_set(body: PermissionSetRequest, request: Request) -> dict[str, Any]:
    actor = getattr(request.state, "user", None) or current_user(request)
    try:
        return create_permission_set(
            name=body.name,
            description=body.description,
            is_active=body.isActive,
            grants=body.grants,
            actor_email=actor["email"] if actor else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/permission-sets/{set_id}")
def admin_update_permission_set(set_id: int, body: PermissionSetUpdateRequest, request: Request) -> dict[str, Any]:
    actor = getattr(request.state, "user", None) or current_user(request)
    try:
        return update_permission_set(
            set_id,
            name=body.name,
            description=body.description,
            is_active=body.isActive,
            grants=body.grants,
            actor_email=actor["email"] if actor else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/admin/permission-sets/{set_id}")
def admin_delete_permission_set(set_id: int, request: Request) -> dict[str, Any]:
    actor = getattr(request.state, "user", None) or current_user(request)
    try:
        delete_permission_set(set_id, actor_email=actor["email"] if actor else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/admin/users")
def admin_list_users() -> dict[str, Any]:
    return {"users": list_users_with_sets()}


@app.get("/api/admin/users/{email}")
def admin_get_user(email: str) -> dict[str, Any]:
    detail = get_user_permission_detail(email)
    if not detail:
        raise HTTPException(status_code=404, detail="User not found")
    effective = effective_grants_for_email(email)
    detail["effectivePermissions"] = effective
    return detail


@app.post("/api/admin/users/{email}/permission-sets")
def admin_assign_set(email: str, body: AssignPermissionSetRequest, request: Request) -> dict[str, Any]:
    actor = getattr(request.state, "user", None) or current_user(request)
    try:
        detail = assign_permission_set(
            email,
            body.permissionSetId,
            actor_email=actor["email"] if actor else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    detail["effectivePermissions"] = effective_grants_for_email(email)
    return detail


@app.delete("/api/admin/users/{email}/permission-sets/{set_id}")
def admin_unassign_set(email: str, set_id: int, request: Request) -> dict[str, Any]:
    actor = getattr(request.state, "user", None) or current_user(request)
    try:
        detail = unassign_permission_set(
            email,
            set_id,
            actor_email=actor["email"] if actor else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    detail["effectivePermissions"] = effective_grants_for_email(email)
    return detail


def _request_public_base(request: Request) -> str:
    """Prefer configured APP_BASE_URL; otherwise use the browser-facing host."""
    configured = (auth_settings().base_url or "").strip().rstrip("/")
    if configured and "127.0.0.1" not in configured and "localhost" not in configured.lower():
        return configured
    forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = forwarded or (request.headers.get("host") or "").strip()
    if not host:
        return configured or "http://127.0.0.1:8766"
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


@app.post("/api/auth/register")
def auth_register(body: AuthEmailRequest, request: Request) -> dict[str, Any]:
    try:
        return auth_service.register(body.email, public_base_url=_request_public_base(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not send verification email: {exc}") from exc


@app.post("/api/auth/forgot-password")
def auth_forgot(body: AuthEmailRequest, request: Request) -> dict[str, Any]:
    try:
        return auth_service.request_password_reset(
            body.email, public_base_url=_request_public_base(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not send reset email: {exc}") from exc


@app.get("/api/auth/token")
def auth_token_info(token: str = Query(...), purpose: str = Query("verify")) -> dict[str, Any]:
    try:
        return auth_service.peek_token(token, purpose)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/set-password")
def auth_set_password(body: AuthSetPasswordRequest) -> dict[str, Any]:
    try:
        return auth_service.set_password_with_token(
            token=body.token,
            purpose=body.purpose,
            password=body.password,
            confirm=body.confirm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/login")
def auth_login(body: AuthLoginRequest) -> Response:
    try:
        result = auth_service.login(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse({"ok": True, "email": result["email"], "message": "Signed in"})
    set_session_cookie(response, result["email"])
    return response


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    response = JSONResponse({"ok": True, "message": "Signed out"})
    clear_session_cookie(response)
    return response


@app.get("/api/connectors")
def get_connectors() -> dict[str, Any]:
    try:
        return {"connectors": list_connectors(), "environments": environment_names(), "default": DEFAULT_ENVIRONMENT}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/connectors/{name}")
def get_connector_details(name: str) -> dict[str, Any]:
    try:
        return get_connector(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/connectors/{name}")
def put_connector(name: str, request: ConnectorRequest) -> dict[str, Any]:
    try:
        payload = request.model_dump()
        rename_from = payload.pop("rename_from", None)
        connector = upsert_connector(request.name or name, payload, rename_from=rename_from)
        return {"connector": connector, "environments": environment_names()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/connectors/{name}")
def remove_connector(name: str) -> dict[str, Any]:
    try:
        delete_connector(name)
        return {"ok": True, "environments": environment_names()}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/connectors/test")
def test_connector(request: ConnectorTestRequest) -> dict[str, Any]:
    try:
        payload = request.model_dump()
        existing_name = payload.pop("existing_name", None) or payload.pop("rename_from", None)
        settings = test_connector_payload(payload, existing_name=existing_name)
        client = IfsAcpClient(settings)
        client.authenticate()
        return {
            "ok": True,
            "message": f"Connected successfully to {settings.base_url}",
            "environments": environment_names(),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Connection failed: {exc}") from exc


@app.get("/api/releases")
def get_releases() -> dict[str, Any]:
    try:
        settings = JiraSettings.from_environment()
        releases = JiraClient(settings).releases()
    except (ValueError, JiraApiError, requests.RequestException) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"project": settings.project_key, "releases": releases}


@app.get("/api/releases/{release_id}/issues")
def get_release_issues(release_id: str) -> dict[str, Any]:
    try:
        settings = JiraSettings.from_environment()
        issues = JiraClient(settings).release_issues(release_id)
    except (ValueError, JiraApiError, requests.RequestException) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"project": settings.project_key, "releaseId": release_id, "issues": issues}


@app.get("/api/releases/{release_id}/details")
def get_release_details(release_id: str) -> dict[str, Any]:
    try:
        settings = JiraSettings.from_environment()
        details = JiraClient(settings).release_details(release_id)
    except (ValueError, JiraApiError, requests.RequestException) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"project": settings.project_key, **details}

@app.get("/api/deliveries")
def get_deliveries() -> dict[str, Any]:
    return {"deliveries": list_deliveries()}


@app.get("/api/deliveries/preview-release")
def preview_delivery_release(date: str = Query(...)) -> dict[str, Any]:
    try:
        delivery_date = date_cls.fromisoformat(date[:10])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Delivery date must be YYYY-MM-DD.") from exc
    jira_release, note = associate_jira_release(delivery_date)
    return {"date": delivery_date.isoformat(), "jiraRelease": jira_release, "jiraMatchNote": note}


@app.get("/api/deliveries/{delivery_id}")
def get_delivery_item(delivery_id: str) -> dict[str, Any]:
    item = get_delivery(delivery_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Delivery not found.")
    return item


@app.post("/api/deliveries")
def create_delivery(request: DeliveryRequest) -> dict[str, Any]:
    try:
        return upsert_delivery(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/deliveries/{delivery_id}")
def update_delivery(delivery_id: str, request: DeliveryRequest) -> dict[str, Any]:
    try:
        return upsert_delivery(request.model_dump(), delivery_id=delivery_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Delivery not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/deliveries/{delivery_id}")
def remove_delivery(delivery_id: str) -> dict[str, str]:
    if not delete_delivery(delivery_id):
        raise HTTPException(status_code=404, detail="Delivery not found.")
    return {"message": "Delivery removed"}


@app.get("/api/environments")
def get_environments() -> dict[str, Any]:
    return {"default": DEFAULT_ENVIRONMENT, "environments": environment_names()}


@app.get("/api/packages")
def get_packages(
    http_request: Request,
    environment: str = Query(DEFAULT_ENVIRONMENT),
    folder: str | None = Query(None),
) -> dict[str, Any]:
    resolved = _resolve_folder(http_request, folder)
    settings = settings_for(environment, resolved, require_auth=False)
    files = package_files(settings.acp_folder)
    return {
        "folder": str(settings.acp_folder),
        "workspaceToken": WORKSPACE_TOKEN,
        "packages": [{"name": path.name, "bytes": path.stat().st_size} for path in files],
    }


@app.get("/api/status")
def get_status(http_request: Request) -> dict[str, Any]:
    with run_lock:
        return asdict(sessions.import_run(_owner(http_request)))


@app.get("/api/clone/status")
def get_clone_status(http_request: Request) -> dict[str, Any]:
    with run_lock:
        return asdict(sessions.clone_run(_owner(http_request)))


@app.post("/api/clone/analyse", status_code=202)
def analyse_clone(
    http_request: Request,
    environment: str = Query(DEFAULT_ENVIRONMENT),
    folder: str | None = Query(None),
) -> dict[str, str]:
    owner = _owner(http_request)
    resolved = _resolve_folder(http_request, folder)
    with run_lock:
        if _busy(owner):
            raise HTTPException(status_code=409, detail="Another import or analysis process is already running.")
        sessions.set_clone_analysis(owner, None)
        clone_run = sessions.clone_run(owner)
        clone_run.running = True
        clone_run.phase = "analysing"
        clone_run.completed = 0
        clone_run.total = 0
        clone_run.folder = resolved
        clone_run.message = "Preparing ACP dependency analysis…"
    threading.Thread(target=run_clone_analysis, args=(environment, resolved, owner), name="acp-clone-analysis", daemon=True).start()
    return {"message": "Dependency analysis started"}


@app.get("/api/clone/analysis")
def get_clone_analysis(http_request: Request) -> dict[str, Any]:
    with run_lock:
        analysis = sessions.clone_analysis(_owner(http_request))
        if analysis is None:
            raise HTTPException(status_code=404, detail="No completed dependency analysis is available yet.")
        return analysis


@app.get("/api/clone/analysis/export")
def export_clone_analysis(http_request: Request, format: str = Query("json")) -> Response:
    with run_lock:
        report = sessions.clone_analysis(_owner(http_request))
        if report is None:
            raise HTTPException(status_code=404, detail="No completed dependency analysis is available yet.")
    if format.lower() == "csv":
        return Response(
            content=export_csv(report),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=acp-dependency-analysis.csv"},
        )
    return Response(
        content=export_json(report),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=acp-dependency-analysis.json"},
    )


@app.get("/api/clone/results/export")
def export_clone_results(http_request: Request, format: str = Query("json")) -> Response:
    with run_lock:
        clone_run = sessions.clone_run(_owner(http_request))
        payload = {
            "environment": clone_run.environment,
            "folder": clone_run.folder,
            "message": clone_run.message,
            "results": list(clone_run.results),
        }
    if not payload["results"]:
        raise HTTPException(status_code=404, detail="No clone results are available to export yet.")
    if format.lower() == "csv":
        return Response(
            content=export_clone_results_csv(payload),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=acp-clone-results.csv"},
        )
    return Response(
        content=export_clone_results_json(payload),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=acp-clone-results.json"},
    )


@app.get("/api/repackage/status")
def get_repackage_status(http_request: Request) -> dict[str, Any]:
    with run_lock:
        return asdict(sessions.repack_run(_owner(http_request)))


@app.get("/api/repackage/report")
def get_repackage_report(http_request: Request, output_folder: str | None = Query(None)) -> dict[str, Any]:
    with run_lock:
        cached = sessions.repack_report(_owner(http_request))
    if output_folder:
        requested = Path(output_folder)
        try:
            return load_manifest(requested)
        except FileNotFoundError as exc:
            cached_folder = Path(str((cached or {}).get("outputFolder") or ""))
            try:
                same = cached is not None and cached_folder.resolve() == requested.resolve()
            except OSError:
                same = False
            if same:
                return cached
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if cached is not None:
        return cached
    raise HTTPException(status_code=404, detail="No repackage report is available yet.")


@app.post("/api/repackage/build", status_code=202)
def start_repackage_build(body: RepackBuildRequest, http_request: Request) -> dict[str, str]:
    owner = _owner(http_request)
    if body.max_items < 1:
        raise HTTPException(status_code=400, detail="Maximum items per package must be at least 1.")
    source = _resolve_folder(http_request, body.source_folder)
    if not body.output_folder or body.output_folder.strip() in {"", WORKSPACE_TOKEN, "@workspace"}:
        output_path = Path(source) / "repackaged"
        output_path.mkdir(parents=True, exist_ok=True)
        output = str(output_path)
    else:
        output = _resolve_folder(http_request, body.output_folder)
    body = RepackBuildRequest(source_folder=source, output_folder=output, max_items=body.max_items)
    with run_lock:
        if _busy(owner):
            raise HTTPException(status_code=409, detail="Another import or analysis process is already running.")
        sessions.set_repack_report(owner, None)
        repack_run = sessions.repack_run(owner)
        repack_run.running = True
        repack_run.cancel_requested = False
        repack_run.phase = "building"
        repack_run.completed = 0
        repack_run.total = 0
        repack_run.results = []
        repack_run.folder = body.output_folder
        repack_run.message = "Scanning source ACP packages…"
    threading.Thread(target=run_repack_build, args=(body, owner), name="acp-repack-build", daemon=True).start()
    return {"message": "Repackage build started"}


@app.post("/api/repackage/validate")
def validate_repackage(http_request: Request, output_folder: str = Query(...)) -> dict[str, Any]:
    try:
        manifest = load_manifest(Path(output_folder))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    results = []
    folder = Path(output_folder)
    for package in manifest.get("packages") or []:
        validation = validate_generated_package(folder / package["path"])
        package["validation"] = validation
        package["status"] = "READY" if validation["ok"] else "INVALID"
        results.append({"package": package["package"], **validation})
    (folder / "deployment-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with run_lock:
        sessions.set_repack_report(_owner(http_request), manifest)
    return {"ok": all(item["ok"] for item in results), "results": results, "manifest": manifest}


@app.post("/api/repackage/imports", status_code=202)
def start_repackage_import(body: RepackImportRequest, http_request: Request) -> dict[str, str]:
    owner = _owner(http_request)
    try:
        load_manifest(Path(body.output_folder))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    with run_lock:
        if _busy(owner):
            raise HTTPException(status_code=409, detail="Another import process is already running.")
        repack_run = sessions.repack_run(owner)
        repack_run.running = True
        repack_run.cancel_requested = False
        repack_run.environment = body.environment
        repack_run.folder = body.output_folder
        repack_run.results = []
        repack_run.message = "Starting generated ACP import…"
        repack_run.phase = "starting"
        repack_run.completed = 0
        repack_run.total = 0
    threading.Thread(target=run_repack_import, args=(body, owner), name="acp-repack-import", daemon=True).start()
    return {"message": "Repackage import started"}


@app.post("/api/clone/imports", status_code=202)
def start_clone(body: CloneRequest, http_request: Request) -> dict[str, str]:
    owner = _owner(http_request)
    body = CloneRequest(
        environment=body.environment,
        folder=_resolve_folder(http_request, body.folder),
        order=body.order,
    )
    with run_lock:
        if _busy(owner):
            raise HTTPException(status_code=409, detail="Another import process is already running.")
        clone_run = sessions.clone_run(owner)
        clone_run.running = True
        clone_run.cancel_requested = False
        clone_run.environment = body.environment
        clone_run.folder = body.folder or r"C:\UpdaClones"
        clone_run.results = []
        clone_run.message = "Starting ACP Deploy…"
        clone_run.phase = "starting"
        clone_run.completed = 0
        clone_run.total = len(body.order)
    threading.Thread(target=run_clone, args=(body, owner), name="acp-clone", daemon=True).start()
    return {"message": "ACP Deploy started"}


@app.get("/api/clone/ai/status")
def get_ai_status() -> dict[str, Any]:
    settings = gemini_settings()
    return {
        "configured": bool(settings["api_key"]),
        "model": settings["model"] if settings["api_key"] else None,
    }


@app.post("/api/clone/ai-retry", status_code=202)
def start_ai_retry(body: CloneRequest, http_request: Request) -> dict[str, str]:
    owner = _owner(http_request)
    body = CloneRequest(
        environment=body.environment,
        folder=_resolve_folder(http_request, body.folder),
        order=body.order,
    )
    if not gemini_configured():
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY is not configured. Add it to .env and restart the dashboard.")
    with run_lock:
        if _busy(owner):
            raise HTTPException(status_code=409, detail="Another import or analysis process is already running.")
        clone_run = sessions.clone_run(owner)
        failed = [row for row in clone_run.results if not row.get("success") and not row.get("ai")]
        if not failed:
            raise HTTPException(status_code=400, detail="No failed Direct Clone packages are available to retry.")
        clone_run.running = True
        clone_run.cancel_requested = False
        clone_run.environment = body.environment
        clone_run.folder = body.folder or clone_run.folder
        clone_run.message = "Starting AI retry of failed ACPs…"
        clone_run.phase = "ai_retry"
        clone_run.completed = 0
        clone_run.total = len(failed)
    threading.Thread(target=run_ai_retry, args=(body, owner), name="acp-clone-ai-retry", daemon=True).start()
    return {"message": "AI retry started"}


@app.post("/api/imports", status_code=202)
def start_import(body: ImportRequest, http_request: Request) -> dict[str, str]:
    owner = _owner(http_request)
    body = ImportRequest(
        environment=body.environment,
        folder=_resolve_folder(http_request, body.folder),
        dry_run=body.dry_run,
    )
    try:
        settings = settings_for(body.environment, body.folder, require_auth=not body.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with run_lock:
        if _busy(owner):
            raise HTTPException(status_code=409, detail="Another import process is already running.")
        run = sessions.import_run(owner)
        run.running = True
        run.cancel_requested = False
        run.dry_run = body.dry_run
        run.environment = body.environment
        run.folder = str(settings.acp_folder)
        run.message = "Starting dry run…" if body.dry_run else "Starting import…"
        run.results = []
    threading.Thread(target=run_imports, args=(body, owner), name="acp-importer", daemon=True).start()
    return {"message": "Dry run started" if body.dry_run else "Import started"}


@app.post("/api/imports/stop", status_code=202)
def stop_import(http_request: Request) -> dict[str, str]:
    owner = _owner(http_request)
    with run_lock:
        run = sessions.import_run(owner)
        clone_run = sessions.clone_run(owner)
        repack_run = sessions.repack_run(owner)
        target = run if run.running else clone_run if clone_run.running else repack_run if repack_run.running else None
        if target is None:
            raise HTTPException(status_code=409, detail="No import is currently running.")
        target.cancel_requested = True
        target.message = "Stop requested. The current IFS operation will finish, then no further packages will start."
    return {"message": "Stop requested"}

def _oracle_endpoint(body: OracleEndpointRequest) -> OracleEndpoint:
    return OracleEndpoint(
        host=body.host,
        port=body.port,
        service=body.service,
        user=body.user,
        password=body.password,
        connect_as=body.connectAs or "service",
    )


@app.get("/api/db-sync/status")
def api_db_sync_status(http_request: Request) -> dict[str, Any]:
    return db_sync_status(_owner(http_request))


@app.post("/api/db-sync/test")
def api_db_sync_test(body: DbSyncEndpointBody) -> dict[str, Any]:
    try:
        return db_test_connection(_oracle_endpoint(body.endpoint))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Connection failed: {exc}") from exc


@app.post("/api/db-sync/schemas")
def api_db_sync_schemas(body: DbSyncEndpointBody) -> dict[str, Any]:
    try:
        return {"schemas": db_list_schemas(_oracle_endpoint(body.endpoint))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/db-sync/tables")
def api_db_sync_tables(body: DbSyncSchemaBody) -> dict[str, Any]:
    if not body.owner_schema:
        raise HTTPException(status_code=400, detail="Schema is required")
    try:
        return {"tables": db_list_tables(_oracle_endpoint(body.endpoint), body.owner_schema)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/db-sync/compare")
def api_db_sync_compare(body: DbSyncCompareBody) -> dict[str, Any]:
    try:
        return compare_tables(
            _oracle_endpoint(body.source),
            _oracle_endpoint(body.target),
            body.owner_schema,
            body.tables or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/db-sync/start", status_code=202)
def api_db_sync_start(body: DbSyncStartBody, http_request: Request) -> dict[str, str]:
    from .auth.permissions import assert_function

    if not body.tables:
        raise HTTPException(status_code=400, detail="Select at least one table")
    if body.addMissingColumns:
        assert_function(http_request, "db_sync", "alter_schema")
    try:
        db_sync_start(
            _owner(http_request),
            source=_oracle_endpoint(body.source),
            target=_oracle_endpoint(body.target),
            schema=body.owner_schema,
            tables=body.tables,
            add_missing_columns=body.addMissingColumns,
            replace_data=body.replaceData,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "DB sync started"}


@app.post("/api/db-sync/stop", status_code=202)
def api_db_sync_stop(http_request: Request) -> dict[str, str]:
    try:
        db_sync_stop(_owner(http_request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Stop requested"}
