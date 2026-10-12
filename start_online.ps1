# ExSE Harp Mobile 1.0.0 - Online-Start ueber einen Cloudflare Quick Tunnel (ohne Konto)
# Start normalerweise per Doppelklick auf start_online.bat.
# Optionen:  -Port 8765   -NoTunnel (nur lokal)   -NoWait (nicht auf Enter warten)
# Umgebungsvariablen (optional):
#   HARP_TOKEN            fester Zugangscode statt eines zufaelligen
#   HARP_TUNNEL_PROTOCOL  z. B. "http2", falls QUIC (UDP) im Netz blockiert ist
#   HARP_PYTHON / HARP_CLOUDFLARED   abweichende Pfade (fuer Tests)
param([int]$Port = 8765, [switch]$NoTunnel, [switch]$NoWait)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location -LiteralPath $root
$onWindows = ($env:OS -eq 'Windows_NT')
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$pidFile = Join-Path $logDir 'online.pids'
$srvPidFile = Join-Path $logDir 'server.pid'
$infoFile = Join-Path $logDir 'online_info.txt'

function Say([string]$m) { Write-Host $m -ForegroundColor Cyan }
function Warn([string]$m) { Write-Host "WARNUNG: $m" -ForegroundColor Yellow }
function Stop-All {
    & (Join-Path $root 'stop_online.ps1') -Port $Port -Quiet
}
function Fail([string]$m) {
    Write-Host ""
    Write-Host "FEHLER: $m" -ForegroundColor Red
    Stop-All
    exit 1   # start_online.bat haelt das Fenster dann mit 'pause' offen
}
function Read-Shared([string]$path) {
    # Logdatei lesen, waehrend ein anderer Prozess hineinschreibt
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    try {
        $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
        $sr = New-Object System.IO.StreamReader($fs)
        $t = $sr.ReadToEnd()
        $sr.Close()
        return $t
    } catch { return '' }
}
function Test-PortInUse([int]$p) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $p); return $true } catch { return $false } finally { $c.Close() }
}
function Get-Tail([string]$path) {
    $t = Read-Shared $path
    if ($t.Length -gt 1500) { $t = $t.Substring($t.Length - 1500) }
    return $t
}

$ver = '?'
$vm = Select-String -Path (Join-Path $root 'harp\__init__.py') -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if ($vm) { $ver = $vm.Matches[0].Groups[1].Value }
Say "== ExSE Harp Mobile $ver - Online-Start =="

# --- 1. Python-Umgebung --------------------------------------------------------------
$py = $env:HARP_PYTHON
if (-not $py) { $py = [System.IO.Path]::Combine($root, '.venv', 'Scripts', 'python.exe') }
if (-not (Test-Path -LiteralPath $py)) {
    Fail "Python-Umgebung nicht gefunden ($py). Bitte zuerst install_windows.bat ausfuehren."
}

# --- 2. Laeuft schon etwas? ----------------------------------------------------------
if (Test-Path -LiteralPath $pidFile) {
    $alive = @(Get-Content -LiteralPath $pidFile | Where-Object { $_ -match '^\d+$' } |
               Where-Object { Get-Process -Id ([int]$_) -ErrorAction SilentlyContinue })
    if ($alive.Count -gt 0) {
        Fail 'Harp Mobile laeuft bereits. Bitte zuerst stop_online.bat ausfuehren.'
    }
    Remove-Item -LiteralPath $pidFile -Force
}
if (Test-PortInUse $Port) {
    Fail "Port $Port ist bereits belegt (laeuft start_server.bat noch?). Programm beenden oder stop_online.bat ausfuehren."
}

# --- 3. Zugangscode ------------------------------------------------------------------
$alphabet = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'   # ohne 0/O und 1/I/L
if ($env:HARP_TOKEN) {
    $code = $env:HARP_TOKEN
    Say 'Zugangscode aus der Umgebungsvariablen HARP_TOKEN uebernommen.'
} else {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 64
    $rng.GetBytes($bytes)
    $sb = New-Object System.Text.StringBuilder
    foreach ($b in $bytes) {
        if ($b -lt 248) { [void]$sb.Append($alphabet[$b % 31]) }   # 248 = 8*31 -> gleichverteilt
        if ($sb.Length -ge 10) { break }
    }
    $code = $sb.ToString()
}

# --- 4. Server starten (nur 127.0.0.1) -----------------------------------------------
$env:HARP_TOKEN = $code
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$srvLog = Join-Path $logDir 'server-online.log'
if (Test-Path -LiteralPath $srvPidFile) { Remove-Item -LiteralPath $srvPidFile -Force }
# Ausgaben schreibt der Server selbst in die Logdatei (--logfile), daher keine Umleitung noetig.
$spArgs = @{
    FilePath         = $py
    ArgumentList     = @('run_server.py', '--host', '127.0.0.1', '--port', "$Port",
                         '--pidfile', "`"$srvPidFile`"", '--logfile', "`"$srvLog`"")
    WorkingDirectory = $root
    PassThru         = $true
}
if ($onWindows) { $spArgs['WindowStyle'] = 'Hidden' }
Say "Starte Server auf http://127.0.0.1:$Port ..."
$server = Start-Process @spArgs
Set-Content -LiteralPath $pidFile -Value $server.Id -Encoding Ascii

$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if ($server.HasExited) { break }
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:$Port/api/health"
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if (-not $ok) { Fail ("Server startet nicht. Letzte Meldungen (logs\server-online.log):`n" + (Get-Tail $srvLog)) }
if (Test-Path -LiteralPath $srvPidFile) { Add-Content -LiteralPath $pidFile -Value (Get-Content -LiteralPath $srvPidFile) -Encoding Ascii }
Say 'Server laeuft (nur lokal erreichbar, Anmeldung mit Zugangscode aktiv).'

if ($NoTunnel) {
    Set-Content -LiteralPath $infoFile -Value "URL: http://127.0.0.1:$Port`r`nCode: $code" -Encoding Ascii
    & $py ([System.IO.Path]::Combine($root, 'tools', 'print_online_info.py')) "http://127.0.0.1:$Port" $code
} else {
    # --- 5. cloudflared besorgen -----------------------------------------------------
    $cf = $env:HARP_CLOUDFLARED
    if (-not $cf) {
        $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
        if ($cmd) { $cf = $cmd.Source }
    }
    if (-not $cf) {
        $cf = [System.IO.Path]::Combine($root, 'tools', 'cloudflared.exe')
        if (-not (Test-Path -LiteralPath $cf)) {
            $url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
            Say "cloudflared nicht gefunden - lade offizielles Release von GitHub:`n  $url"
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
                $ProgressPreference = 'SilentlyContinue'
                $tmp = "$cf.download"
                Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
                Move-Item -LiteralPath $tmp -Destination $cf -Force
            } catch {
                Fail ("Download von cloudflared fehlgeschlagen: $($_.Exception.Message)`n" +
                      "Alternative: in einer Eingabeaufforderung  winget install --id Cloudflare.cloudflared  ausfuehren " +
                      "und start_online.bat erneut starten.")
            }
        }
    }
    Say "cloudflared: $cf"

    # --- 6. Tunnel starten -----------------------------------------------------------
    $cfLog = Join-Path $logDir 'cloudflared.log'
    if (Test-Path -LiteralPath $cfLog) { Remove-Item -LiteralPath $cfLog -Force }
    $cfArgs = @('tunnel', '--no-autoupdate', '--logfile', "`"$cfLog`"")
    if ($env:HARP_TUNNEL_PROTOCOL) { $cfArgs += @('--protocol', $env:HARP_TUNNEL_PROTOCOL) }
    $cfArgs += @('--url', "http://127.0.0.1:$Port")
    $tpArgs = @{
        FilePath         = $cf
        ArgumentList     = $cfArgs
        WorkingDirectory = $root
        PassThru         = $true
    }
    if ($onWindows) { $tpArgs['WindowStyle'] = 'Hidden' }
    Say 'Starte Cloudflare Quick Tunnel (kann 10-30 Sekunden dauern) ...'
    try { $tunnel = Start-Process @tpArgs } catch { Fail "cloudflared konnte nicht gestartet werden: $($_.Exception.Message)" }
    Add-Content -LiteralPath $pidFile -Value $tunnel.Id -Encoding Ascii

    $publicUrl = $null
    for ($i = 0; $i -lt 120 -and -not $publicUrl; $i++) {
        Start-Sleep -Milliseconds 500
        $t = Read-Shared $cfLog
        $m = [regex]::Matches($t, 'https://[a-z0-9-]+\.trycloudflare\.com')
        foreach ($x in $m) { if ($x.Value -ne 'https://api.trycloudflare.com') { $publicUrl = $x.Value; break } }
        if ($tunnel.HasExited) { break }
    }
    if (-not $publicUrl) {
        Fail ("Keine trycloudflare-Adresse erhalten. Letzte cloudflared-Meldungen:`n" + (Get-Tail $cfLog))
    }
    $registered = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ((Read-Shared $cfLog) -match 'Registered tunnel connection') { $registered = $true; break }
        if ($tunnel.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $registered) {
        Warn ('Der Tunnel meldet noch keine Verbindung zu Cloudflare. Moeglicherweise blockiert das Netzwerk ' +
              'ausgehende Verbindungen (Port 7844, UDP/TCP). Versuch: set HARP_TUNNEL_PROTOCOL=http2 und neu starten. ' +
              'Details: logs\cloudflared.log')
    }
    Set-Content -LiteralPath $infoFile -Value "URL: $publicUrl`r`nCode: $code" -Encoding Ascii
    & $py ([System.IO.Path]::Combine($root, 'tools', 'print_online_info.py')) $publicUrl $code
    Say 'Hinweis: Die neue Adresse ist oft erst nach 10-60 Sekunden weltweit erreichbar.'
}

if ($NoWait) { exit 0 }
Write-Host ''
[void](Read-Host 'Zum BEENDEN (Server und Tunnel) Enter druecken')
Stop-All
Say 'Beendet.'
