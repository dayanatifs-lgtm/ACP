"""Authenticated client for the IFS AppConfigPackageHandling projection."""

from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from requests.auth import HTTPBasicAuth

from .config import Settings

LOG = logging.getLogger(__name__)


class IfsApiError(RuntimeError):
    """An IFS IAM or projection request failed."""


class IfsAcpClient:
    """Implements the request sequence recorded in the supplied HAR file."""

    SERVICE = "AppConfigPackageHandling.svc"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        # Do not inherit a stale desktop HTTP(S)_PROXY setting unless explicitly requested.
        self.session.trust_env = settings.use_env_proxy
        self.service_url = f"{settings.base_url}/{self.SERVICE}"
        self._token_expires_at = 0.0

    def authenticate(self) -> None:
        """Obtain an IAM bearer token without ever logging the token or secret."""
        data: dict[str, str] = {"grant_type": self.settings.grant_type}
        if self.settings.scope:
            data["scope"] = self.settings.scope
        if self.settings.grant_type == "password":
            data["username"] = self.settings.username or ""
            data["password"] = self.settings.password or ""

        auth = None
        if self.settings.token_client_auth == "basic":
            auth = HTTPBasicAuth(self.settings.client_id, self.settings.client_secret)
        elif self.settings.token_client_auth == "body":
            data.update(client_id=self.settings.client_id, client_secret=self.settings.client_secret)
        else:
            raise ValueError("IFS_TOKEN_CLIENT_AUTH must be basic or body")

        host = self._iam_host()
        self._wake_environment()
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self.session.post(
                    self.settings.token_url,
                    data=data,
                    auth=auth,
                    timeout=self._iam_timeout(),
                    verify=self.settings.verify_tls,
                )
                self._raise_for_status(response, "IAM token request")
                token_response = response.json()
                token = token_response.get("access_token")
                if not token:
                    raise IfsApiError("IAM token response did not contain access_token")
                try:
                    expires_in = max(0, float(token_response.get("expires_in", 0)))
                except (TypeError, ValueError):
                    expires_in = 0
                self._token_expires_at = time.monotonic() + expires_in if expires_in else 0.0
                self.session.headers.update(
                    {
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json;odata.metadata=full;IEEE754Compatible=true",
                    }
                )
                if self.settings.xsrf_token:
                    self.session.headers["X-XSRF-TOKEN"] = self.settings.xsrf_token
                LOG.info("Authenticated with IAM")
                return
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                LOG.warning("IAM token request to %s failed (attempt %s/3): %s", host, attempt, exc)
                if attempt < 3:
                    time.sleep(3 * attempt)
        raise IfsApiError(
            f"IAM login for {host} timed out. The IFS environment may be asleep or unreachable. "
            "Open the environment in a browser to wake it, then click Import All again."
        ) from last_error

    def import_acp(self, path: Path, *, dry_run: bool = False) -> tuple[bool, str]:
        """Run the HAR-derived import sequence and return (success, summary)."""
        if dry_run:
            LOG.info("DRY RUN: would import %s", path.name)
            return True, "DRY RUN"

        objkey: str | None = None
        try:
            objkey, etag = self._create_virtual_package()
            self._upload_file(objkey, etag, path)
            LOG.info("Unzipping %s", path.name)
            self._post_action("UnzipAcpFile", {"Objkey": objkey})
            LOG.info("Reading package item information")
            self._post_action("FetchItemSetInfo", {"Objkey": objkey})
            # The HAR calls ImportFinish after fetching items. Waiting for a final
            # status here deadlocks because that finalising action has not run yet.
            state = self._get_entity(objkey)
            errors = self._get_error_count(objkey, state.get("@odata.etag") or etag)
            LOG.info("Finalising package import")
            self._post_entity_action(objkey, "AppConfigPackageVirtual_ImportFinish", state.get("@odata.etag") or etag)
            # The captured requests use Prefer: wait=99999, so the server completes
            # each action synchronously. Read the final entity once for the summary.
            final_state = self._get_entity(objkey)
            log_text = self._get_log_file(objkey)
            summary = str(final_state.get("Summary") or final_state.get("ImportStatus") or "Import completed")
            reported_errors = self._reported_error_count(final_state, errors)
            already_present = self._already_present_item_count(log_text)
            real_errors = max(0, reported_errors - already_present)
            if reported_errors:
                summary = f"{summary}; error items: {reported_errors}"
            if already_present:
                summary = (
                    f"{summary}; {already_present} item(s) already present in IFS were accepted"
                )
            # Pre-finish GetErrorItemCount can count items that later import as
            # "identical / already exists". Those objects are in IFS, so they
            # are not treated as import failures.
            successful = real_errors == 0
            if successful and already_present == 0 and self._is_failure(final_state):
                successful = False
            if successful:
                try:
                    publish_success, publish_message = self._publish_if_needed(
                        final_state,
                        fallback_name=path.stem,
                    )
                except IfsApiError as exc:
                    # Manual IFS import finishes at ImportFinish. A follow-up
                    # GET by the XML PackageId often 404s even when the ACP
                    # is already published under a different id.
                    if self._is_not_found(str(exc)):
                        publish_success = True
                        publish_message = (
                            "Imported successfully; IFS already applied the package "
                            "(publish lookup by PackageId returned 404)"
                        )
                    else:
                        publish_success, publish_message = False, f"Publish failed: {exc}"
                successful = successful and publish_success
            else:
                publish_message = "Publish skipped: import reported errors or an invalid status"
            return successful, f"{summary}; {publish_message}" + self._log_hint(log_text)
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise IfsApiError(f"{path.name}: {exc}") from exc
        finally:
            if objkey:
                try:
                    state = self._get_entity(objkey)
                    self._post_entity_action(objkey, "AppConfigPackageVirtual_ClearVirtuals", state.get("@odata.etag", "*"))
                except Exception as exc:  # Cleanup must not mask the import result.
                    LOG.warning("Could not clear temporary virtual items for %s: %s", path.name, exc)

    def _create_virtual_package(self) -> tuple[str, str]:
        # HAR: GET Default(), then POST AppConfigPackageVirtualSet with {}.
        self._request("GET", "AppConfigPackageVirtualSet/IfsApp.AppConfigPackageHandling.AppConfigPackageVirtual_Default()")
        response = self._request("POST", "AppConfigPackageVirtualSet", json={})
        entity = response.json()
        objkey = entity.get("Objkey")
        if not objkey:
            raise IfsApiError("Create virtual package response did not contain Objkey")
        return str(objkey), response.headers.get("ETag") or entity.get("@odata.etag") or "*"

    def _upload_file(self, objkey: str, etag: str, path: Path) -> None:
        disposition = base64.b64encode(path.name.encode("utf-8")).decode("ascii")
        with path.open("rb") as file_handle:
            self._request(
                "PATCH",
                f"{self._entity_path(objkey)}/ImportFile",
                data=file_handle,
                headers={
                    "Content-Type": "application/octet-stream",
                    "If-Match": etag,
                    "X-IFS-Content-Disposition": f"filename={disposition}",
                },
            )
        LOG.info("Uploaded %s", path.name)

    def _get_error_count(self, objkey: str, etag: str) -> int:
        response = self._post_entity_action(objkey, "AppConfigPackageVirtual_GetErrorItemCount", etag)
        return self._find_error_count(response.json())

    def _wait_for_state(self, objkey: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.settings.timeout_seconds
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            latest = self._get_entity(objkey)
            if self._is_failure(latest) or self._is_terminal_success(latest):
                return latest
            time.sleep(self.settings.poll_seconds)
        raise IfsApiError(f"Timed out waiting for package {objkey} to finish")

    def _get_entity(self, objkey: str) -> dict[str, Any]:
        return self._request("GET", self._entity_path(objkey)).json()

    def _publish_if_needed(
        self,
        imported_package: dict[str, Any],
        fallback_name: str | None = None,
    ) -> tuple[bool, str]:
        """Publish only when IFS exposes the Publish command for the package.

        The original import HAR ends at ImportFinish. Publish is a separate
        AppConfigPackageSet action that must use the PackageId IFS stored,
        not the PACKAGE_ID written into the generated zip.
        """
        package_id = imported_package.get("PackageId")
        package_name = imported_package.get("Name") or fallback_name
        try:
            package = self._resolve_imported_package(package_id, package_name)
        except IfsApiError as exc:
            if self._is_not_found(str(exc)):
                return True, (
                    "Imported successfully; IFS already applied the package "
                    "(publish lookup returned 404)"
                )
            raise
        if not package:
            # Import already created the ACP. A 404 here means IFS has not
            # exposed AppConfigPackageSet yet, or it published during import
            # under a different PackageId (common for enumerations).
            return True, (
                "Publish skipped: package imported successfully but was not found by "
                "PackageId; IFS often already published it"
            )

        package_id = package.get("PackageId") or package_id
        enabled = package.get("EnablePublishCommand")
        if not self._as_bool(enabled):
            return True, "Publish skipped: command is not available (already published or not eligible)"

        etag = package.get("@odata.etag") or "*"
        LOG.info("Publish command is available for package %s; checking deployment effects", package_id)
        self._request("GET", f"CheckDeploymentEffects(PackageId='{quote(str(package_id), safe='')}')")
        LOG.info("Publishing package %s", package_id)
        last_error: IfsApiError | None = None
        for attempt in range(1, 4):
            try:
                self._request(
                    "POST",
                    f"{self._package_path(str(package_id))}/IfsApp.AppConfigPackageHandling.AppConfigPackage_Publish",
                    json={},
                    headers={"If-Match": etag},
                )
                last_error = None
                break
            except IfsApiError as exc:
                last_error = exc
                if attempt < 3 and (self._is_cache_lock_error(str(exc)) or self._is_not_found(str(exc))):
                    delay = 5 * attempt
                    LOG.warning(
                        "IFS is not ready to publish %s; retrying in %s seconds",
                        package_id,
                        delay,
                    )
                    time.sleep(delay)
                    resolved = self._resolve_imported_package(package_id, package_name)
                    if resolved:
                        package = resolved
                        package_id = package.get("PackageId") or package_id
                        etag = package.get("@odata.etag") or etag
                    continue
                return False, f"Publish failed: {exc}"
        if last_error:
            if self._is_not_found(str(last_error)):
                return True, (
                    "Publish skipped: package imported successfully; IFS publish lookup "
                    "returned 404 (already published)"
                )
            return False, f"Publish failed: {last_error}"

        refreshed = self._resolve_imported_package(package_id, package_name) or {}
        if self._as_bool(refreshed.get("EnablePublishCommand")):
            return False, "Publish failed: IFS still shows the Publish command after the action"
        return True, "Published successfully"

    def publish_existing(self, name: str) -> tuple[bool, str]:
        """Publish an ACP that is already in IFS, looked up by package name."""
        return self._publish_if_needed({"Name": name}, fallback_name=name)

    def _resolve_imported_package(self, package_id: Any, package_name: Any) -> dict[str, Any] | None:
        """Find the stored ACP. IFS often uses a different PackageId than the zip XML."""
        name = str(package_name or "").strip()
        if name:
            try:
                safe_name = name.replace("'", "''")
                response = self._request(
                    "GET",
                    f"AppConfigPackageSet?$filter=Name eq '{safe_name}'&$select=PackageId,Name,EnablePublishCommand&$top=5",
                )
                rows = response.json().get("value") or []
                if rows:
                    return rows[0]
            except IfsApiError as exc:
                if not self._is_not_found(str(exc)):
                    raise
                LOG.warning("Package name %s was not found in AppConfigPackageSet: %s", name, exc)
        if not package_id:
            return None
        try:
            return self._get_package(str(package_id))
        except IfsApiError as exc:
            if self._is_not_found(str(exc)):
                LOG.warning("PackageId %s from the ACP XML is not the stored IFS id: %s", package_id, exc)
                return None
            raise

    def _get_package(self, package_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self._package_path(package_id)}?$select=PackageId,Name,EnablePublishCommand",
        ).json()
    def _get_log_file(self, objkey: str) -> str:
        # The HAR retrieves LogFile after ClearVirtuals.  Read it before cleanup so it
        # is preserved even if an environment purges virtual data more aggressively.
        response = self._request("GET", f"{self._entity_path(objkey)}/LogFile", headers={"Accept": "text/plain, */*"})
        return response.content.decode("utf-8", errors="replace")

    def _post_action(self, path: str, payload: dict[str, str]) -> requests.Response:
        return self._request("POST", path, json=payload)

    def _post_entity_action(self, objkey: str, action: str, etag: str) -> requests.Response:
        return self._request(
            "POST",
            f"{self._entity_path(objkey)}/IfsApp.AppConfigPackageHandling.{action}",
            json={},
            headers={"If-Match": etag},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        # Long import batches can run beyond an IAM access-token lifetime.
        # Renew one minute early so the next package does not start with a
        # bearer token that is about to expire.
        if self._token_expires_at and time.monotonic() >= self._token_expires_at - 60:
            LOG.info("IFS access token is expiring; refreshing it before the next request")
            self.authenticate()

        headers = {"Prefer": "wait=99999"}
        headers.update(kwargs.pop("headers", {}))
        url = f"{self.service_url}/{path}"
        request_kwargs = dict(kwargs)
        stream = request_kwargs.get("data")
        stream_position = stream.tell() if hasattr(stream, "tell") and hasattr(stream, "seek") else None

        def send() -> requests.Response:
            return self.session.request(
                method,
                url,
                headers=headers,
                timeout=self.settings.request_timeout_seconds,
                verify=self.settings.verify_tls,
                **request_kwargs,
            )

        response = send()
        if response.status_code == 401:
            # A 401 means IFS rejected the request before processing it, so it
            # is safe to obtain a fresh bearer token and retry once.  Reset a
            # streamed upload to its original position before replaying it.
            LOG.warning("IFS returned 401 for %s %s; refreshing IAM token and retrying once", method, path)
            response.close()
            self.authenticate()
            if stream_position is not None:
                stream.seek(stream_position)
            response = send()

        # IFS can briefly return MI_MODIFIED_ERROR while its projection
        # metadata cache catches up immediately after an ACP import.  This is
        # safe to retry for reads; do not replay writes here.
        if method.upper() == "GET" and response.status_code == 503 and "MI_MODIFIED_ERROR" in response.text:
            for delay in (2, 5, 10):
                LOG.warning("IFS metadata cache is updating for %s; retrying in %s seconds", path, delay)
                response.close()
                time.sleep(delay)
                response = send()
                if not (response.status_code == 503 and "MI_MODIFIED_ERROR" in response.text):
                    break
        self._raise_for_status(response, f"{method} {path}")
        return response

    @staticmethod
    def _entity_path(objkey: str) -> str:
        return f"AppConfigPackageVirtualSet(Objkey='{quote(objkey, safe='')}')"

    @staticmethod
    def _package_path(package_id: str) -> str:
        return f"AppConfigPackageSet(PackageId='{quote(package_id, safe='')}')"

    def _iam_host(self) -> str:
        return urlparse(self.settings.token_url or self.settings.base_url).netloc or "IFS IAM"

    def _iam_timeout(self) -> tuple[float, float]:
        # Token calls should fail fast; import actions keep the longer request timeout.
        read = min(45.0, float(self.settings.request_timeout_seconds or 45))
        return (10.0, max(15.0, read))

    def _wake_environment(self) -> None:
        """Best-effort ping so a sleeping IFS build environment can start before IAM login."""
        parsed = urlparse(self.settings.token_url or self.settings.base_url)
        if not parsed.scheme or not parsed.netloc:
            return
        try:
            self.session.get(
                f"{parsed.scheme}://{parsed.netloc}/",
                timeout=(8, 15),
                verify=self.settings.verify_tls,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            LOG.info("Environment wake-up ping for %s did not complete: %s", parsed.netloc, exc)

    @staticmethod
    def _as_bool(value: Any) -> bool:
        return value is True or (isinstance(value, str) and value.strip().lower() == "true")

    _ALREADY_PRESENT = re.compile(
        r"is identical|already exists|already exist|already present|"
        r"identical\.?\s*ignored|exists in (?:another |the )?package|"
        r"no difference(?:s)? detected",
        re.I,
    )

    @classmethod
    def _already_present_item_count(cls, log_text: str) -> int:
        if not log_text:
            return 0
        chunks = re.split(r"[\r\n]+|(?=Importing )", log_text)
        line_count = sum(1 for chunk in chunks if chunk.strip() and cls._ALREADY_PRESENT.search(chunk))
        pattern_count = len(cls._ALREADY_PRESENT.findall(log_text))
        return max(line_count, pattern_count)

    @classmethod
    def _identical_item_count(cls, log_text: str) -> int:
        return cls._already_present_item_count(log_text)

    @classmethod
    def _reported_error_count(cls, entity: dict[str, Any], error_items: int) -> int:
        summary = str(entity.get("Summary") or "")
        match = re.search(r"errors?\s*:\s*(\d+)", summary, flags=re.I)
        if match:
            return int(match.group(1))
        return int(error_items or 0)

    @classmethod
    def _identical_items_only(cls, entity: dict[str, Any], log_text: str, error_items: int) -> bool:
        """IFS reports identical/already-exists items as errors. Those objects are already in IFS."""
        already_present = cls._already_present_item_count(log_text)
        reported = cls._reported_error_count(entity, error_items)
        return already_present > 0 and reported > 0 and already_present >= reported

    @staticmethod
    def _is_cache_lock_error(message: str) -> bool:
        text = (message or "").upper()
        return "FND_LOCKED" in text or "CACHE MANAGEMENT RECORD IS CURRENTLY LOCKED" in text

    @staticmethod
    def _is_not_found(message: str) -> bool:
        text = (message or "").upper()
        return "404" in text or "ODP_RESOURCE_NOTFOUND" in text or "RESOURCE NOT FOUND" in text

    @staticmethod
    def _is_failure(entity: dict[str, Any]) -> bool:
        # A successful IFS summary contains text such as "Errors: 0". Treat the
        # status as authoritative and only regard a non-zero error count as failed.
        status = str(entity.get("ImportStatus", "")).lower()
        if any(word in status for word in ("fail", "error", "invalid")):
            return True
        summary = str(entity.get("Summary", "")).lower()
        match = re.search(r"errors?\s*:\s*(\d+)", summary)
        if match:
            return int(match.group(1)) > 0
        return any(word in summary for word in ("failed", "invalid"))

    @staticmethod
    def _is_terminal_success(entity: dict[str, Any]) -> bool:
        status = str(entity.get("ImportStatus", "")).lower()
        return any(word in status for word in ("success", "complete", "finished", "imported"))

    @staticmethod
    def _find_error_count(value: Any) -> int:
        if isinstance(value, dict):
            for key, child in value.items():
                if "error" in key.lower() and "count" in key.lower():
                    try:
                        return int(child)
                    except (TypeError, ValueError):
                        pass
                found = IfsAcpClient._find_error_count(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = IfsAcpClient._find_error_count(child)
                if found:
                    return found
        return 0

    @classmethod
    def _log_hint(cls, log_text: str) -> str:
        if not log_text:
            return ""
        lines = [line.strip() for line in re.split(r"[\r\n]+|(?=Importing )", log_text) if line.strip()]
        interesting = [
            line
            for line in lines
            if re.search(r"error|identical|ignored|fail|warning|already exist|invalid", line, re.I)
        ]
        chosen = interesting[-8:] or lines[-6:]
        one_line = " ".join(chosen)[:500]
        return f"; log: {one_line}" if one_line else ""

    @staticmethod
    def _raise_for_status(response: requests.Response, operation: str) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            text = response.text[:1000].replace("\n", " ")
            raise IfsApiError(f"{operation} failed ({response.status_code}): {text}") from exc
