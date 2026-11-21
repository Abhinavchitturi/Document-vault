@echo off
rem Double-click to stop the vault.
cd /d "%~dp0"
call vault.cmd stop
echo.
pause
