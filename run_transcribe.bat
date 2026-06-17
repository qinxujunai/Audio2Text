@echo off
cd /d "%~dp0"

if not exist ".\.venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found:
    echo .\.venv\Scripts\activate.bat
    pause
    exit /b 1
)

call .\.venv\Scripts\activate.bat

if "%CUDA_RUNTIME_PATH%"=="" (
    if exist "D:\Apps\NVIDIA\CUDA\v12-runtime\bin" set "CUDA_RUNTIME_PATH=D:\Apps\NVIDIA\CUDA\v12-runtime"
)

if "%CUDNN_PATH%"=="" (
    if exist "D:\Apps\NVIDIA\CUDA\v12-runtime\bin" set "CUDNN_PATH=D:\Apps\NVIDIA\CUDA\v12-runtime"
)

if not "%CUDA_RUNTIME_PATH%"=="" (
    if exist "%CUDA_RUNTIME_PATH%\bin" set "PATH=%CUDA_RUNTIME_PATH%\bin;%PATH%"
)

if not "%CUDNN_PATH%"=="" (
    if exist "%CUDNN_PATH%\bin" set "PATH=%CUDNN_PATH%\bin;%PATH%"
)

python -m scripts.run_transcribe %*
pause
