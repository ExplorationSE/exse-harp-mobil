"""Startet den Harp-Mobil-Server.  Aufruf:  python run_server.py [--config config.toml] [--port 8765]"""
import argparse
import os
import sys
import logging

import uvicorn

from harp import __version__
from harp.config import load_config
from harp.server import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description=f"ExSE Harp Mobil {__version__} Server")
    ap.add_argument("--config", default=None, help="Pfad zur config.toml")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--source", default=None, help="pure | snapi | hh500")
    ap.add_argument("--token", default=None,
                    help="Zugangscode (überschreibt config.toml; alternativ Umgebungsvariable HARP_TOKEN)")
    ap.add_argument("--logfile", default=None, help="Ausgaben (inkl. Zugriffsprotokoll) in diese Datei schreiben")
    ap.add_argument("--pidfile", default=None, help="eigene Prozess-ID in diese Datei schreiben (für stop_online)")
    a = ap.parse_args()
    if a.logfile:  # vor der Logging-Konfiguration umleiten, damit auch uvicorn in die Datei schreibt
        logf = open(a.logfile, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = logf
    if a.pidfile:
        with open(a.pidfile, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
    cfg = load_config(a.config)
    if a.host:
        cfg.host = a.host
    if a.port:
        cfg.port = a.port
    if a.source:
        cfg.source = a.source
    if a.token or os.environ.get("HARP_TOKEN"):
        cfg.token = a.token or os.environ["HARP_TOKEN"]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"ExSE Harp Mobil {__version__} – Quelle: {cfg.source}, Daten: {cfg.data_dir}")
    print(f"Im Browser öffnen: http://<IP-dieses-PCs>:{cfg.port}/" + (f"?token=<TOKEN>" if cfg.token else ""))
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info", ws_ping_interval=20)


if __name__ == "__main__":
    main()
