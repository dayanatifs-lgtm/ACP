@echo off
setlocal
cd /d "%~dp0"
start "IFS ACP Importer" /B python -m uvicorn acp_importer.web:app --host 127.0.0.1 --port 8766
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8766
endlocal