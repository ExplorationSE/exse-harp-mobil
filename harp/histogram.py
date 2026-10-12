"""Server-seitige Histogrammierung, Akkumulation und Datenreduktion für die Anzeige."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .ptu import Events


def hist_len_from_code(code: int) -> int:
    """Wie HH500_SetHistoLen: Länge = 1024 * 2**code (code 0..7 → 1024..131072)."""
    return 1024 * (2 ** int(code))


def histogram_t3(ev: Events, num_channels: int, binning: int, nbins: int) -> np.ndarray:
    """T3: dtime-Histogramm je Kanal. Rückgabe (num_channels, nbins), Kanal k in Zeile k-1.

    binning: Zusammenfassung von 2**binning dtime-Einheiten pro Bin (wie HH500_SetBinning relativ
    zur Dateiauflösung). Ereignisse außerhalb des Histogrammbereichs werden verworfen.
    """
    out = np.zeros((num_channels, nbins), np.uint64)
    if len(ev) == 0:
        return out
    ch = ev.channel.astype(np.int64)
    b = (ev.dtime.astype(np.int64) >> binning)
    ok = (ch >= 1) & (ch <= num_channels) & (b < nbins)
    idx = (ch[ok] - 1) * nbins + b[ok]
    out += np.bincount(idx, minlength=num_channels * nbins).reshape(num_channels, nbins).astype(np.uint64)
    return out


def histogram_t2(ev: Events, num_channels: int, binning: int, nbins: int,
                 last_sync: int | None, start_channel: int = 0, first: int = 1
                 ) -> tuple[np.ndarray, int | None, bool]:
    """T2: Start-Stopp-Histogramm (Zeit seit letztem Start-Ereignis) je Kanal.

    start_channel 0 = Sync, sonst ein Detektorkanal (Referenzkanal als Sync-Ersatz; seine eigenen
    Ereignisse sind nur Starts, keine Stopps). Gibt (hist, neuer_letzter_start, hat_start) zurück.
    first = 0: Sync-Ereignisse sind Datenkanal K0 (Zeile 0, Kanal k in Zeile k); sonst Kanal k in Zeile k-1.
    """
    rows = num_channels - first + 1
    out = np.zeros((rows, nbins), np.uint64)
    if len(ev) == 0:
        return out, last_sync, last_sync is not None
    is_sync = ev.channel == start_channel
    sync_t = ev.time[is_sync]
    has_sync = bool(sync_t.size) or last_sync is not None
    if has_sync:
        ph = ~is_sync & (ev.channel >= first)
        t = ev.time[ph]
        ch = ev.channel[ph].astype(np.int64)
        all_sync = sync_t if last_sync is None else np.concatenate([[last_sync], sync_t])
        k = np.searchsorted(all_sync, t, side="right") - 1
        valid = k >= 0
        dt = np.zeros(t.size, np.int64)
        dt[valid] = t[valid] - all_sync[k[valid]]
        b = dt >> binning
        ok = valid & (ch >= first) & (ch <= num_channels) & (b < nbins)
        idx = (ch[ok] - first) * nbins + b[ok]
        out += np.bincount(idx, minlength=rows * nbins).reshape(rows, nbins).astype(np.uint64)
        if all_sync.size:
            last_sync = int(all_sync[-1])
    return out, last_sync, has_sync


CORR_BINS = 2048   # feste Länge des Korrelationshistogramms (symmetrisch, ±1024 Bins)


def correlate(t: np.ndarray, c: np.ndarray, num_channels: int, bw: int, nbins: int,
              tail: tuple[np.ndarray, np.ndarray] | None, ref: int = 1, first: int = 1
              ) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]:
    """Kreuzkorrelation (Zeitdifferenz-Histogramm) τ = t(Kj) − t(Kref) für alle j ≠ ref.

    ``t``: aufsteigend sortierte absolute Zeiten (ganzzahlige Einheiten, T2: Makrozeit-Einheiten,
    T3: ps aus nsync·Syncperiode + dtime·Auflösung), ``c``: Kanäle (1-basiert; 0/andere ignoriert).
    Zeile j-1 enthält die Häufigkeit von τ im Fenster [−W, +W) mit W = nbins/2 · bw. Es werden
    ALLE Paare im Fenster gezählt (Multi-Start/Multi-Stopp, wie bei einer HBT-g²-Messung).
    Frame-übergreifend: ``tail`` enthält die Ereignisse der letzten W des Vorgänger-Frames; ein Paar
    wird genau in dem Frame gezählt, in dem sein späteres Ereignis liegt.
    Rückgabe: (hist, neuer_tail). Zeile ref-first bleibt leer. first = 0: T2-Sync als Kanal K0 (Zeile 0).
    """
    out = np.zeros((num_channels - first + 1, nbins), np.uint64)
    w = (nbins // 2) * bw
    ph = (c >= first) & (c <= num_channels)
    t = np.asarray(t, np.int64)[ph]
    c = np.asarray(c, np.int64)[ph]
    if tail is None:
        tail = (np.zeros(0, np.int64), np.zeros(0, np.int64))
    all_t = np.concatenate([tail[0], t])
    all_c = np.concatenate([tail[1], c])
    is_cur = np.arange(all_t.size) >= tail[0].size
    sel_a = all_c == ref
    a_t, a_cur = all_t[sel_a], is_cur[sel_a]
    if a_t.size:
        for j in range(first, num_channels + 1):
            if j == ref:
                continue
            sel_b = all_c == j
            b_t, b_cur = all_t[sel_b], is_cur[sel_b]
            if not b_t.size:
                continue
            lo = np.searchsorted(a_t, b_t - w, side="right")   # τ = b − a < +W
            hi = np.searchsorted(a_t, b_t + w, side="right")   # τ ≥ −W
            n = hi - lo
            tot = int(n.sum())
            if tot == 0:
                continue
            bi = np.repeat(np.arange(b_t.size), n)
            ai = np.repeat(lo, n) + (np.arange(tot) - np.repeat(np.cumsum(n) - n, n))
            keep = b_cur[bi] | a_cur[ai]
            b = (b_t[bi] - a_t[ai] + w) // bw
            ok = keep & (b >= 0) & (b < nbins)
            out[j - first] += np.bincount(b[ok], minlength=nbins)[:nbins].astype(np.uint64)
    if all_t.size:
        k = np.searchsorted(all_t, all_t[-1] - w, side="left")
        tail = (all_t[k:], all_c[k:])
    return out, tail


def correlate_t2(ev: Events, num_channels: int, binning: int, nbins: int,
                 tail: tuple[np.ndarray, np.ndarray] | None, ref: int = 1):
    """T2-Variante: Zeiten = Makrozeit, Binbreite 2^binning Makrozeit-Einheiten."""
    return correlate(ev.time, ev.channel, num_channels, 1 << binning, nbins, tail, ref)


def t3_abs_time_ps(ev: Events, period_ps: float, res_ps: float) -> tuple[np.ndarray, np.ndarray]:
    """T3: absolute Ankunftszeit in ps = nsync·Syncperiode + dtime·Auflösung (sortiert)."""
    t = np.rint(ev.time.astype(np.float64) * period_ps).astype(np.int64) + \
        np.rint(ev.dtime.astype(np.float64) * res_ps).astype(np.int64)
    o = np.argsort(t, kind="stable")
    return t[o], ev.channel[o]


def counts_per_channel(ev: Events, num_channels: int, first: int = 1) -> np.ndarray:
    rows = num_channels - first + 1
    if len(ev) == 0:
        return np.zeros(rows, np.int64)
    c = ev.channel.astype(np.int64)
    c = c[(c >= first) & (c <= num_channels)]
    return np.bincount(c - first, minlength=rows)[:rows].astype(np.int64)


def rebin(h: np.ndarray, max_points: int) -> tuple[np.ndarray, int]:
    """Summiert benachbarte Bins (ganzzahliger Faktor), bis höchstens max_points übrig sind.

    Arbeitet auf der letzten Achse. Rückgabe (rebinned, faktor).
    """
    n = h.shape[-1]
    f = 1
    while n // f > max_points:
        f *= 2
    if f == 1:
        return h, 1
    m = (n // f) * f
    r = h[..., :m].reshape(*h.shape[:-1], n // f, f).sum(axis=-1)
    return r, f


@dataclass
class Frame:
    """Ergebnis eines Frames einer Datenquelle (Inkremente, nicht akkumuliert)."""
    hist: np.ndarray                 # (num_channels, nbins) Zählungen in diesem Frame
    counts: np.ndarray               # (num_channels,) Ereignisse je Kanal in diesem Frame
    meas_time_s: float               # abgedeckte Messzeit (Datei-/Gerätezeit) des Frames
    bin_width_ps: float              # Breite eines Bins im Histogramm
    sync_count: float = 0.0          # Sync-Ereignisse im Frame (T3: Anzahl Sync-Perioden)
    t_file_s: float = 0.0            # Datei-/Messzeit am Frame-Ende
    progress: float = 0.0            # 0..1 Fortschritt in der Datei
    has_histogram: bool = True       # False z. B. bei T2 ohne Sync
    hist_kind: str = "dtime"         # 'dtime' (T3) oder 'ref' (T2: Zeit seit letztem Ereignis auf dem Referenzkanal)
    corr: np.ndarray | None = None   # (num_channels, CORR_BINS) Kreuzkorrelation zum Referenzkanal
    corr_bin_ps: float = 0.0
    corr_x0_ps: float = 0.0          # linke Kante von Bin 0 (negativ, symmetrisches Fenster)
    wrapped: bool = False            # Datei wurde am Ende neu begonnen (Loop)


@dataclass
class Accumulator:
    """Akkumuliert Frames: 'accumulate' (seit Reset) oder 'sliding' (letzte N Frames)."""
    mode: str = "accumulate"
    window_frames: int = 20
    total: np.ndarray | None = None
    total_time_s: float = 0.0
    frames: deque = field(default_factory=deque)

    def reset(self) -> None:
        self.total = None
        self.total_time_s = 0.0
        self.frames.clear()

    def configure(self, mode: str, window_frames: int) -> None:
        if mode != self.mode or window_frames != self.window_frames:
            self.mode, self.window_frames = mode, window_frames
            self.reset()

    def add(self, fr: Frame) -> np.ndarray:
        return self.add_array(fr.hist, fr.meas_time_s)

    def add_array(self, hist: np.ndarray, meas_time_s: float) -> np.ndarray:
        h = hist.astype(np.uint64)
        if self.total is None or self.total.shape != h.shape:
            self.total = np.zeros_like(h)
            self.total_time_s = 0.0
            self.frames.clear()
        self.total += h
        self.total_time_s += meas_time_s
        if self.mode == "sliding":
            self.frames.append((h, meas_time_s))
            while len(self.frames) > self.window_frames:
                old, t = self.frames.popleft()
                self.total -= old
                self.total_time_s -= t
        return self.total


@dataclass
class RateTracker:
    """Zählraten-Zeitverlauf (Ringpuffer)."""
    maxlen: int = 600
    t: deque = field(default_factory=lambda: deque(maxlen=600))
    rates: deque = field(default_factory=lambda: deque(maxlen=600))

    def reset(self) -> None:
        self.t.clear()
        self.rates.clear()

    def add(self, t_s: float, rates: list[float]) -> None:
        self.t.append(t_s)
        self.rates.append(rates)
