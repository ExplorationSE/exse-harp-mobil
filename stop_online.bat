@echo off
rem ExSE Harp Mobile 1.0.0 - Online-Betrieb beenden (Server und Tunnel)
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_online.ps1" %*
pause
