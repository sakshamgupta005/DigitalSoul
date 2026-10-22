@echo off
cd /d "%~dp0"
echo Cleaning old instances...
taskkill /F /IM digitalsoul-notch.exe >nul 2>&1
timeout /t 1 /nobreak >nul 2>&1 || ping 127.0.0.1 -n 2 >nul
set "DIGITALSOUL_PROJECT_DIR=%cd%"
echo Starting DigitalSoul Notch Bar...
start "" "%cd%\.ghost\notch-target\debug\digitalsoul-notch.exe"
