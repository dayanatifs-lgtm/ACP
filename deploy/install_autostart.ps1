# Install ACP Importer as a Windows Scheduled Task that starts at boot.
# Run elevated:
#   powershell -ExecutionPolicy Bypass -File deploy\install_autostart.ps1

param(
    [string]$TaskName = "ACP Importer Dashboard"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Bat = Join-Path $Root "deploy\start_server.bat"

if (-not (Test-Path $Bat)) {
    throw "Missing $Bat"
}

$action = New-ScheduledTaskAction -Execute $Bat -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Scheduled task '$TaskName' installed and started."
Write-Host "Open http://dse1thorftp1:8080/"
