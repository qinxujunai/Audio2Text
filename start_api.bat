@echo off
title Praxis AI - Wanxiang Chengwen
setlocal

cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_api_bootstrap.ps1"

echo.
echo [INFO] Service stopped or failed.
pause
