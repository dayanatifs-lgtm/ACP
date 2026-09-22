@echo off
REM Starts the ACP Importer dashboard for network access.
REM Default port is 8088 if 8080 is blocked on the server.
REM Override: set ACP_PORT=8090 && deploy\start_server.bat
REM Open: http://dse1thorftp1:%ACP_PORT%/
setlocal
cd /d "%~dp0.."

if "%ACP_PORT%"=="" set "ACP_PORT=8088"
if "%APP_BASE_URL%"=="" set "APP_BASE_URL=http://dse1thorftp1:%ACP_PORT%"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Python is missing or failed to create .venv. Install Python 3.10+ and retry.
    pause
    exit /b 1
  )
  set "PY=.venv\Scripts\python.exe"
)

"%PY%" -c "import uvicorn, multipart, oracledb" 1>nul 2>nul
if errorlevel 1 (
  echo Installing / updating requirements into .venv ...
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Could not install requirements. Check network / proxy and retry.
    echo If SSL fails, try:
    echo   "%PY%" -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
    pause
    exit /b 1
  )
)

echo.
echo If bind fails with WinError 10013, port %ACP_PORT% is reserved or in use.
echo Check with:
echo   netstat -ano ^| findstr :%ACP_PORT%
echo   netsh interface ipv4 show excludedportrange protocol=tcp
echo Then set another port, e.g.  set ACP_PORT=8766
echo.
echo Starting ACP Importer on 0.0.0.0:%ACP_PORT% ...
echo Browse: http://dse1thorftp1:%ACP_PORT%/
"%PY%" -m uvicorn acp_importer.web:app --host 0.0.0.0 --port %ACP_PORT%
if errorlevel 1 pause
endlocal
