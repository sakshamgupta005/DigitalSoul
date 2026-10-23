@echo off
cd /d "%~dp0"
echo ========================================================
echo      DigitalSoul - Local Agent & Companion Dashboard
echo ========================================================
echo.

:: Check if port 7777 is already listening
netstat -ano | findstr :7777 >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] DigitalSoul Daemon is already running on port 7777.
) else (
    echo [*] Launching Python FastAPI Daemon on 127.0.0.1:7777...
    start "DigitalSoul Daemon" /b ".\.venv\Scripts\python.exe" -m uvicorn agent.ghost.app:app --host 0.0.0.0 --port 7777
    timeout /t 3 /nobreak >nul
)

echo [*] Opening Companion Dashboard in your browser...
start http://127.0.0.1:7777/dashboard

echo [OK] Active! You can access the dashboard at:
echo      http://127.0.0.1:7777/dashboard
echo      http://127.0.0.1:7777/docs
echo.
