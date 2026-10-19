@echo off
rem ---------------------------------------------------------------
rem  Rebuild fincheck-launcher.exe with PyInstaller (needs Python 3.10+ on PATH).
rem  Keep this file ASCII-only, see run.bat for the reason.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

python -m pip install --quiet --upgrade pyinstaller
python -m PyInstaller --onefile --noconfirm --clean --name fincheck-launcher ^
  --distpath . --workpath build --specpath build launcher.py

echo.
echo Done. fincheck-launcher.exe is in this folder; the build\ folder can be deleted.
pause
