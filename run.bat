@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  Entry point: hand over to launcher.py (interactive launcher).
rem  NOTE: keep this file ASCII-only. Chinese text inside a .bat
rem  breaks cmd.exe parsing on consoles whose code page is not GBK.
rem  All Chinese guidance is printed by launcher.py instead.
rem ---------------------------------------------------------------

rem --- 1) Python 3 already on PATH ---
where python >nul 2>nul
if not errorlevel 1 (
  python launcher.py
  goto :done
)

rem --- 2) the Windows Python Launcher (py.exe) ---
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launcher.py
  goto :done
)

rem --- 3) no Python at all: use the bundled exe, it fetches a private runtime ---
if exist "fincheck-launcher.exe" (
  echo [INFO] Python 3 not found on PATH. Starting fincheck-launcher.exe instead.
  echo        It will download a private Python runtime automatically.
  echo.
  fincheck-launcher.exe
  goto :done
)

echo [ERROR] Python 3 not found on PATH and fincheck-launcher.exe is missing.
echo         Please install Python 3.10 or newer and tick "Add Python to PATH".
echo         Opening the official download page now.
start "" "https://www.python.org/downloads/windows/"

:done
echo.
pause
