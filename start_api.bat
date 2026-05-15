@echo off
title Praxis AI - Wanxiang Chengwen
setlocal

cd /d "%~dp0"

if not exist ".\.venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment not found:
    echo .\.venv\Scripts\python.exe
    pause
    exit /b 1
)

call .\.venv\Scripts\activate.bat
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\workspace\runtime\playwright-browsers"

if "%AUDIO2TEXT_API_PORT%"=="" (
    set "AUDIO2TEXT_DISPLAY_PORT=8000"
) else (
    set "AUDIO2TEXT_DISPLAY_PORT=%AUDIO2TEXT_API_PORT%"
)

echo [INFO] Project directory:
echo %CD%
echo.
echo [INFO] Local URL:
echo http://127.0.0.1:%AUDIO2TEXT_DISPLAY_PORT%
echo.

if "%AUDIO2TEXT_LOCAL_ONLY%"=="1" (
    echo [INFO] Local-only mode. No public tunnel will be started.
    echo [INFO] To share a public link, close this window and run start_api.bat without AUDIO2TEXT_LOCAL_ONLY=1.
    echo.
    python -m scripts.start_api
) else (
    echo [INFO] Starting local API and public preview tunnel.
    echo [INFO] Keep this window open while other people use the link.
    echo [INFO] To start local-only next time: set AUDIO2TEXT_LOCAL_ONLY=1
    echo.
    python -m scripts.public_preview
)

echo.
echo [INFO] Service stopped or failed.
pause
