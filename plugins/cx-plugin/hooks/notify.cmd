@echo off
setlocal

set "CX_NOTIFY_SCRIPT=%~dp0notify.py"

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  py -3 "%CX_NOTIFY_SCRIPT%"
  exit /b 0
)

for /f "delims=" %%P in ('where python3 2^>nul ^| findstr /I /V "WindowsApps"') do (
  "%%P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 (
    "%%P" "%CX_NOTIFY_SCRIPT%"
    exit /b 0
  )
)

for /f "delims=" %%P in ('where python 2^>nul ^| findstr /I /V "WindowsApps"') do (
  "%%P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 (
    "%%P" "%CX_NOTIFY_SCRIPT%"
    exit /b 0
  )
)

echo {}
exit /b 0
