"""Datenquellen (Abstraktion): reiner Python-PTU-Replay, snAPI-PTU-Replay, HH500-Live (Stub)."""
from .base import AcqParams, DataSource, SourceError, SourceInfo


def create_source(kind: str, **kwargs) -> DataSource:
    kind = kind.lower()
    if kind in ("pure", "pure_python", "purepythonptu", "python"):
        from .pure_ptu import PurePythonPtuSource
        return PurePythonPtuSource(**kwargs)
    if kind in ("snapi", "snapi_ptu", "snapiptu"):
        from .snapi_ptu import SnapiPtuSource
        return SnapiPtuSource(**kwargs)
    if kind in ("hh500", "hh500_live", "live"):
        from .hh500_live import HH500LiveSource
        return HH500LiveSource(**kwargs)
    raise SourceError(f"Unbekannte Datenquelle: {kind}")


__all__ = ["AcqParams", "DataSource", "SourceError", "SourceInfo", "create_source"]
