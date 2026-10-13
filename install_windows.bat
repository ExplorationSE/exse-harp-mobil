@echo off
rem ExSE Harp Mobil - einmalige Installation (Windows 10/11, 64 Bit)
setlocal
cd /d "%~dp0"
set "PYEXE="

rem 1) Python suchen. Jeder Kandidat wird wirklich ausgefuehrt und muss die Markierung HARP_PY_OK
rem    ausgeben (Version >= 3.10, 64 Bit). Nur den Fehlercode zu pruefen reicht nicht: der neue
rem    Python-Installationsmanager ("py") liefert bei fehlender Version teils negative Fehlercodes,
rem    die "if errorlevel 1" nicht erkennt.
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3" "python") do (
  if not defined PYEXE call :probe %%~C
)
if not defined PYEXE goto no_python

echo Verwende:
%PYEXE% -c "import sys; print('  Python', sys.version.split()[0], '-', sys.executable)"

rem 2) Virtuelle Umgebung anlegen
if exist ".venv\Scripts\python.exe" goto install
echo Lege virtuelle Umgebung .venv an ...
%PYEXE% -m venv .venv
if not "%errorlevel%"=="0" goto venv_failed
if not exist ".venv\Scripts\python.exe" goto venv_failed

:install
echo Installiere Pakete (einige Minuten) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not "%errorlevel%"=="0" goto pip_failed
echo.
echo Fertig. Starten:
echo   start_online.bat   - Zugriff ueber das Internet (Cloudflare Quick Tunnel)
echo   start_server.bat   - Zugriff nur im lokalen Netz / an diesem PC
echo Optional fuer die snAPI-Quelle:  .venv\Scripts\python.exe -m pip install snAPI
echo.
pause
exit /b 0

:probe
rem Aufruf: call :probe <befehl> [argumente] - setzt PYEXE, wenn der Befehl ein passendes Python startet
set "HARP_OUT="
for /f "usebackq delims=" %%L in (`%* -c "import sys; print('HARP_PY_OK' if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 'HARP_PY_OLD')" 2^>nul`) do set "HARP_OUT=%%L"
if "%HARP_OUT%"=="HARP_PY_OK" set "PYEXE=%*"
exit /b 0

:no_python
echo FEHLER: Kein passendes Python gefunden (benoetigt 3.10 oder neuer, 64 Bit).
echo Geprueft wurden: py -3.13, py -3.12, py -3.11, py -3, python
echo Abhilfe (eine der beiden Moeglichkeiten):
echo   - Mit dem Python-Installationsmanager in einer Eingabeaufforderung:  py install 3.13
echo   - Oder Python 3.13 (64 Bit) von https://www.python.org/downloads/ installieren
echo     und dabei "Add python.exe to PATH" ankreuzen.
echo Danach diese Datei erneut starten. Es wurde nichts installiert.
pause
exit /b 1

:venv_failed
echo FEHLER: Die virtuelle Umgebung .venv konnte nicht angelegt werden. Abbruch vor der Paketinstallation.
echo Ggf. den Ordner .venv loeschen und diese Datei erneut starten.
pause
exit /b 1

:pip_failed
echo FEHLER: Paketinstallation fehlgeschlagen. Internetverbindung pruefen und erneut starten.
echo Hinter einem Firmen-Proxy ggf.:  set HTTPS_PROXY=http://proxy:port
pause
exit /b 1
