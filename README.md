# IFS ACP Importer

Imports every `.acp` or `.zip` package in `C:\UpdaClones` (or a configured folder) through the IFS `AppConfigPackageHandling` OData projection. The HAR shows that the manual IFS import uploaded a `.zip` file. It processes packages serially, reports each result, and continues after a package failure.

## HAR-derived request flow

This implementation is based on `thor-cfg.ifs.cloud.har`, captured on 13 August 2026. It reproduces these projection calls, omitting the browser-only `odata-debug=json` parameter:

1. `GET AppConfigPackageVirtualSet/..._Default()` — initialise client defaults.
2. `POST AppConfigPackageVirtualSet` with `{}` — creates a virtual package entity; its `Objkey` and ETag identify the import.
3. `PATCH AppConfigPackageVirtualSet(Objkey='...')/ImportFile` — raw ACP bytes, `Content-Type: application/octet-stream`, `If-Match: <ETag>`, and `X-IFS-Content-Disposition: filename=<base64 filename>`.
4. `POST UnzipAcpFile` with `{"Objkey":"..."}`.
5. `POST FetchItemSetInfo` with `{"Objkey":"..."}`.
6. `GET AppConfigPackageVirtualSet(Objkey='...')` — read `ImportStatus`/`Summary`; the program polls this when the service reports a non-terminal state.
7. `POST .../AppConfigPackageVirtual_GetErrorItemCount` with `{}` and `If-Match` — checks item errors.
8. `POST .../AppConfigPackageVirtual_ImportFinish` with `{}` and `If-Match` — completes the import.
9. `GET .../LogFile` — retrieves the ACP import log. `POST .../AppConfigPackageVirtual_ClearVirtuals` clears temporary virtual items afterward.

The capture used an already-authenticated web session and contains no OAuth token request. The supplied IAM URL is therefore used as an OAuth/OpenID Connect token endpoint, but the exact grant must be confirmed with the IAM administrator. `client_credentials` with Basic client authentication is the default; the program also supports a password grant if the client is configured to allow it.

The browser capture contains an `X-XSRF-TOKEN`, but direct bearer-token projection calls often do not need it. Set `IFS_XSRF_TOKEN` only if your environment returns a CSRF-related error. Do not copy a browser session cookie into this program.

## Setup

1. Use Python 3.10+ and create a virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set the real values. Never commit `.env`.
4. Place ACP `.acp` or `.zip` files under `C:\UpdaClones`.

Run a safe folder scan first (this works without IAM settings):

```powershell
python -m acp_importer.main --dry-run
```

Then run the import:

```powershell
python -m acp_importer.main
```

Use another folder temporarily with `--folder C:\path\to\packages`. Add `--verbose` for HTTP-operation-level diagnostic logging (tokens and secrets are never logged).

## Required IAM confirmation

Before the first live run, confirm these points with the IFS/IAM administrator:

- Is the IAM client allowed to use `client_credentials`, or must it use a different grant/scope/audience?
- Does the IFS projection accept an IAM bearer token directly, or does it require a CSRF token in addition?
- What exact `ImportStatus` values indicate a successful completed import in this environment?
- Does the client have permissions for `AppConfigPackageHandling` and its import actions?

The HAR proves the projection request flow, file field name, action names, payloads, ETag use, and log endpoint. It does not prove those IAM grant and permission settings.

## Security

The credentials originally supplied in chat must be treated as exposed. Rotate the IAM client secret and user password, then put replacements only in your local `.env` or a managed secret store. The application never needs Postman-generated Objkeys: it creates a new `Objkey` for each ACP itself.

## Local web dashboard

After installing dependencies, double-click `start_dashboard.bat`. It starts a local-only server on `http://127.0.0.1:8766` and opens the dashboard in your browser. The dashboard lists `.acp`/`.zip` packages from `IFS_ACP_FOLDER`, runs a dry-run or import with buttons, and shows progress and results.

The browser UI never receives IAM credentials: it only calls the Python process running on the same computer. React is loaded from the public `unpkg.com` CDN, so the first page load needs ordinary internet access. The IFS import itself remains in Python.

## Multiple IFS environments and folders

The dashboard now lets the user choose a local ACP folder for each run. `C:\UpdaClones` stays the default, but you can type another existing local folder and press **Refresh** before importing.

To switch IAM credentials, copy `environments.example.json` to `environments.local.json`, add each environment's own base URL, token/login URL, client credentials, grant type, and optional default ACP folder. `environments.local.json` is ignored by Git and is never sent to the browser; the dashboard only displays its profile names. Restart the dashboard after changing this file, then choose the environment in the drop-down.

Keep the existing `.env` as the **Default (.env)** profile. Use a separate named profile for UAT, test, production, or any other IFS environment.

## Conditional ACP publishing

After a package imports successfully, the importer reads `AppConfigPackageSet(PackageId=...)` and checks `EnablePublishCommand`, exactly as recorded in `acpnew.har`. If it is `true`, it calls `CheckDeploymentEffects(PackageId=...)` and then posts to `AppConfigPackage_Publish` with the package ETag. If it is `false`, the result says that publishing was skipped because the command is hidden (the package is already published or is not eligible). The CLI and dashboard show this publish status in each import result.

### Proxy setting

The importer ignores inherited Windows `HTTP_PROXY`/`HTTPS_PROXY` values by default. If a particular IFS environment requires a corporate proxy, set `IFS_USE_ENV_PROXY=true` in `.env` or add `"use_env_proxy": true` to that environment profile.

## Jira Releases

The new `/releases` page is independent from ACP imports. The HAR shows the Jira releases UI is server-rendered: `GET /projects/THOR?selectedItem=...release-page` preloads the Relay `ProjectVersionsComponentQuery` using the authenticated Atlassian browser session. It does not contain a replayable release-list HTTP request or persisted-query payload, so the application uses a separately configured Jira API client and never reuses browser cookies.

Set `JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_EMAIL`, and `JIRA_API_TOKEN` in `.env`. By default the client requests `GET /rest/api/3/project/{JIRA_PROJECT_KEY}/versions` using HTTP Basic authentication with your Jira email and API token. Configure `JIRA_RELEASES_URL` to a documented environment-specific endpoint if needed. The response may be a list or a paginated object with `values`; the page displays release name, ID, released/unreleased state, archive state, date, and description.

## Delivery Calendar

Open **Delivery Calendar** in the dashboard (`http://127.0.0.1:8766/calendar`) to plan deliveries on a monthly or weekly grid.

- Click a date cell or **+ New** to add a delivery (name, date, environment, type, description, and delivery items).
- Each save looks up Jira versions through the same `/api/releases` client used on the Releases page.
- The latest Jira release whose `releaseDate` is on or before the delivery date is linked. If none exists, or Jira is unavailable, the calendar entry is still saved without a Jira link.
- Click a coloured block for delivery details. A linked release name opens the existing Jira release page (items and pull requests).

Entries are stored locally in `deliveries.json` next to the project (this file is gitignored).

## Dashboard login

Open `/login` to sign in with email and password.

1. **Create account** (`/register`) — enter email; a one-time verification link is sent.
2. Open the link → **set password** (min 8 characters).
3. Sign in at `/login`.
4. **Forgot password** sends a one-time reset link.

Configure in `.env`:

- `AUTH_ENABLED=true`
- `AUTH_SECRET=` long random string
- `APP_BASE_URL=` public URL of the dashboard (used in email links)
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` for real email

If SMTP is not configured, verification/reset links are written to `logs/auth-links.log` (and shown once in the UI as a debug link).

Users are stored in local `users.local.sqlite` (gitignored).

## Environment connectors

Open **Connectors** in the dashboard (`/connectors`) to add IFS environments from the UI instead of editing `environments.local.json` by hand.

1. Enter connector name, URLs, client auth, and credentials.
2. Click **Test Connection** to validate IAM login.
3. Click **Save connector** — the name appears in the **IFS environment** dropdown on ACP Importer and ACP Clone.

Secrets are stored only in the local `environments.local.json` file (gitignored) and are masked in the UI.

## Deploy to Windows Server (dse1thorftp1:8080)

See [deploy/README.md](deploy/README.md). Short version:

1. Copy the project to the server (for example `C:\Apps\acp-importer`).
2. Install Python 3.10+ on the server.
3. Run `deploy\setup_server.ps1` as Administrator.
4. Configure `.env` / `environments.local.json` on the server.
5. Start with `deploy\start_server.bat` and open http://dse1thorftp1:8080/

## ACP Clone dependency analysis

Open **ACP Clone** (`http://127.0.0.1:8766/clone`). Choose the IFS environment and ACP folder, then click **Analyse dependencies**. The analyzer scans `.acp`/`.zip` archives, builds an object provider index, and produces a deployment plan with topological levels.

- Confirmed edges only control import order (`consumer -> provider`, provider first).
- Ambiguous providers are listed and never auto-selected.
- Missing providers are listed and never invented.
- Circular chains are excluded from the plan instead of being silently broken.
- **Start ACP Clone** imports the plan sequentially using the existing IFS import client. Parallel import is not enabled.
- Export JSON or CSV from the analysis card. Parsed import failures are stored in `import_failures.json` for later AI-assisted retry (not connected yet).

Run the unit tests:

```powershell
python -m unittest tests.test_dependency_analyzer
```
