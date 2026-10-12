"""HH500LiveSource – Platzhalter (Stub) für die echte HydraHarp 500 über snAPI.

In V1 NICHT implementiert. Die Klasse dokumentiert die geplante Abbildung der Web-Steuerbefehle
auf snAPI-Aufrufe (Namen aus snAPI/Main.py, Klasse Device bzw. Histogram; ungetestet):

  Verbindung:      sn = snAPI(); sn.getDevice(); sn.initDevice(MeasMode.Histogram oder MeasMode.T3)
                   sn.loadIniConfig("config/HH500.ini")   # Beispielkonfiguration aus snAPI-Demos
  Binning:         sn.device.setBinning(binning)           # HH500: Code 1..24 laut snAPI-Doku
  Histogrammlänge: sn.device.setHistoLength(lengthCode)    # Länge = 2**(10+lengthCode)
  Sync-Teiler:     sn.device.setSyncDiv(div)
  Trigger:         sn.device.setInputTrigMode(ch, TrigMode.Edge|CFD)
                   sn.device.setInputEdgeTrig(ch, level_mV, edge) / sn.device.setInputCFD(ch, lvl, zc)
  Offsets:         sn.device.setInputChannelOffset(ch, ps), sn.device.setSyncChannelOffset(ps)
  Kanäle:          sn.device.setInputChannelEnable(ch, 0|1)
  Zählraten:       sn.device.getCountRates()   # [Sync, Kanal1, ...], 100-ms-Gate
  Messung:         sn.histogram.setSequenceMode(SequenceMode.Timer, wait4newData=False, param=frame_s)
                   sn.histogram.measure(acqTime=0, waitFinished=False); getData(); stopMeasure()

Vor einer Umsetzung müssen Wertebereiche gegen hh500defin.h (HH500Lib v1.0.0.0) und das
Verhalten von snAPI am echten Gerät geprüft werden.
"""
from __future__ import annotations

from pathlib import Path

from ..histogram import Frame
from .base import AcqParams, DataSource, SourceError, SourceInfo


class HH500LiveSource(DataSource):
    kind = "hh500"

    def __init__(self, **_: object) -> None:
        self.params = AcqParams()

    def open(self, path: Path | None) -> SourceInfo:
        raise SourceError("HH500-Live-Quelle ist in V1 nur ein Platzhalter und nicht implementiert. "
                          "Bitte source = \"pure\" oder \"snapi\" verwenden.")

    def configure(self, params: AcqParams) -> None:
        self.params = params

    def start(self) -> None:
        raise SourceError("HH500-Live-Quelle nicht implementiert (V1)")

    def stop(self) -> None:
        pass

    def rewind(self) -> None:
        pass

    def read_frame(self, meas_time_s: float) -> Frame | None:
        raise SourceError("HH500-Live-Quelle nicht implementiert (V1)")
