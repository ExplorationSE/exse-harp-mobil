# ExSE Harp Mobil - beendet Server und Cloudflare-Tunnel, die mit start_online gestartet wurden.
param([int]$Port = 8765, [switch]$Quiet)
$ErrorActionPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$onWindows = ($env:OS -eq 'Windows_NT')
$logDir = Join-Path $root 'logs'
$pidFile = Join-Path $logDir 'online.pids'
$srvPidFile = Join-Path $logDir 'server.pid'

$ids = @()
foreach ($f in @($pidFile, $srvPidFile)) {
    if (Test-Path -LiteralPath $f) { $ids += @(Get-Content -LiteralPath $f | Where-Object { $_ -match '^\d+$' }) }
}
$n = 0
foreach ($id in ($ids | Select-Object -Unique)) {
    $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    if ($p) {
        if ($onWindows) { & taskkill.exe /PID $id /T /F 2>&1 | Out-Null } else { Stop-Process -Id ([int]$id) -Force }
        $n++
    }
}
# Rueckfallebene (Windows): Prozesse anhand der Kommandozeile finden
if ($onWindows) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='cloudflared.exe'"
    foreach ($p in $procs) {
        $cl = [string]$p.CommandLine
        if ($cl -match "run_server\.py.*--port\s+$Port" -or $cl -match "tunnel.*127\.0\.0\.1:$Port") {
            Stop-Process -Id $p.ProcessId -Force
            $n++
        }
    }
}
foreach ($f in @($pidFile, $srvPidFile, (Join-Path $logDir 'online_info.txt'))) {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}
if (-not $Quiet) {
    if ($n -gt 0) { Write-Host "Harp Mobil beendet ($n Prozess(e))." -ForegroundColor Cyan }
    else { Write-Host 'Es lief kein Harp-Mobil-Online-Prozess.' -ForegroundColor Cyan }
}
