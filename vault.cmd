@echo off
rem The vault's control command on Windows: vault start | stop | status | logs | update | autostart on|off
setlocal
cd /d "%~dp0"
rem Single-file database inside data\ (no server needed). To use PostgreSQL instead, set VAULT_DATABASE_URL first.
if not defined VAULT_DATABASE_URL set "VAULT_DATABASE_URL=sqlite:///data/index/vault.db"
set "PYTHONUTF8=1"
set "PY=python"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -m backend.deploy.vault %*
exit /b %errorlevel%
