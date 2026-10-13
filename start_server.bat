@echo off
rem ExSE Harp Mobil - Start fuer lokales Netz / diesen PC (ohne Tunnel). Vorher einmalig: install_windows.bat
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto no_venv
".venv\Scripts\python.exe" run_server.py %*
pause
exit /b 0

:no_venv
echo FEHLER: .venv fehlt. Bitte zuerst install_windows.bat ausfuehren.
pause
exit /b 1
