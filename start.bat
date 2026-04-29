@echo off
chcp 65001 >nul 2>&1
title STEM Tutor Agent

echo ============================================
echo   STEM Tutor Agent - Remote Access Launcher
echo ============================================
echo.

where cloudflared >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] cloudflared not found. Please install: winget install Cloudflare.cloudflared
    pause
    exit /b 1
)

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] python not found.
    pause
    exit /b 1
)

if not exist "key.env" if not exist "..\key.env" (
    echo [WARN] key.env not found in current or parent directory.
    echo        Copy key.env.example to key.env and fill in your API key.
    echo.
    pause
    exit /b 1
)

set PORT=8000

echo [1/3] Starting web server on port %PORT% ...
start /b python -m uvicorn web.app:app --host 0.0.0.0 --port %PORT% --reload
if %errorlevel% neq 0 (
    echo [ERROR] Failed to start web server.
    pause
    exit /b 1
)

echo [2/3] Waiting for server to be ready ...
:wait_loop
curl -s -o nul http://localhost:%PORT%/ >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
echo       Server is ready at http://localhost:%PORT%

echo [3/3] Starting Cloudflare Tunnel ...
echo       Your public URL will appear below:
echo.
echo ============================================
cloudflared tunnel --url http://localhost:%PORT% --protocol http2 --retries 5
echo ============================================

echo.
echo Shutting down web server ...
wmic process where "commandline like '%%uvicorn web.app:app%%'" call terminate >nul 2>&1
echo Done.
pause
