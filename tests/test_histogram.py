import numpy as np
import pytest

from harp.histogram import (Accumulator, Frame, counts_per_channel, hist_len_from_code, histogram_t2,
                            histogram_t3, rebin)
from harp.ptu import Events


def ev(t, c, d=None):
    t = np.asarray(t, np.int64)
    return Events(t, np.asarray(c, np.int16), np.asarray(d if d is not None else np.zeros(t.size), np.int32))


def test_hist_len():
    assert hist_len_from_code(0) == 1024 and hist_len_from_code(7) == 131072


def test_t3_binning_and_range():
    e = ev([0] * 6, [1, 1, 2, 2, 2, 3], [0, 7, 8, 15, 5000, 1])
    h = histogram_t3(e, 4, binning=3, nbins=100)
    assert h.shape == (4, 100)
    assert h[0, 0] == 2           # dtime 0 und 7 → Bin 0
    assert h[1, 1] == 2           # 8 und 15 → Bin 1; 5000>>3=625 außerhalb (verworfen)
    assert h[2, 0] == 1 and h.sum() == 5


def test_t3_ignores_unknown_channels():
    h = histogram_t3(ev([0, 0], [5, 0], [1, 1]), 4, 0, 10)
    assert h.sum() == 0


def test_t3_lifetime_shape(t3_file):
    from harp.ptu import read_all
    hdr, e = read_all(t3_file)
    h = histogram_t3(e, 4, 3, 1024)
    bw_ns = hdr.resolution * 8 * 1e9
    peaks = (np.argmax(h, axis=1) + 0.5) * bw_ns
    assert np.all((peaks > 2.6) & (peaks < 3.6))      # IRF-Lage ≈ 3 ns
    # Abklingzeiten über die Schwerpunkte nach dem Peak: Kanal 4 (4,5 ns) klingt langsamer ab als Kanal 1 (0,6 ns)
    def mean_delay(row):
        p = int(np.argmax(row)); x = np.arange(row.size - p); y = row[p:].astype(float); y -= np.median(row[-50:])
        y = np.clip(y, 0, None); return (x * y).sum() / y.sum() * bw_ns
    taus = [mean_delay(r) for r in h]
    assert taus[0] < taus[1] < taus[2] < taus[3]
    assert taus[0] < 1.2 and taus[3] == pytest.approx(4.5, rel=0.25)  # grobe Schätzung (IRF-Faltung)


def test_t2_start_stop_across_frames():
    e1 = ev([0, 100, 1000], [0, 1, 0])
    h, last, has = histogram_t2(e1, 2, 0, 2000, None)
    assert has and last == 1000 and h[0, 100] == 1
    e2 = ev([1050, 1999], [2, 1])       # kein Sync in diesem Block → Bezug auf letzten Sync (1000)
    h, last, has = histogram_t2(e2, 2, 0, 2000, last)
    assert h[1, 50] == 1 and h[0, 999] == 1


def test_t2_without_sync_has_no_histogram():
    h, last, has = histogram_t2(ev([1, 2, 3], [1, 2, 1]), 2, 0, 100, None)
    assert not has and h.sum() == 0


def test_counts():
    assert counts_per_channel(ev([0, 0, 0], [1, 1, 3]), 4).tolist() == [2, 0, 1, 0]


def test_rebin():
    h = np.arange(16).reshape(2, 8)
    r, f = rebin(h, 4)
    assert f == 2 and r.tolist() == [[1, 5, 9, 13], [17, 21, 25, 29]]
    r, f = rebin(h, 8)
    assert f == 1 and r is h


def _frame(v, t=0.1):
    return Frame(hist=np.full((1, 4), v, np.uint64), counts=np.array([v]), meas_time_s=t, bin_width_ps=1)


def test_accumulate_and_sliding():
    a = Accumulator("accumulate")
    for v in (1, 2, 3):
        tot = a.add(_frame(v))
    assert tot[0, 0] == 6 and a.total_time_s == pytest.approx(0.3)
    s = Accumulator("sliding", window_frames=2)
    for v in (1, 2, 3):
        tot = s.add(_frame(v))
    assert tot[0, 0] == 5 and s.total_time_s == pytest.approx(0.2)
    s.reset()
    assert s.total is None
