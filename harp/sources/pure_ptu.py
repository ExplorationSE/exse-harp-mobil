"""PurePythonPtuSource: PTU-Datei in Echtzeit abspielen, Histogrammierung mit NumPy.

Läuft ohne PicoQuant-Software auf jedem Betriebssystem (Fallback und Testquelle).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..histogram import (CORR_BINS, Frame, correlate, counts_per_channel, hist_len_from_code,
                         histogram_t2, histogram_t3, t3_abs_time_ps)
from ..ptu import Events, PtuHeader, StreamDecoder, open_records, read_header
from .base import AcqParams, DataSource, SourceError, SourceInfo

CHUNK = 262_144  # Records pro Dekodier-Block


class PurePythonPtuSource(DataSource):
    kind = "pure"

    def __init__(self, **_: object) -> None:
        self.hdr: PtuHeader | None = None
        self.rec: np.ndarray | None = None
        self.dec: StreamDecoder | None = None
        self.params = AcqParams()
        self.info: SourceInfo | None = None
        self._pos = 0
        self._buf = Events.empty()
        self._t = 0            # aktuelle Wiedergabeposition (Makrozeit-Einheiten)
        self._t0 = 0           # Zeit des ersten Ereignisses
        self._t_end = 0
        self._last_sync: int | None = None
        self._tail = None      # T2-Korrelation: Ereignisse am Ende des letzten Frames
        self._eof = False

    # ------------------------------------------------------------ Öffnen
    def open(self, path: Path | None) -> SourceInfo:
        if path is None:
            raise SourceError("Keine PTU-Datei angegeben")
        hdr = read_header(path)
        rec = open_records(hdr)
        if rec.size == 0:
            raise SourceError("PTU-Datei enthält keine Records")
        self.hdr, self.rec = hdr, rec
        # Einmaliger schneller Durchlauf: Start-/Endzeit und genutzte Kanäle bestimmen
        dec = StreamDecoder(hdr.record_type)
        t_first = None
        t_last = 0
        max_ch = 0
        n_k0 = 0
        k0_head = np.zeros(0, np.int64)
        for i in range(0, rec.size, 4_000_000):
            ev = dec.decode(np.asarray(rec[i:i + 4_000_000]))
            if len(ev) and hdr.mode == "T2":
                k0 = ev.time[ev.channel == 0]
                n_k0 += int(k0.size)
                if k0_head.size < 100_000:
                    k0_head = np.concatenate([k0_head, k0[:100_000]])
            if len(ev):
                if t_first is None:
                    t_first = int(ev.time[0])
                t_last = max(t_last, int(ev.time.max()))
                max_ch = max(max_ch, int(ev.channel.max()))
        if t_first is None:
            raise SourceError("Keine Photonen-Ereignisse in der Datei")
        self._t0, self._t_end = t_first, t_last
        nch = max(int(hdr.get("HW_InpChannels", 0) or 0), max_ch, 1)
        nch = min(nch, 64)
        g = hdr.global_resolution
        mode = hdr.mode
        res_ps = (hdr.resolution if mode == "T3" else g) * 1e12
        dtime_bits = 15 if hdr.family != "pt" else 12
        self.info = SourceInfo(
            kind=self.kind, name=Path(path).name, mode=mode, num_channels=nch,
            resolution_ps=res_ps, sync_rate_hz=hdr.sync_rate,
            duration_s=(t_last - t_first) * g, num_records=int(rec.size),
            record_type=hdr.record_type_name, hw_type=str(hdr.get("HW_Type", "")),
            max_binning=dtime_bits if mode == "T3" else 20,
            has_histogram=True,
            first_channel=0 if n_k0 > 0 else 1,
            k0_periodic=_is_periodic(k0_head),
        )
        self.rewind()
        return self.info

    def configure(self, params: AcqParams) -> None:
        if (params.ref_channel, params.corr_binning) != (self.params.ref_channel, self.params.corr_binning):
            self._tail = None
        if params.ref_channel != self.params.ref_channel:
            self._last_sync = None   # T2: Start-Kanal des Histogramms gewechselt
        self.params = params

    def start(self) -> None:
        if self.hdr is None:
            raise SourceError("Keine Datei geöffnet")

    def stop(self) -> None:
        pass

    def rewind(self) -> None:
        assert self.hdr is not None
        self.dec = StreamDecoder(self.hdr.record_type)
        self._pos = 0
        self._buf = Events.empty()
        self._t = self._t0
        self._last_sync = None
        self._tail = None
        self._eof = False

    # ------------------------------------------------------------ Wiedergabe
    def _fill_until(self, target: int) -> None:
        assert self.rec is not None and self.dec is not None
        parts = [self._buf]
        last = int(self._buf.time[-1]) if len(self._buf) else -1
        while last < target and self._pos < self.rec.size:
            ev = self.dec.decode(np.asarray(self.rec[self._pos:self._pos + CHUNK]))
            self._pos += CHUNK
            if len(ev):
                parts.append(ev)
                last = int(ev.time[-1])
        if self._pos >= self.rec.size:
            self._eof = True
        self._buf = Events.concat(parts)

    def read_frame(self, meas_time_s: float) -> Frame | None:
        if self.hdr is None or self.info is None:
            raise SourceError("Keine Datei geöffnet")
        g = self.hdr.global_resolution
        nch = self.info.num_channels
        first = self.info.first_channel
        nbins = hist_len_from_code(self.params.hist_len_code)
        binning = min(self.params.binning, self.info.max_binning)
        wrapped = False
        if self._eof and len(self._buf) == 0:
            if not self.params.loop:
                return None
            self.rewind()
            wrapped = True
        units = max(1, int(round(meas_time_s / g)))
        target = self._t + units
        self._fill_until(target)
        k = int(np.searchsorted(self._buf.time, target, side="left"))
        ev = self._buf.take(slice(0, k))
        self._buf = self._buf.take(slice(k, None))
        covered = units
        if self._eof and len(self._buf) == 0:
            covered = max(1, min(units, self._t_end - self._t + 1))
        self._t = target
        bw = self.info.resolution_ps * (2 ** binning)
        kind = "dtime"
        if self.info.mode == "T3":
            hist = histogram_t3(ev, nch, binning, nbins)
            has_hist = True
            sync_count = float(covered)  # eine Makrozeit-Einheit = eine Sync-Periode
            # Korrelation auf absoluter Zeit in ps (nsync·Periode + dtime·Auflösung)
            ct, cc = t3_abs_time_ps(ev, g * 1e12, self.hdr.resolution * 1e12)
            base_ps = 1.0
        else:
            sync_count = float(np.count_nonzero(ev.channel == 0))
            if sync_count == 0 and self.info.sync_rate_hz > 0:   # Sync nicht aufgezeichnet: Header-Rate
                sync_count = self.info.sync_rate_hz * covered * g
            # Referenzkanal ersetzt den Sync: Zeit seit dem letzten Ereignis auf Kref
            sref = min(max(first, self.params.ref_channel), nch)
            hist, self._last_sync, _ = histogram_t2(ev, nch, binning, nbins, self._last_sync, sref, first)
            has_hist = True
            kind = "ref"
            ct, cc = ev.time, ev.channel
            base_ps = g * 1e12
        cb = 1 << self.params.corr_binning
        ref = min(max(first, self.params.ref_channel), nch)
        corr, self._tail = correlate(ct, cc, nch, cb, CORR_BINS, self._tail, ref, first)
        cbw = base_ps * cb
        return Frame(
            hist=hist, counts=counts_per_channel(ev, nch, first), meas_time_s=covered * g,
            bin_width_ps=bw, sync_count=sync_count, t_file_s=(self._t - self._t0) * g,
            progress=min(1.0, (self._t - self._t0) / max(1, self._t_end - self._t0)),
            has_histogram=has_hist, wrapped=wrapped, hist_kind=kind,
            corr=corr, corr_bin_ps=cbw, corr_x0_ps=-(CORR_BINS // 2) * cbw,
        )


def _is_periodic(t: np.ndarray) -> bool:
    """Regelmäßige Ereignisse (Laser-Sync) haben fast konstante Abstände; ein Herald-Detektor nicht."""
    if t.size < 20:
        return False
    d = np.diff(np.sort(t)).astype(np.float64)
    return bool(d.mean() > 0 and d.std() / d.mean() < 0.1)
