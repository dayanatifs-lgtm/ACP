# Deploy ACP Importer to Windows Server (dse1thorftp1:8080)

Target URL examples:

- **http://dse1thorftp1:8088/** (default in `start_server.bat`)
- **http://dse1thorftp1:8080/** only if that port is free on the server

## Port 8080 / WinError 10013

Windows often blocks bind on 8080 (`WinError 10013`) when the port is in use or in a reserved/excluded range (Hyper-V, IIS, etc.).

On the server:

```bat
netstat -ano | findstr :8080
netsh interface ipv4 show excludedportrange protocol=tcp
```

Use a free port instead:

```bat
set ACP_PORT=8088
deploy\start_server.bat
```

Then open **http://dse1thorftp1:8088/**. Also allow that port in Windows Firewall (`setup_server.ps1` or manually).


## 1. Copy the project to the server

On your PC, copy this whole folder to the server, for example:

`C:\Apps\acp-importer\`

Include:

- `acp_importer\`
- `requirements.txt`
- `deploy\`
- `.env` (create on the server; do not rely on OneDrive sync of secrets)
- `environments.local.json` if you use named IFS environments

Do **not** commit or share `.env` passwords/tokens.

## 2. Install Python on the server

1. RDP to `dse1thorftp1` (as you already do).
2. Install **Python 3.10+** and tick **Add python.exe to PATH**.
3. Open an **Administrator** PowerShell in `C:\Apps\acp-importer`.

## 3. Run setup

```powershell
cd C:\Apps\acp-importer
powershell -ExecutionPolicy Bypass -File deploy\setup_server.ps1
```

This creates `.venv`, installs packages, and opens firewall TCP **8080**.

## 4. Configure secrets on the server

Edit `C:\Apps\acp-importer\.env` and `environments.local.json` with the IFS / Jira / Gemini values this machine should use.

For login emails to work, set at least:

```
APP_BASE_URL=http://dse1thorftp1:8088
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=<SendGrid API key>
SMTP_FROM=<verified sender address>
SMTP_USE_TLS=true
```

If SMTP is not set, registration still works: a one-time link is shown on the page (and saved under `logs\auth-links.log`). Without SMTP, users must open that link themselves.

ACP folders referenced in the UI (for example `C:\UpdaClones` or `C:\newcode\...`) must exist **on this server**, or change the paths in the UI to server-local folders.

### Per-user package uploads (recommended for multi-user)

Users should **upload** `.acp` / `.zip` files from their PCs. Files are stored under each user’s private folder on the server:

- Default root: `<project>\workspaces\<email>\`
- Override with `ACP_WORKSPACES_ROOT` in `.env` (for example `C:\AcpWorkspaces`)

Import and Clone use that workspace automatically. Do not expect the server to read `C:\...` paths from a user’s laptop.

## 5. Start the site

```powershell
deploy\start_server.bat
```

Then open:

- http://dse1thorftp1:8080/
- or http://10.1.65.94:8080/

Keep the console window open while testing. Close it to stop the site.

## 6. Optional: start at boot

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install_autostart.ps1
```

## Local vs server

| Mode | Command | URL |
|------|---------|-----|
| Your laptop | `start_dashboard.bat` | http://127.0.0.1:8766 |
| Server | `deploy\start_server.bat` | http://dse1thorftp1:8080/ |

## Checks if the page does not load

1. On the server: `netstat -ano | findstr :8080` — should show `LISTENING`.
2. Firewall rule **ACP Importer 8080** exists (setup script creates it).
3. From your PC you can ping / resolve `dse1thorftp1`.
4. Corporate network policy allows TCP 8080 to that host.
