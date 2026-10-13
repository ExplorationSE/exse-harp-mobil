#!/usr/bin/env bash
# Beendet den Online-Betrieb von Harp Mobil: Tunnel (cloudflared/tunnelmole/ssh) und Server.
cd "$(dirname "$0")"
pkill -f "cloudflared tunnel .*127.0.0.1:8765" && echo "cloudflared beendet"
pkill -f "tmole 8765" && echo "tunnelmole beendet"
pkill -f "ssh .*-R.*127.0.0.1:8765" && echo "ssh-Tunnel beendet"
pkill -f "run_server.py --host 127.0.0.1 --port 8765" && echo "Server beendet"
rm -f logs/online.pids
sleep 1
pgrep -af "run_server.py|cloudflared tunnel|tmole" || echo "Keine Harp-Mobil-Prozesse mehr aktiv."
