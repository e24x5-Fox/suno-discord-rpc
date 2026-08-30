@echo off
title Suno RPC Build
echo.
echo ============================================
echo   Building Suno RPC (backend + Electron)
echo ============================================
echo.
REM Полная сборка: PyInstaller собирает Python-бэкенд, electron-builder кладёт
REM его внутрь установщика вместе с интерфейсом. Порядок важен: electron-builder
REM берёт готовый python\dist\suno-rpc-backend.exe через extraResources.

set ROOT=%~dp0
set LOG=%ROOT%build_log.txt
echo Build log > "%LOG%"
echo %date% %time% >> "%LOG%"

echo [1/5] Checking Python and Node...
python --version || ( echo [ERROR] Python not found - https://python.org & goto :end )
node --version   || ( echo [ERROR] Node.js not found - https://nodejs.org  & goto :end )

echo [2/5] Installing Python packages...
python -m pip install pyinstaller -r "%ROOT%python\requirements.txt" >> "%LOG%" 2>&1
if errorlevel 1 ( echo [ERROR] pip failed - see build_log.txt & goto :end )

echo [3/5] Building backend EXE...
cd /d "%ROOT%python"
pyinstaller suno_rpc.spec --noconfirm --clean >> "%LOG%" 2>&1
if not exist "%ROOT%python\dist\suno-rpc-backend.exe" (
    echo [ERROR] backend EXE not created - see build_log.txt
    goto :end
)
for %%F in ("%ROOT%python\dist\suno-rpc-backend.exe") do echo   backend: %%~zF bytes

echo [4/5] Installing Electron packages...
cd /d "%ROOT%desktop"
if not exist "%ROOT%desktop\node_modules" ( call npm install >> "%LOG%" 2>&1 )
if errorlevel 1 ( echo [ERROR] npm install failed - see build_log.txt & goto :end )

echo [5/5] Building installer...
call npm run dist >> "%LOG%" 2>&1
if errorlevel 1 ( echo [ERROR] electron-builder failed - see build_log.txt & goto :end )

echo.
echo ============================================
echo   DONE: desktop\dist\
echo ============================================
dir /b "%ROOT%desktop\dist\*.exe"

:end
echo.
echo Log: %LOG%
echo.
echo Press any key...
pause >nul
