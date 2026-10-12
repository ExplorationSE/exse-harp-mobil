"""SnapiPtuSource: PTU-Wiedergabe und Histogrammierung mit PicoQuants snAPI (File-Device).

Teststand: unter WINDOWS UNGETESTET. Getestet unter Linux x86-64 mit dem offiziellen Wheel
snapi-1.2.5-cp313-cp313-manylinux_2_28_x86_64.whl (github.com/PicoQuant/snAPI, Ordner wheelhouse/),
File-Device mit synthetischen PTU-Dateien (kein echtes Gerät).

Verwendete snAPI-Funktionen (Quelle: snAPI/Main.py und doc_source/*.rst im Repository
PicoQuant/snAPI, eingesehen am 11.10.2026):
  * snAPI.Main.snAPI()                    – Konstruktor
  * snAPI.getFileDevice(path)             – "opening a PTU file ... handled like a real device";
                                            initDevice() ist für File-Devices nicht nötig.
  * snAPI.deviceConfig / snAPI.measDescription (dict)
  * Histogram.setSequenceMode(SequenceMode.Timer, wait4newData=True, param=<Fenster in s>)
                                          – "Each call to getData returns one window";
                                            wait4newData=True "Recommended for file devices".
  * Histogram.measure(acqTime, waitFinished=False, savePTU=False)
                                          – acqTime "will be ignored if device is a FileDevice"
  * Histogram.getData() -> (data[numChans, numBins], bins_ps); ([], []) am Ende

Beobachtungen am File-Device (snAPI 1.2.5, Linux):
  * getData() liefert im Sequence-Modus Fenster-INKREMENTE (Schalter `snapi_windows_cumulative`
    bleibt als Absicherung für andere Versionen).
  * data hat NumChans+1 Zeilen; Zeile 0 (Sync) ist im T3-Modus leer und wird verworfen.
  * deviceConfig["Resolution"] = Dateiauflösung in ps, NumBins = 32768 (15-Bit-dtime).
  * snAPI arbeitet ein File-Device deutlich schneller als Echtzeit ab (20-s-Datei in < 1 s);
    das Echtzeit-Tempo entsteht durch den Frame-Takt des Servers (getData blockiert fensterweise).
  * Ein am Dateiende unvollständiges Fenster wird nicht ausgeliefert (Verlust ≤ 1 Basisfenster).
  * Prozess-Speicherbedarf ≈ 0,9 GB (snAPI-interne Puffer), unabhängig von der Fenstergröße.
  * WICHTIG: stopMeasure() mitten in der Datei und anschließendes Öffnen einer weiteren Datei im
    selben Prozess führte zu Hängern bzw. Speicherfehlern (Segfault). Deshalb läuft snAPI hier
    in einem eigenen Worker-Prozess, der bei Dateiwechsel/„Von vorn“/Loop neu gestartet wird.
    Das isoliert zugleich Abstürze der nativen Bibliothek vom Webserver.

Ein Frame wird aus k Basisfenstern (`snapi_base_window_s`, Standard 50 ms) summiert, damit
Tempo/Frame-Intervall ohne Neustart geändert werden können. „Stopp“ pausiert nur (es wird nicht
weitergelesen); die Position in der Datei bleibt erhalten.
"""
from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import numpy as np

from ..histogram import Frame, hist_len_from_code
from .base import AcqParams, DataSource, SourceError, SourceInfo

CALL_TIMEOUT_S = 60.0


def _worker(conn, cumulative: bool) -> None:  # pragma: no cover - läuft im Kindprozess
    """Kindprozess: besitzt die snAPI-Instanz und führt Befehle sequentiell aus."""
    try:
        from snAPI.Main import snAPI  # type: ignore
        from snAPI.Constants import SequenceMode  # type: ignore
    except Exception as e:
        conn.send(("err", f"snAPI-Import fehlgeschlagen: {e}"))
        return
    sn = snAPI()
    prev = None
    nch = 0
    conn.send(("ok", "ready"))
    while True:
        try:
            cmd, arg = conn.recv()
        except EOFError:
            break
        try:
            if cmd == "quit":
                break
            if cmd == "open":
                if not sn.getFileDevice(arg):
                    conn.send(("err", f"snAPI konnte die Datei nicht öffnen: {arg}"))
                    continue
                cfg = dict(sn.deviceConfig)
                nch = int(cfg.get("NumChans", 4))
                conn.send(("ok", {"cfg": cfg, "md": dict(getattr(sn, "measDescription", {}) or {})}))
            elif cmd == "start":
                h = sn.histogram
                h.setSequenceMode(sequenceMode=SequenceMode.Timer, wait4newData=True, param=float(arg))
                if not h.measure(acqTime=0, waitFinished=False, savePTU=False):
                    conn.send(("err", "snAPI histogram.measure() meldete einen Fehler"))
                    continue
                prev = None
                conn.send(("ok", None))
            elif cmd == "get":
                k, binning, nbins = arg
                acc = None
                got = 0
                bw = 0.0
                while got < k:
                    data, bins = sn.histogram.getData()
                    if len(data) == 0:
                        break
                    a = np.asarray(data, dtype=np.uint64)
                    if a.ndim == 1:
                        a = a.reshape(1, -1)
                    if a.shape[0] == nch + 1:
                        a = a[1:]  # Zeile 0 = Sync
                    if cumulative:
                        inc = a - prev if prev is not None and prev.shape == a.shape else a
                        prev = a.copy()
                        a = inc
                    acc = a.copy() if acc is None else acc + a
                    if len(bins) > 1:
                        bw = float(bins[1] - bins[0])
                    got += 1
                if acc is None:
                    conn.send(("ok", None))
                    continue
                f = 2 ** int(binning)
                n = (acc.shape[1] // f) * f
                acc = acc[:, :n].reshape(acc.shape[0], -1, f).sum(axis=2)
                hist = np.zeros((acc.shape[0], nbins), np.uint64)
                m = min(nbins, acc.shape[1])
                hist[:, :m] = acc[:, :m]
                counts = acc.sum(axis=1).astype(np.int64)
                conn.send(("ok", (hist, counts, got, bw * f)))
            else:
                conn.send(("err", f"unbekannter Befehl {cmd}"))
        except Exception as e:
            conn.send(("err", f"{type(e).__name__}: {e}"))
    try:
        sn.closeDevice()
    except Exception:
        pass


class SnapiPtuSource(DataSource):
    kind = "snapi"

    def __init__(self, snapi_windows_cumulative: bool = False, snapi_base_window_s: float = 0.05,
                 **_: object) -> None:
        self.cumulative = snapi_windows_cumulative
        self.base_window_s = float(snapi_base_window_s)
        self.params = AcqParams()
        self.info: SourceInfo | None = None
        self.path: Path | None = None
        self._proc = None
        self._conn = None
        self._measuring = False
        self._t = 0.0

    # ------------------------------------------------------------ Worker-Prozess
    def _spawn(self) -> None:
        try:
            import snAPI  # noqa: F401  # type: ignore
        except Exception as e:
            raise SourceError(
                "snAPI ist nicht installiert oder nicht lauffähig (pip install snAPI; benötigt die "
                f"PicoQuant-Bibliotheken). Ursprünglicher Fehler: {e}") from e
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self._proc = ctx.Process(target=_worker, args=(child, self.cumulative), daemon=True,
                                 name="snapi-worker")
        self._proc.start()
        self._conn = parent
        self._recv(CALL_TIMEOUT_S)

    def _kill(self) -> None:
        if self._proc is None:
            return
        try:
            self._conn.send(("quit", None))
        except Exception:
            pass
        self._proc.join(3.0)
        if self._proc.is_alive():
            self._proc.kill()
            self._proc.join(2.0)
        self._proc = None
        self._conn = None
        self._measuring = False

    def _recv(self, timeout: float):
        if not self._conn.poll(timeout):
            self._kill()
            raise SourceError("snAPI-Worker antwortet nicht (Zeitüberschreitung)")
        try:
            status, val = self._conn.recv()
        except EOFError:
            self._kill()
            raise SourceError("snAPI-Worker wurde unerwartet beendet (Absturz der nativen Bibliothek?)")
        if status != "ok":
            raise SourceError(str(val))
        return val

    def _call(self, cmd: str, arg=None, timeout: float = CALL_TIMEOUT_S):
        if self._proc is None or not self._proc.is_alive():
            raise SourceError("snAPI-Worker läuft nicht")
        self._conn.send((cmd, arg))
        return self._recv(timeout)

    def _restart_with(self, path: Path) -> dict:
        self._kill()
        self._spawn()
        return self._call("open", str(path))

    # ------------------------------------------------------------ DataSource
    def open(self, path: Path | None) -> SourceInfo:
        if path is None:
            raise SourceError("Keine PTU-Datei angegeben")
        res = self._restart_with(Path(path))
        cfg, md = res["cfg"], res["md"]
        self.path = Path(path)
        mode = {0: "HIST", 2: "T2", 3: "T3"}.get(int(cfg.get("MeasMode", 3)), "?")
        res_ps = float(cfg.get("Resolution", cfg.get("BaseResolution", 1.0)))
        self.info = SourceInfo(
            kind=self.kind, name=self.path.name, mode=mode, num_channels=int(cfg.get("NumChans", 4)),
            resolution_ps=res_ps, sync_rate_hz=float(md.get("AveSyncRate", 0.0) or 0.0),
            duration_s=float(md.get("AcqTime", 0) or 0) / 1000.0, num_records=int(md.get("NumRecs", 0) or 0),
            record_type="snAPI FileDevice", hw_type=str(cfg.get("Model", "")),
            max_binning=15, has_histogram=True,
        )
        self._t = 0.0
        return self.info

    def configure(self, params: AcqParams) -> None:
        self.params = params

    def start(self) -> None:
        if self.info is None:
            raise SourceError("Keine Datei geöffnet")

    def stop(self) -> None:
        pass  # Pause: es wird einfach nicht weitergelesen (wait4newData=True puffert)

    def rewind(self) -> None:
        if self.path is not None:
            self._restart_with(self.path)
        self._t = 0.0

    def read_frame(self, meas_time_s: float) -> Frame | None:
        if self.info is None:
            raise SourceError("Keine Datei geöffnet")
        if not self._measuring:
            self._call("start", self.base_window_s)
            self._measuring = True
        k = max(1, int(round(meas_time_s / self.base_window_s)))
        nbins = hist_len_from_code(self.params.hist_len_code)
        args = (k, int(self.params.binning), nbins)
        res = self._call("get", args)
        wrapped = False
        if res is None:                      # Dateiende
            if not self.params.loop:
                return None
            self.rewind()
            self._call("start", self.base_window_s)
            self._measuring = True
            wrapped = True
            res = self._call("get", args)
            if res is None:
                return None
        hist, counts, got, bw = res
        win = got * self.base_window_s
        self._t += win
        prog = self._t / self.info.duration_s if self.info.duration_s else 0.0
        return Frame(hist=hist, counts=counts, meas_time_s=win,
                     bin_width_ps=bw or self.info.resolution_ps * 2 ** int(self.params.binning),
                     sync_count=self.info.sync_rate_hz * win, t_file_s=self._t,
                     progress=min(1.0, prog), wrapped=wrapped)

    def close(self) -> None:
        self._kill()
