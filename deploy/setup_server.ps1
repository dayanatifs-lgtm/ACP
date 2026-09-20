# Setup ACP Importer on a Windows Server for http://dse1thorftp1:8080/
# Run from an elevated PowerShell in the project folder:
#   powershell -ExecutionPolicy Bypass -File deploy\setup_server.ps1

param(
    [string]$Port = "8088",
    [string]$ListenHost = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Project root: $Root"

function Assert-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python is not on PATH. Install Python 3.10+ from https://www.python.org/downloads/ and re-run."
    }
    & python --version
}

Assert-Python

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example. Edit .env with IFS/Jira/Gemini values before use."
    } else {
        Write-Warning "No .env found. Create one before running imports."
    }
}

$ruleName = "ACP Importer $Port"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existing) {
    try {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port | Out-Null
        Write-Host "Opened Windows Firewall TCP $Port."
    } catch {
        Write-Warning "Could not open firewall port $Port (run PowerShell as Administrator). Error: $($_.Exception.Message)"
    }
} else {
    Write-Host "Firewall rule already exists: $ruleName"
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "1. Edit .env and environments.local.json on this server."
Write-Host "2. Start the site:  deploy\start_server.bat"
Write-Host "3. Browse:          http://dse1thorftp1:$Port/"
Write-Host "   Tip: if port 8080 is forbidden (WinError 10013), use 8088/8766 instead:"
Write-Host "        set ACP_PORT=8766 && deploy\start_server.bat"
