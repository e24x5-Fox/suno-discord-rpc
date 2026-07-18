@echo off
title SunoRPC Build
echo.
echo ============================================
echo   Building SunoRPC
echo ============================================
echo.

set LOG=%~dp0build_log.txt
echo Build log > "%LOG%"
echo %date% %time% >> "%LOG%"

echo [1/4] Checking Python...
python --version
python --version >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo Download https://python.org - check 'Add Python to PATH'
    goto :end
)

echo [2/4] Installing packages...
python -m pip install pyinstaller pypresence websockets pystray pillow aiohttp >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] pip failed - see build_log.txt
    goto :end
)
echo OK

echo [3/4] Checking files...
if not exist "%~dp0suno_rpc.py"   ( echo [ERROR] suno_rpc.py missing!   & goto :end )
if not exist "%~dp0suno_rpc.ico"  ( echo [ERROR] suno_rpc.ico missing!  & goto :end )
if not exist "%~dp0suno_rpc.spec" ( echo [ERROR] suno_rpc.spec missing! & goto :end )
echo OK

echo [4/4] Building EXE...
cd /d "%~dp0"
pyinstaller suno_rpc.spec --noconfirm --clean >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller failed - see build_log.txt
    goto :end
)

if exist "%~dp0dist\SunoRPC.exe" (
    echo.
    echo ============================================
    echo   DONE: dist\SunoRPC.exe
    echo ============================================
    for %%F in ("%~dp0dist\SunoRPC.exe") do echo   Size: %%~zF bytes
    echo   Ошибки смотри во вкладке "Логи" ^(значок ⚠^) в самом приложении.
) else (
    echo [ERROR] EXE not created - see build_log.txt
)
:end
echo.
echo Log: %LOG%
echo.
echo Press any key...
pause >nul
