@echo off
rem ExSE Harp Mobile 1.0.0 - online ueber Cloudflare Quick Tunnel starten
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_online.ps1" %*
if errorlevel 1 pause
