"""Steuerbefehle: Validierung, Steuer-Sperre (ein steuernder Client) und Befehlsprotokoll."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Wertebereiche (V1, Simulation). Wo sinnvoll an HH500Lib v1.0.0.0 (hh500defin.h) angelehnt:
#   Histogrammlänge: Code 0..7 → 1024..131072 Bins (MINLENCODE..MAXLENCODE)
#   Binning: Verdopplungsstufen; HH500 erlaubt bis 24 (MAXBINSTEPS), für T3-Dateien sind
#   wegen der 15-Bit-dtime nur 0..15 sinnvoll.
LIMITS = {
    "refresh_ms": (100, 2000),
    "speed": (0.05, 100.0),
    "binning": (0, 15),
    "hist_len_code": (0, 7),
    "window_frames": (2, 300),
    "display_points": (64, 8192),
    "corr_binning": (0, 24),
}


class Settings(BaseModel):
    """Alle über die Web-Oberfläche einstellbaren Parameter."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    refresh_ms: int = Field(150, ge=LIMITS["refresh_ms"][0], le=LIMITS["refresh_ms"][1],
                            description="Frame-Intervall (Wanduhr) in ms; 150 ms ≈ 6–7 Hz")
    speed: float = Field(1.0, ge=LIMITS["speed"][0], le=LIMITS["speed"][1],
                         description="Wiedergabegeschwindigkeit (1 = Echtzeit)")
    binning: int = Field(3, ge=LIMITS["binning"][0], le=LIMITS["binning"][1],
                         description="Bin = Auflösung × 2^binning")
    hist_len_code: int = Field(0, ge=LIMITS["hist_len_code"][0], le=LIMITS["hist_len_code"][1],
                               description="Histogrammlänge = 1024 × 2^code")
    channels: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])
    mode: Literal["accumulate", "sliding"] = "accumulate"
    window_frames: int = Field(20, ge=LIMITS["window_frames"][0], le=LIMITS["window_frames"][1])
    display_points: int = Field(1024, ge=LIMITS["display_points"][0], le=LIMITS["display_points"][1])
    loop: bool = True
    ref_channel: int = Field(1, ge=0, le=64, description="Referenzkanal der Kreuzkorrelation")
    corr_binning: int = Field(9, ge=LIMITS["corr_binning"][0], le=LIMITS["corr_binning"][1],
                              description="Korrelation: Binbreite = Basis × 2^n (T3: 1 ps, T2: Makrozeit)")

    @field_validator("channels")
    @classmethod
    def _channels(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("mindestens ein Kanal muss ausgewählt sein")
        if len(set(v)) != len(v):
            raise ValueError("Kanäle doppelt angegeben")
        for c in v:
            if not 0 <= c <= 64:
                raise ValueError(f"ungültiger Kanal {c}")
        return sorted(v)

    @property
    def frame_meas_time_s(self) -> float:
        """Messzeit (Dateizeit) pro Frame = Frame-Intervall × Geschwindigkeit."""
        return self.refresh_ms / 1000.0 * self.speed


class CommandError(ValueError):
    pass


ALLOWED_CMDS = {"start", "stop", "reset", "set", "open_file", "acquire_control", "release_control",
                "rewind"}


def validate_set(current: Settings, params: dict, num_channels: int, max_binning: int,
                 first_channel: int = 1) -> Settings:
    """Prüft eine Änderung serverseitig; liefert neue Settings oder wirft CommandError."""
    if not isinstance(params, dict) or not params:
        raise CommandError("'params' muss ein nicht-leeres Objekt sein")
    unknown = set(params) - set(Settings.model_fields)
    if unknown:
        raise CommandError(f"Unbekannte Parameter: {', '.join(sorted(unknown))}")
    merged = current.model_dump() | params
    try:
        new = Settings(**merged)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        raise CommandError(msgs) from None
    bad = [c for c in new.channels if c > num_channels or c < first_channel]
    if bad:
        raise CommandError(f"Kanal {bad} nicht vorhanden (Quelle hat {num_channels} Kanäle)")
    if not first_channel <= new.ref_channel <= num_channels:
        raise CommandError(f"Referenzkanal {new.ref_channel} nicht vorhanden (Quelle hat {num_channels} Kanäle)")
    if new.binning > max_binning:
        raise CommandError(f"binning > {max_binning} für diese Quelle nicht sinnvoll")
    return new


def safe_file(data_dir: Path, name: str) -> Path:
    """Nur Dateinamen aus dem Datenordner zulassen (kein Pfad-Traversal)."""
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
        raise CommandError("ungültiger Dateiname")
    p = (data_dir / name).resolve()
    if p.parent != data_dir.resolve() or not p.is_file() or p.suffix.lower() != ".ptu":
        raise CommandError(f"Datei nicht gefunden: {name}")
    return p


class ControlLock:
    """Nur ein Client darf steuern. Sperre verfällt bei Trennung oder nach Inaktivität."""

    def __init__(self, timeout_s: float = 300.0) -> None:
        self.timeout_s = timeout_s
        self.holder: str | None = None
        self.last: float = 0.0
        self._lock = threading.Lock()

    def _expired(self) -> bool:
        return self.holder is not None and (time.monotonic() - self.last) > self.timeout_s

    def acquire(self, client: str) -> bool:
        with self._lock:
            if self.holder in (None, client) or self._expired():
                self.holder, self.last = client, time.monotonic()
                return True
            return False

    def check(self, client: str) -> bool:
        with self._lock:
            if self._expired():
                self.holder = None
            if self.holder == client:
                self.last = time.monotonic()
                return True
            return False

    def release(self, client: str) -> None:
        with self._lock:
            if self.holder == client:
                self.holder = None


class CommandLog:
    """Protokolliert jeden Befehl als JSON-Zeile (logs/commands.jsonl)."""

    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / "commands.jsonl"
        self._lock = threading.Lock()

    def write(self, client: str, addr: str, cmd: str, params: object, ok: bool, msg: str = "") -> None:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "client": client, "addr": addr,
                 "cmd": cmd, "params": params, "ok": ok, "msg": msg}
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
