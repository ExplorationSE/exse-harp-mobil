"""Konfiguration aus config.toml laden."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8765
    source: str = "pure"                 # pure | snapi | hh500
    data_dir: Path = BASE_DIR / "data"
    default_file: str = "Demo_Synthetisch_T3_Fluoreszenz_4Kanal_5min.ptu"
    token: str = ""                      # leer = keine Authentifizierung (nur LAN!)
    log_dir: Path = BASE_DIR / "logs"
    lock_timeout_s: float = 300.0
    snapi_windows_cumulative: bool = False
    snapi_base_window_s: float = 0.05
    autostart: bool = True
    defaults: dict = field(default_factory=dict)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else BASE_DIR / "config.toml"
    cfg = Config()
    if not path.exists():
        return cfg
    with open(path, "rb") as f:
        d = tomllib.load(f)
    srv = d.get("server", {})
    cfg.host = srv.get("host", cfg.host)
    cfg.port = int(srv.get("port", cfg.port))
    cfg.token = str(srv.get("token", cfg.token))
    cfg.lock_timeout_s = float(srv.get("lock_timeout_s", cfg.lock_timeout_s))
    src = d.get("source", {})
    cfg.source = src.get("kind", cfg.source)
    dd = Path(src.get("data_dir", str(cfg.data_dir)))
    cfg.data_dir = dd if dd.is_absolute() else (BASE_DIR / dd)
    cfg.default_file = src.get("default_file", cfg.default_file)
    cfg.snapi_windows_cumulative = bool(src.get("snapi_windows_cumulative", False))
    cfg.snapi_base_window_s = float(src.get("snapi_base_window_s", 0.05))
    cfg.autostart = bool(src.get("autostart", cfg.autostart))
    ld = Path(d.get("logging", {}).get("log_dir", str(cfg.log_dir)))
    cfg.log_dir = ld if ld.is_absolute() else (BASE_DIR / ld)
    cfg.defaults = d.get("defaults", {})
    return cfg
