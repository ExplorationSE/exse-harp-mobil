import numpy as np
import pytest

from harp.ptu import read_all
from harp.histogram import histogram_t3
from harp.sources import AcqParams, SourceError, create_source


def test_pure_source_replays_whole_file(t3_file):
    src = create_source("pure")
    info = src.open(t3_file)
    assert info.mode == "T3" and info.num_channels == 4 and info.resolution_ps == pytest.approx(4.0)
    src.configure(AcqParams(binning=3, hist_len_code=0, loop=False))
    total, frames, t = None, 0, 0.0
    while (fr := src.read_frame(0.1)) is not None:
        total = fr.hist if total is None else total + fr.hist
        frames += 1
        t += fr.meas_time_s
        assert fr.bin_width_ps == pytest.approx(32.0)
    _, ev = read_all(t3_file)
    assert np.array_equal(total, histogram_t3(ev, 4, 3, 1024))   # nichts verloren/doppelt
    assert 9 <= frames <= 11 and t == pytest.approx(info.duration_s, abs=0.11)


def test_pure_source_frame_rates(t3_file):
    src = create_source("pure")
    src.open(t3_file)
    src.configure(AcqParams(loop=True))
    fr = src.read_frame(0.2)
    rates = fr.counts / fr.meas_time_s
    assert fr.sync_count / fr.meas_time_s == pytest.approx(40e6, rel=1e-6)
    assert np.all(rates > 5e3) and np.all(rates < 2e5)


def test_pure_source_loops(t3_file):
    src = create_source("pure")
    src.open(t3_file)
    src.configure(AcqParams(loop=True))
    wrapped = False
    for _ in range(8):
        fr = src.read_frame(0.3)
        wrapped |= fr.wrapped
    assert wrapped


def test_pure_source_t2(t2_file):
    src = create_source("pure")
    info = src.open(t2_file)
    assert info.mode == "T2"
    src.configure(AcqParams(binning=4, hist_len_code=1))
    fr = src.read_frame(0.05)
    assert fr.has_histogram and fr.hist.sum() > 0
    assert fr.sync_count / fr.meas_time_s == pytest.approx(1e6, rel=0.01)
    assert fr.hist_kind == "ref" and fr.hist[0].sum() == 0   # K1 = Start (Referenz), keine eigene Kurve
    assert fr.hist[1:].sum() > 0


def test_snapi_source_reports_missing_snapi(t3_file):
    src = create_source("snapi")
    try:
        import snAPI  # noqa: F401
        pytest.skip("snAPI vorhanden – dieser Test prüft nur die Fehlermeldung ohne snAPI")
    except ImportError:
        pass
    with pytest.raises(SourceError, match="snAPI"):
        src.open(t3_file)


def test_hh500_stub():
    with pytest.raises(SourceError, match="Platzhalter"):
        create_source("hh500").open(None)


def test_unknown_source():
    with pytest.raises(SourceError):
        create_source("foo")


def test_snapi_file_device_matches_pure(t3_file):
    """Nur wenn snAPI installiert ist (z. B. Linux-Wheel snapi 1.2.5 oder Windows)."""
    pytest.importorskip("snAPI.Main")
    src = create_source("snapi", snapi_base_window_s=0.05)
    info = src.open(t3_file)
    assert info.mode == "T3" and info.num_channels == 4 and info.resolution_ps == pytest.approx(4.0)
    src.configure(AcqParams(binning=3, hist_len_code=0, loop=False))
    src.start()
    total = None
    while (fr := src.read_frame(0.2)) is not None:
        total = fr.hist.astype(np.int64) if total is None else total + fr.hist
    src.close()
    _, ev = read_all(t3_file)
    ref = histogram_t3(ev, 4, 3, 1024).astype(np.int64)
    # snAPI liefert das letzte, unvollständige Fenster nicht aus → höchstens ~1 Fenster (50 ms) Verlust
    assert np.all(total.sum(axis=1) <= ref.sum(axis=1))
    assert np.all(total.sum(axis=1) >= 0.9 * ref.sum(axis=1))
    # gleiche Form: normierte kumulative Verteilungen (Kolmogorov-Abstand) nahezu identisch
    cdf = lambda h: np.cumsum(h, axis=1) / h.sum(axis=1, keepdims=True)
    assert np.abs(cdf(total) - cdf(ref)).max() < 0.02
