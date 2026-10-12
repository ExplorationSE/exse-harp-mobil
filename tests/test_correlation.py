"""T2-Kreuzkorrelation (HBT / Zeitdifferenz-Histogramm)."""
import json
import shutil

import numpy as np
from fastapi.testclient import TestClient

from harp.config import Config
from harp.histogram import correlate_t2
from harp.ptu import Events
from harp.server import create_app
from harp.sources.base import AcqParams
from harp.sources.pure_ptu import PurePythonPtuSource


def ev(t, c):
    t = np.asarray(t, np.int64)
    return Events(time=t, channel=np.asarray(c, np.int64), dtime=np.zeros(t.size, np.int64))


def brute(t, c, nbins, bw):
    w = nbins // 2 * bw
    out = np.zeros(nbins, np.int64)
    a = t[c == 1]
    for b in t[c == 2]:
        d = b - a
        d = d[(d >= -w) & (d < w)]
        np.add.at(out, (d + w) // bw, 1)
    return out


def test_known_delays():
    # K1 bei 1000, K2 bei 1300 (+300) und 900 (−100)
    h, _ = correlate_t2(ev([900, 1000, 1300], [2, 1, 2]), 2, 0, 1024, None)
    nz = {int(i) - 512: int(v) for i, v in enumerate(h[1]) if v}
    assert nz == {300: 1, -100: 1}
    assert h[0].sum() == 0          # Referenzkanal ohne eigene Kurve


def test_matches_bruteforce_and_frame_split():
    rng = np.random.default_rng(0)
    n = 4000
    t = np.sort(rng.integers(0, 2_000_000, n))
    c = rng.integers(1, 3, n)
    ref = brute(t, c, 512, 8)
    h_all, _ = correlate_t2(ev(t, c), 2, 3, 512, None)
    assert np.array_equal(h_all[1].astype(np.int64), ref)
    # in viele Frames zerlegt → identisches Ergebnis (Paare über Frame-Grenzen genau einmal)
    tail, acc = None, np.zeros(512, np.int64)
    for chunk in np.array_split(np.arange(n), 37):
        h, tail = correlate_t2(ev(t[chunk], c[chunk]), 2, 3, 512, tail)
        acc += h[1].astype(np.int64)
    assert np.array_equal(acc, ref)


def test_t3_absolute_time_correlation():
    from harp.histogram import correlate, t3_abs_time_ps
    # Sync-Periode 25 ns, Auflösung 4 ps. K2 kommt 500 Bins (2 ns) nach K1 im selben Sync,
    # einmal 2 Syncs später (τ = 50 ns + 0) → Peaks bei 2 ns und 50 ns.
    e = Events(time=np.array([10, 10, 20, 22], np.int64), channel=np.array([1, 2, 1, 2], np.int64),
               dtime=np.array([100, 600, 300, 300], np.int64))
    t, c = t3_abs_time_ps(e, 25_000.0, 4.0)
    assert list(t) == [250_400, 252_400, 501_200, 551_200]
    h, _ = correlate(t, c, 2, 128, 2048, None, ref=1)
    tau = {(int(i) - 1024) * 128 for i in np.nonzero(h[1])[0]}
    # Paare: (K1@250400,K2@252400)=+2000; (K1@501200,K2@551200)=+50000; (K1@501200,K2@252400)=−248800 (außerhalb ±131 ns)
    assert tau == {(2000 // 128) * 128, (50000 // 128) * 128}


def test_pure_source_t3_corr(t3_file):
    s = PurePythonPtuSource()
    s.open(t3_file)
    s.configure(AcqParams(loop=False, ref_channel=1, corr_binning=7))
    tot = None
    while (fr := s.read_frame(0.1)) is not None:
        assert fr.corr.shape == (4, 2048) and fr.corr_bin_ps == 128 and fr.corr_x0_ps == -1024 * 128
        tot = fr.corr if tot is None else tot + fr.corr
    assert tot[0].sum() == 0 and tot[2].sum() > 0          # Referenz ohne Kurve
    # unabhängige Kanäle, gepulst mit 40 MHz → Peaks im Abstand der Sync-Periode (25 ns)
    x = (-1024 * 128 + (np.arange(2048) + 0.5) * 128) / 1000.0
    prof = tot[2].astype(float)
    pk = [prof[(x > 25 * k - 3) & (x < 25 * k + 8)].sum() for k in range(-4, 5)]
    mid = [prof[(x > 25 * k + 12) & (x < 25 * k + 18)].sum() for k in range(-4, 4)]
    assert min(pk) > 5 * max(mid)


def test_hbt_antibunching(hbt_file):
    s = PurePythonPtuSource()
    info = s.open(hbt_file)
    assert info.mode == "T2" and info.num_channels == 2 and info.sync_rate_hz == 10e6
    s.configure(AcqParams(loop=False, ref_channel=1, corr_binning=9))
    tot = None
    while (fr := s.read_frame(0.5)) is not None:
        assert fr.hist_kind == "ref" and fr.has_histogram and fr.hist[0].sum() == 0   # Start = K1
        tot = fr.corr if tot is None else tot + fr.corr
    x = (-1024 * 512 + (np.arange(2048) + 0.5) * 512) / 1000.0   # ns
    area = lambda c: tot[1][(x > c - 15) & (x < c + 15)].sum()   # noqa: E731
    side = np.mean([area(k * 100 + 1.2) for k in (-4, -3, 3, 4)])
    g0 = area(1.2) / side
    assert g0 < 0.5, g0                         # Antibunching
    assert area(101.2) > 1.05 * area(401.2)     # Bunching-Schulter durch Blinken


def test_server_views(hbt_file, t3_file, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(hbt_file, d / "hbt.ptu")
    shutil.copy(t3_file, d / "t3.ptu")
    app = create_app(Config(data_dir=d, default_file="hbt.ptu", log_dir=tmp_path / "logs"))

    def until(ws, pred):
        while True:
            m = json.loads(ws.receive_text())
            if pred(m):
                return m

    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        st = until(ws, lambda m: m["type"] == "status")
        assert st["settings"]["corr_binning"] == 9 and st["settings"]["ref_channel"] == 1
        m = until(ws, lambda m: m["type"] == "frame")
        assert m["has_histogram"] is True and m["hist_kind"] == "ref" and m["channels"] == [2]
        assert m["corr_channels"] == [2] and m["corr_ref"] == 1 and m["corr_x0_ps"] < 0
        ws.send_text(json.dumps({"type": "command", "cmd": "acquire_control", "id": 0}))
        ws.send_text(json.dumps({"type": "command", "cmd": "set", "params": {"ref_channel": 2}, "id": 1}))
        m = until(ws, lambda m: m["type"] == "frame" and m.get("corr_ref") == 2)
        assert m["corr_channels"] == [1] and m["channels"] == [1]
        ws.send_text(json.dumps({"type": "command", "cmd": "set", "params": {"ref_channel": 3}, "id": 2}))
        a = until(ws, lambda m: m["type"] == "ack" and m.get("id") == 2)
        assert a["ok"] is False
        ws.send_text(json.dumps({"type": "command", "cmd": "open_file", "params": {"name": "t3.ptu"}, "id": 3}))
        st = until(ws, lambda m: m["type"] == "status" and m.get("file") == "t3.ptu")
        assert st["settings"]["binning"] == 3 and st["settings"]["corr_binning"] == 7
        ws.send_text(json.dumps({"type": "command", "cmd": "start", "id": 4}))
        m = until(ws, lambda m: m["type"] == "frame" and m["hist_kind"] == "dtime")
        assert m["has_histogram"] and m["corr_channels"] == [2, 3, 4] and m["corr_bin_ps"] >= 128


def test_file_list_dedupe(t3_file, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(t3_file, d / "a.ptu")
    shutil.copy(t3_file, d / "B.PTU")
    (d / "notes.txt").write_text("x")
    app = create_app(Config(data_dir=d, default_file="a.ptu", log_dir=tmp_path / "logs"))
    with TestClient(app) as c:
        names = [f["name"] for f in c.get("/api/files").json()]
    assert names == ["a.ptu", "B.PTU"]


def test_t2_reference_start_histogram():
    """T2: Zeit seit dem letzten Ereignis auf dem Referenzkanal, über Frame-Grenzen hinweg."""
    from harp.histogram import histogram_t2
    def ev(t, c):
        return Events(time=np.array(t, np.int64), channel=np.array(c, np.int8),
                      dtime=np.zeros(len(t), np.int64))
    # Sync (0) wird ignoriert, K1 = Start; Stopps auf K2/K3
    h, last, ok = histogram_t2(ev([5, 10, 13, 14, 20, 27], [0, 1, 2, 3, 1, 2]), 3, 0, 16, None, 1)
    assert ok and last == 20 and h[0].sum() == 0
    assert list(np.nonzero(h[1])[0]) == [3, 7] and list(np.nonzero(h[2])[0]) == [4]
    # nächster Frame: Start aus dem vorigen Frame gilt weiter; Ereignis vor jedem Start fällt weg
    h2, last2, _ = histogram_t2(ev([30, 35], [2, 1]), 3, 1, 16, last, 1)   # Binbreite 2
    assert last2 == 35 and h2[1][5] == 1 and h2.sum() == 1
    h3, _, _ = histogram_t2(ev([3, 8], [2, 1]), 3, 0, 16, None, 1)
    assert h3.sum() == 0
