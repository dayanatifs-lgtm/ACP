# IFS ACP Importer

Imports every `.acp` file in `C:\UpdaClones` (or a configured folder) through the IFS `AppConfigPackageHandling` OData projection. It processes packages serially, reports each result, and continues after a package failure.

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
4. Place ACP files under `C:\UpdaClones`.

Run a safe folder scan first:

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
