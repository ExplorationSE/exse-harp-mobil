@echo off
rem ExSE Harp Mobile 1.0.0 - einmalige Installation (Windows 10/11, 64 Bit)
setlocal
cd /d "%~dp0"
set "PYEXE="

rem 1) Python suchen: bevorzugt Python 3.13 ueber den Python-Launcher "py"
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3.13"
if defined PYEXE goto check_version
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
if defined PYEXE goto check_version

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
set "PYEXE=python"

:check_version
%PYEXE% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 goto bad_version
echo Verwende:
%PYEXE% -c "import sys; print('  Python', sys.version.split()[0], '-', sys.executable)"

rem 2) Virtuelle Umgebung anlegen
if exist ".venv\Scripts\python.exe" goto install
echo Lege virtuelle Umgebung .venv an ...
%PYEXE% -m venv .venv
if errorlevel 1 goto venv_failed

:install
echo Installiere Pakete (einige Minuten) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pip_failed
echo.
echo Fertig. Starten:
echo   start_online.bat   - Zugriff ueber das Internet (Cloudflare Quick Tunnel)
echo   start_server.bat   - Zugriff nur im lokalen Netz / an diesem PC
echo Optional fuer die snAPI-Quelle:  .venv\Scripts\python.exe -m pip install snAPI
echo.
pause
exit /b 0

:no_python
echo FEHLER: Python wurde nicht gefunden.
echo Bitte Python 3.13 (64 Bit) von https://www.python.org/downloads/ installieren
echo und dabei "Add python.exe to PATH" ankreuzen. Danach diese Datei erneut starten.
pause
exit /b 1

:bad_version
echo FEHLER: Gefundenes Python ist zu alt (benoetigt 3.10 oder neuer, 64 Bit) oder ist nur der
echo Platzhalter aus dem Microsoft Store. Bitte Python 3.13 (64 Bit) von python.org installieren.
pause
exit /b 1

:venv_failed
echo FEHLER: Die virtuelle Umgebung konnte nicht angelegt werden.
pause
exit /b 1

:pip_failed
echo FEHLER: Paketinstallation fehlgeschlagen. Internetverbindung pruefen und erneut starten.
echo Hinter einem Firmen-Proxy ggf.:  set HTTPS_PROXY=http://proxy:port
pause
exit /b 1
