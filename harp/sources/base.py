"""Gemeinsame Schnittstelle aller Datenquellen."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path

from ..histogram import Frame


class SourceError(RuntimeError):
    pass


@dataclass
class SourceInfo:
    kind: str                 # 'pure' | 'snapi' | 'hh500'
    name: str                 # Anzeigename (z. B. Dateiname)
    mode: str                 # 'T2' | 'T3' | 'HIST'
    num_channels: int         # Detektorkanäle (ohne Sync)
    resolution_ps: float      # Basis-Binbreite (T3: dtime-Auflösung, T2: Zeittakt)
    sync_rate_hz: float
    duration_s: float
    num_records: int = 0
    record_type: str = ""
    hw_type: str = ""
    max_binning: int = 12
    has_histogram: bool = True
    first_channel: int = 1    # 0: T2-Datei mit Sync-Ereignissen → Sync ist Datenkanal K0
    k0_periodic: bool = False  # K0 regelmäßig (Laser-Sync) statt zufällig (z. B. Herald-Detektor)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AcqParams:
    binning: int = 3          # Bin = resolution * 2**binning
    hist_len_code: int = 0    # Länge = 1024 * 2**code
    loop: bool = True         # Datei am Ende von vorn abspielen
    ref_channel: int = 1      # Korrelation: Referenzkanal (Start)
    corr_binning: int = 9     # Korrelation: Binbreite = Basis × 2^corr_binning (T2: Makrozeit, T3: 1 ps)


class DataSource(ABC):
    """Eine Datenquelle liefert pro Aufruf von read_frame() ein Histogramm-Inkrement.

    read_frame() darf blockieren; der Server ruft es in einem Worker-Thread auf.
    Alle Aufrufe erfolgen sequentiell aus genau einem Thread (wie es auch die HH500Lib verlangt).
    """
    kind = "base"

    @abstractmethod
    def open(self, path: Path | None) -> SourceInfo: ...

    @abstractmethod
    def configure(self, params: AcqParams) -> None: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def rewind(self) -> None: ...

    @abstractmethod
    def read_frame(self, meas_time_s: float) -> Frame | None:
        """Nächstes Frame für meas_time_s Mess-/Dateizeit; None = Ende der Daten."""

    def close(self) -> None:
        pass
