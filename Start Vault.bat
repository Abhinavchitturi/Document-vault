@echo off
rem Double-click to start the vault (and open it in the browser).
cd /d "%~dp0"
call vault.cmd start
echo.
echo You can close this window; the vault keeps running.
pause
