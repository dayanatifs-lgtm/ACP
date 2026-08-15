"""Authenticated client for the IFS AppConfigPackageHandling projection."""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
        self.service_url = f"{settings.base_url}/{self.SERVICE}"

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

        response = self.session.post(
            self.settings.token_url,
            data=data,
            auth=auth,
            timeout=self.settings.request_timeout_seconds,
            verify=self.settings.verify_tls,
        )
        self._raise_for_status(response, "IAM token request")
        token = response.json().get("access_token")
        if not token:
            raise IfsApiError("IAM token response did not contain access_token")
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json;odata.metadata=full;IEEE754Compatible=true",
            }
        )
        if self.settings.xsrf_token:
            self.session.headers["X-XSRF-TOKEN"] = self.settings.xsrf_token
        LOG.info("Authenticated with IAM")

    def import_acp(self, path: Path, *, dry_run: bool = False) -> tuple[bool, str]:
        """Run the HAR-derived import sequence and return (success, summary)."""
        if dry_run:
            LOG.info("DRY RUN: would import %s", path.name)
            return True, "DRY RUN"

        objkey: str | None = None
        try:
            objkey, etag = self._create_virtual_package()
            self._upload_file(objkey, etag, path)
            self._post_action("UnzipAcpFile", {"Objkey": objkey})
            self._post_action("FetchItemSetInfo", {"Objkey": objkey})
            state = self._wait_for_state(objkey)
            errors = self._get_error_count(objkey, state.get("@odata.etag") or etag)
            self._post_entity_action(objkey, "AppConfigPackageVirtual_ImportFinish", state.get("@odata.etag") or etag)
            final_state = self._wait_for_state(objkey)
            log_text = self._get_log_file(objkey)
            summary = str(final_state.get("Summary") or final_state.get("ImportStatus") or "Import completed")
            if errors:
                summary = f"{summary}; error items: {errors}"
            successful = not errors and not self._is_failure(final_state)
            return successful, summary + self._log_hint(log_text)
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
        headers = {"Prefer": "wait=99999"}
        headers.update(kwargs.pop("headers", {}))
        response = self.session.request(
            method,
            f"{self.service_url}/{path}",
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
            verify=self.settings.verify_tls,
            **kwargs,
        )
        self._raise_for_status(response, f"{method} {path}")
        return response

    @staticmethod
    def _entity_path(objkey: str) -> str:
        return f"AppConfigPackageVirtualSet(Objkey='{quote(objkey, safe='')}')"

    @staticmethod
    def _is_failure(entity: dict[str, Any]) -> bool:
        text = " ".join(str(entity.get(key, "")) for key in ("ImportStatus", "Summary")).lower()
        return any(word in text for word in ("fail", "error", "invalid"))

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

    @staticmethod
    def _log_hint(log_text: str) -> str:
        one_line = " ".join(log_text.split())
        return f"; log: {one_line[:240]}" if one_line else ""

    @staticmethod
    def _raise_for_status(response: requests.Response, operation: str) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            text = response.text[:1000].replace("\n", " ")
            raise IfsApiError(f"{operation} failed ({response.status_code}): {text}") from exc
