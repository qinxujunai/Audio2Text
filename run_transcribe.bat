@echo off
cd /d "%~dp0"

if not exist ".\.venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found:
    echo .\.venv\Scripts\activate.bat
    pause
    exit /b 1
)

call .\.venv\Scripts\activate.bat
python -m scripts.run_transcribe %*
pause
