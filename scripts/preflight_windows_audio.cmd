@echo off
setlocal

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%preflight_windows_audio.ps1

if not exist "%PS1%" (
  echo ERROR: Script not found: %PS1%
  exit /b 1
)

powershell -ExecutionPolicy Bypass -File "%PS1%"
if errorlevel 2 (
  echo Preflight completed with blockers. Check JSON report path above.
  exit /b 2
)
if errorlevel 1 (
  echo Preflight execution failed.
  exit /b 1
)

echo Preflight completed without blockers.
endlocal
