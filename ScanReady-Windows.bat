@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "ROOT=%~dp0"
pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
  echo ERROR: ScanReady could not enter its application folder.
  exit /b 2
)

set "MODE=launch"
set "OFFLINE_ONLY=false"
set "DRY_RUN=false"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="test" set "MODE=test"& shift& goto parse_args
if /I "%~1"=="evaluate" set "MODE=evaluate"& shift& goto parse_args
if /I "%~1"=="--check" set "MODE=check"& shift& goto parse_args
if /I "%~1"=="--offline-only" set "OFFLINE_ONLY=true"& shift& goto parse_args
if /I "%~1"=="--dry-run" set "DRY_RUN=true"& shift& goto parse_args
echo ERROR: Unknown ScanReady option: %~1
popd
exit /b 2

:args_done
if not exist "app.py" goto incomplete
if not exist "requirements-lock.txt" goto incomplete
if not exist "scanready\__init__.py" goto incomplete
if not exist "scanready\launch.py" goto incomplete

for /f %%L in ('powershell -NoProfile -Command "(Resolve-Path -LiteralPath '.').Path.Length"') do set "PATH_LENGTH=%%L"
if defined PATH_LENGTH if !PATH_LENGTH! GTR 100 echo WARNING: Extract ScanReady to a path shorter than 100 characters, such as C:\ScanReady.

set "LOCK_HASH="
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'requirements-lock.txt').Hash.ToLower()"') do set "LOCK_HASH=%%H"
if not defined LOCK_HASH (
  echo ERROR: Could not calculate the dependency-lock hash.
  popd
  exit /b 2
)

set "BUNDLED_ARCHIVE=vendor\python\cpython-3.11.16+20260924-x86_64-pc-windows-msvc-install_only.tar.gz"
set "BUNDLED_SHA256=0dc3a404d4d6501f3b8f99d58354410a0b1699dd5c21070778a594db02070dd3"
set "WHEEL_DIR=vendor\wheels\windows-x64"
set "RUNTIME_SOURCE=system-python"
if exist "!BUNDLED_ARCHIVE!" if exist "!WHEEL_DIR!" set "RUNTIME_SOURCE=bundled-archive"
set "READY_ID=!LOCK_HASH!:!BUNDLED_SHA256!"
set "RUNTIME_PY=.runtime\python\python.exe"
if not exist "!RUNTIME_PY!" if exist ".runtime\venv\Scripts\python.exe" set "RUNTIME_PY=.runtime\venv\Scripts\python.exe"
set "RUNTIME_READY=false"
set "RUNTIME_COMPATIBLE=false"
set "READY_HASH="
if exist "!RUNTIME_PY!" (
  "!RUNTIME_PY!" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 set "RUNTIME_COMPATIBLE=true"
)
if exist ".runtime\ready.txt" set /p READY_HASH=<".runtime\ready.txt"
if /I "!RUNTIME_COMPATIBLE!"=="true" if /I "!READY_HASH!"=="!READY_ID!" set "RUNTIME_READY=true"
set "BOOTSTRAP_PY=not-needed"
set "BOOTSTRAP_KIND=path"
if /I "!RUNTIME_READY!"=="false" if /I "!RUNTIME_SOURCE!"=="system-python" call :select_bootstrap
if errorlevel 1 (
  popd
  exit /b 2
)

if /I "!DRY_RUN!"=="true" (
  echo ScanReady setup plan
  echo mode=!MODE!
  echo offline-only=!OFFLINE_ONLY!
  echo runtime-source=!RUNTIME_SOURCE!
  echo bootstrap-python=!BOOTSTRAP_PY!
  echo runtime-compatible=!RUNTIME_COMPATIBLE!
  echo runtime-ready=!RUNTIME_READY!
  if /I "!RUNTIME_READY!"=="true" (
    echo action=reuse-runtime
  ) else if /I "!RUNTIME_SOURCE!"=="bundled-archive" (
    echo action=extract-bundled-runtime
  ) else if exist ".runtime" (
    echo action=rebuild-runtime
  ) else (
    echo action=create-online-runtime
  )
  popd
  exit /b 0
)

if /I "!RUNTIME_READY!"=="false" call :install_runtime
if errorlevel 1 (
  popd
  exit /b 3
)

if /I "!MODE!"=="test" "!RUNTIME_PY!" -m pytest -q& set "RESULT=!ERRORLEVEL!"& goto finish
if /I "!MODE!"=="evaluate" "!RUNTIME_PY!" -m scanready evaluate --split heldout --check& set "RESULT=!ERRORLEVEL!"& goto finish
if /I "!MODE!"=="check" (
  "!RUNTIME_PY!" -m scanready selftest
  if errorlevel 1 (set "RESULT=!ERRORLEVEL!"& goto finish)
  "!RUNTIME_PY!" -m scanready launch --no-browser --smoke
  set "RESULT=!ERRORLEVEL!"
  goto finish
)
"!RUNTIME_PY!" -m scanready launch
set "RESULT=!ERRORLEVEL!"
goto finish

:select_bootstrap
set "BOOTSTRAP_PY="
set "BOOTSTRAP_KIND=path"
if defined SCANREADY_PYTHON (
  if not exist "%SCANREADY_PYTHON%" (
    echo ERROR: SCANREADY_PYTHON does not point to a Python executable.
    exit /b 1
  )
  set "BOOTSTRAP_PY=%SCANREADY_PYTHON%"
) else (
  where py >nul 2>&1
  if not errorlevel 1 (
    set "BOOTSTRAP_KIND=launcher"
    set "BOOTSTRAP_PY=py -3.11"
  ) else (
    for /f "delims=" %%P in ('where python 2^>nul') do if not defined BOOTSTRAP_PY set "BOOTSTRAP_PY=%%P"
  )
)
if not defined BOOTSTRAP_PY (
  echo ERROR: No Python 3.11 runtime is available. Re-extract the complete ScanReady release.
  exit /b 1
)
if /I "!BOOTSTRAP_KIND!"=="launcher" (
  py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
) else (
  "!BOOTSTRAP_PY!" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
)
if errorlevel 1 (
  echo ERROR: ScanReady requires Python 3.11. The selected bootstrap interpreter is incompatible.
  exit /b 1
)
exit /b 0

:install_runtime
if /I "!OFFLINE_ONLY!"=="true" if not exist "!WHEEL_DIR!" (
  echo ERROR: The Windows offline wheel bundle is missing. Re-extract the complete ScanReady folder.
  exit /b 1
)
if exist ".runtime" rmdir /s /q ".runtime"
if /I "!RUNTIME_SOURCE!"=="bundled-archive" (
  set "ACTUAL_ARCHIVE_HASH="
  for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath '!BUNDLED_ARCHIVE!').Hash.ToLower()"') do set "ACTUAL_ARCHIVE_HASH=%%H"
  if /I not "!ACTUAL_ARCHIVE_HASH!"=="!BUNDLED_SHA256!" (
    echo ERROR: The bundled Windows Python archive failed its SHA-256 integrity check.
    exit /b 1
  )
  mkdir ".runtime" >nul 2>&1
  tar -xzf "!BUNDLED_ARCHIVE!" -C ".runtime"
  if errorlevel 1 (
    echo ERROR: ScanReady could not extract its bundled Python runtime.
    exit /b 1
  )
  set "RUNTIME_PY=.runtime\python\python.exe"
) else (
  set "RUNTIME_PY=.runtime\venv\Scripts\python.exe"
  if /I "!BOOTSTRAP_KIND!"=="launcher" (
    py -3.11 -m venv ".runtime\venv"
  ) else (
    "!BOOTSTRAP_PY!" -m venv ".runtime\venv"
  )
)
if not exist "!RUNTIME_PY!" (
  echo ERROR: ScanReady could not create its private runtime.
  exit /b 1
)
if /I "!RUNTIME_SOURCE!"=="bundled-archive" (
  "!RUNTIME_PY!" -m pip install --disable-pip-version-check --no-index --find-links "!WHEEL_DIR!" -r "requirements-lock.txt"
) else if /I "!OFFLINE_ONLY!"=="true" (
  "!RUNTIME_PY!" -m pip install --disable-pip-version-check --no-index --find-links "!WHEEL_DIR!" -r "requirements-lock.txt"
) else (
  "!RUNTIME_PY!" -m pip install --disable-pip-version-check -r "requirements-lock.txt"
)
if errorlevel 1 (
  echo ERROR: ScanReady dependency installation failed.
  exit /b 1
)
>".runtime\ready.txt" echo !READY_ID!
exit /b 0

:incomplete
echo ERROR: ScanReady appears incomplete. Please extract the complete ScanReady folder before running it.
popd
exit /b 2

:finish
popd
exit /b !RESULT!

