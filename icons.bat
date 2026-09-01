@echo off
title Suno RPC Icons
echo.
echo ============================================
echo   Rebuilding icons from Krita
echo ============================================
echo.
REM Rebuilds every app icon from ikona\ishod\italon.kra in two steps:
REM   import-icons.py - pulls each character out of the .kra in all plate shapes
REM   build-icons.py  - turns them into .ico sets and writes icons/variants.json
REM Nothing has to be toggled in Krita first: import-icons.py sets layer
REM visibility itself for every shape, so the saved state does not matter.
REM Comments here are ASCII on purpose - cmd reads this file in the OEM code
REM page and mangles Cyrillic REM lines into bogus commands.

set ROOT=%~dp0
cd /d "%ROOT%"

echo [1/3] Checking Python and Pillow...
python --version || ( echo [ERROR] Python not found - https://python.org & goto :end )
python -c "import PIL" 2>nul || (
    echo   Pillow not installed, installing...
    python -m pip install Pillow || ( echo [ERROR] pip failed & goto :end )
)

echo.
echo [2/3] Importing characters from Krita...
python import-icons.py
if errorlevel 1 ( echo [ERROR] import-icons.py failed & goto :end )

echo.
echo [3/3] Building .ico sets...
python build-icons.py
if errorlevel 1 ( echo [ERROR] build-icons.py failed & goto :end )

echo.
echo ============================================
echo   DONE
echo ============================================
echo.
echo Restart Suno RPC to see the new icons in the picker.
echo Commit and push - Discord loads icons by URL from the repo.

:end
echo.
echo Press any key...
pause >nul
