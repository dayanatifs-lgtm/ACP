"""Independent Jira release retrieval client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


class JiraApiError(RuntimeError):
    """Jira release retrieval failed."""


@dataclass(frozen=True)
class JiraSettings:
    base_url: str
    project_key: str
    email: str
    api_token: str
    releases_url: str | None
    verify_tls: bool
    ca_bundle: str | None

    @classmethod
    def from_environment(cls) -> "JiraSettings":
        import os
        from dotenv import load_dotenv

        # Resolve the file beside the application rather than relying on the
        # directory from which Uvicorn happened to be started.
        project_env = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(dotenv_path=project_env)
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"Missing required Jira setting: {name}")
            return value
        ca_bundle = os.getenv("JIRA_CA_BUNDLE", "").strip() or None
        if ca_bundle and not Path(ca_bundle).is_file():
            raise ValueError(
                "JIRA_CA_BUNDLE does not point to a certificate file. "
                "Clear it when JIRA_VERIFY_TLS=false, or provide your company CA .pem path."
            )
        return cls(
            base_url=required("JIRA_BASE_URL").rstrip("/"),
            project_key=required("JIRA_PROJECT_KEY"),
            email=required("JIRA_EMAIL"),
            api_token=required("JIRA_API_TOKEN"),
            releases_url=os.getenv("JIRA_RELEASES_URL", "").strip() or None,
            verify_tls=os.getenv("JIRA_VERIFY_TLS", "true").strip().lower() in {"1", "true", "yes", "on"},
            ca_bundle=ca_bundle,
        )


class JiraClient:
    def __init__(self, settings: JiraSettings) -> None:
        self.settings = settings
        # Postman normally trusts the Windows certificate store.  Use the same
        # store for Jira when no explicit company CA file was supplied.
        if settings.verify_tls and not settings.ca_bundle:
            try:
                import truststore
                truststore.inject_into_ssl()
            except ImportError:
                # The dependency is listed in requirements.txt.  Retaining a
                # fallback here keeps the configuration error understandable
                # if someone runs the source before installing dependencies.
                pass
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = HTTPBasicAuth(settings.email, settings.api_token)
        self.session.headers.update({"Accept": "application/json"})

    def _get_json(self, url: str, *, params: dict[str, str] | None = None) -> Any:
        # A company-issued root certificate can be supplied through
        # JIRA_CA_BUNDLE when HTTPS traffic is inspected by a corporate proxy.
        verify: bool | str = self.settings.ca_bundle or self.settings.verify_tls
        response = self.session.get(url, params=params, timeout=30, verify=verify)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise JiraApiError(f"Jira releases request failed ({response.status_code}): {response.text[:500]}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type", "unknown")
            raise JiraApiError(
                f"Jira returned a non-JSON response (HTTP {response.status_code}, "
                f"Content-Type: {content_type}): {response.text[:300]}"
            ) from exc
        return payload

    def releases(self) -> list[dict[str, Any]]:
        url = self.settings.releases_url or f"{self.settings.base_url}/rest/api/3/project/{self.settings.project_key}/versions"
        payload = self._get_json(url)
        items = payload.get("values", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise JiraApiError("Unexpected Jira releases response structure")
        releases = [
            {
                "id": str(item.get("id", "")),
                "name": item.get("name", "Unnamed release"),
                "released": bool(item.get("released")),
                "archived": bool(item.get("archived")),
                "releaseDate": item.get("releaseDate") or item.get("userReleaseDate") or "—",
                "description": item.get("description") or "—",
            }
            for item in items
        ]
        # Jira does not guarantee a date order in this endpoint.  Keep the
        # most recently released packages at the top of the dashboard.
        def release_date(item: dict[str, Any]) -> date:
            try:
                return date.fromisoformat(item["releaseDate"])
            except (TypeError, ValueError):
                return date.min

        def release_order(item: dict[str, Any]) -> tuple[date, int]:
            try:
                identifier = int(item["id"])
            except (TypeError, ValueError):
                identifier = -1
            return release_date(item), identifier

        return sorted(releases, key=release_order, reverse=True)

    def release_issues(self, release_id: str) -> list[dict[str, str]]:
        """Return the Jira issues whose fixVersion is the selected release."""
        if not release_id.isdecimal():
            raise JiraApiError("Release ID must be numeric")

        url = f"{self.settings.base_url}/rest/api/3/search/jql"
        params = {
            "jql": f"project = {self.settings.project_key} AND fixVersion = {release_id}",
            "maxResults": "100",
            "fields": "summary,status,assignee,issuetype,fixVersions",
        }
        issues: list[dict[str, Any]] = []
        next_page_token: str | None = None
        seen_tokens: set[str] = set()

        while True:
            page_params = dict(params)
            if next_page_token:
                page_params["nextPageToken"] = next_page_token
            payload = self._get_json(url, params=page_params)
            if not isinstance(payload, dict) or not isinstance(payload.get("issues", []), list):
                raise JiraApiError("Unexpected Jira issue-search response structure")
            issues.extend(payload["issues"])

            next_page_token = payload.get("nextPageToken")
            if not next_page_token or next_page_token in seen_tokens:
                break
            seen_tokens.add(next_page_token)

        return [
            {
                "id": str(issue.get("id", "")),
                "key": issue.get("key", "Unknown issue"),
                "summary": issue.get("fields", {}).get("summary") or "—",
                "status": issue.get("fields", {}).get("status", {}).get("name") or "—",
                "assignee": issue.get("fields", {}).get("assignee", {}).get("displayName") or "Unassigned",
                "issueType": issue.get("fields", {}).get("issuetype", {}).get("name") or "—",
            }
            for issue in issues
        ]

    def release_details(self, release_id: str) -> dict[str, Any]:
        """Return release items with child development-task PR links."""
        release = next((item for item in self.releases() if item["id"] == release_id), None)
        if not release:
            raise JiraApiError(f"Release {release_id} was not found in project {self.settings.project_key}")

        items: list[dict[str, Any]] = []
        for issue in self.release_issues(release_id):
            issue_key = issue["key"]
            issue_data = self._get_json(
                f"{self.settings.base_url}/rest/api/3/issue/{issue_key}",
                params={"fields": "subtasks,summary,status,issuetype"},
            )
            children = issue_data.get("fields", {}).get("subtasks", []) if isinstance(issue_data, dict) else []
            development_tasks = [self._development_task(task) for task in children if task.get("key")]

            # Some releases contain Development Tasks directly rather than as
            # subtasks.  In that case inspect the release item itself.
            if not development_tasks:
                development_tasks = [self._development_task({"key": issue_key, "fields": issue_data.get("fields", {})})]

            items.append({
                **issue,
                "browseUrl": f"{self.settings.base_url}/browse/{issue_key}",
                "developmentTasks": development_tasks,
            })

        return {"release": release, "items": items}

    def _development_task(self, task: dict[str, Any]) -> dict[str, Any]:
        key = str(task["key"])
        fields = task.get("fields", {})
        remote_links = self._get_json(f"{self.settings.base_url}/rest/api/3/issue/{key}/remotelink")
        if not isinstance(remote_links, list):
            raise JiraApiError(f"Unexpected remote-link response for {key}")
        pull_requests = [
            {"label": link.get("title") or link.get("object", {}).get("title") or "Open PR", "url": url}
            for link in remote_links
            if (url := link.get("object", {}).get("url") or link.get("url")) and self._is_pull_request(link, url)
        ]
        return {
            "key": key,
            "summary": fields.get("summary") or "—",
            "issueType": fields.get("issuetype", {}).get("name") or "—",
            "pullRequests": pull_requests,
            "browseUrl": f"{self.settings.base_url}/browse/{key}",
        }

    @staticmethod
    def _is_pull_request(link: dict[str, Any], url: str) -> bool:
        text = " ".join(str(value or "") for value in (
            link.get("title"), link.get("object", {}).get("title"), link.get("object", {}).get("summary"), url,
        )).lower()
        return "pullrequest" in text or "/pull/" in text or text.startswith("pr-") or " pr-" in text
