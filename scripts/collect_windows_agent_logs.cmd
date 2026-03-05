@echo off
setlocal

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%collect_windows_agent_logs.ps1

if not exist "%PS1%" (
  echo ERROR: Script not found: %PS1%
  exit /b 1
)

powershell -ExecutionPolicy Bypass -File "%PS1%" -LastMinutes 60
if errorlevel 1 (
  echo Failed to collect diagnostics.
  exit /b 1
)

echo Diagnostics collected successfully.
endlocal
