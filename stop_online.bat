@echo off
rem ExSE Harp Mobile - Online-Betrieb beenden (Server und Tunnel)
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_online.ps1" %*
pause
