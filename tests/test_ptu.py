import struct

import numpy as np
import pytest

from harp import ptu


def test_header_t3(t3_file):
    h = ptu.read_header(t3_file)
    assert h.version == "1.0.00"
    assert h.record_type == ptu.RT_GENERIC_T3 and h.mode == "T3"
    assert h.global_resolution == pytest.approx(25e-9)
    assert h.resolution == pytest.approx(4e-12)
    assert h.tags["HW_Type"] == "HydraHarp 500"
    assert h.tags["Measurement_Mode"] == 3
    assert h.num_records == (t3_file.stat().st_size - h.data_offset) // 4
    assert h.tags["File_CreatingTime"].startswith("20")


def test_bad_magic(tmp_path):
    p = tmp_path / "x.ptu"
    p.write_bytes(b"NOTAPTU\0" + b"\0" * 64)
    with pytest.raises(ptu.PtuError):
        ptu.read_header(p)


def test_t3_roundtrip_with_overflows():
    nsync = np.array([5, 1023, 1024, 5000, 5000, 3_000_000, 3_000_001], np.int64)
    ch = np.array([1, 2, 3, 4, 1, 8, 2])
    dt = np.array([0, 100, 32767, 5, 6, 7, 8])
    rec = ptu.encode_t3_generic(nsync, ch, dt)
    ofl = ((rec >> 25) & 0x3F == 0x3F) & (rec >> 31 == 1)
    assert ofl.sum() >= 2                         # mehrere Overflow-Records
    ev = ptu.StreamDecoder(ptu.RT_GENERIC_T3).decode(rec)
    assert ev.time.tolist() == nsync.tolist()
    assert ev.channel.tolist() == ch.tolist()
    assert ev.dtime.tolist() == dt.tolist()


def test_t3_chunked_equals_full():
    rng = np.random.default_rng(0)
    nsync = np.sort(rng.integers(0, 10_000_000, 5000))
    rec = ptu.encode_t3_generic(nsync, rng.integers(1, 5, 5000), rng.integers(0, 6000, 5000))
    full = ptu.StreamDecoder(ptu.RT_GENERIC_T3).decode(rec)
    d = ptu.StreamDecoder(ptu.RT_GENERIC_T3)
    parts = [d.decode(rec[i:i + 333]) for i in range(0, rec.size, 333)]
    joined = ptu.Events.concat(parts)
    assert np.array_equal(full.time, joined.time) and np.array_equal(full.time, nsync)


def test_hydraharp_v1_overflow_always_one():
    # HydraHarp V1: Overflow-Record bedeutet immer genau einen Überlauf, unabhängig vom nsync-Feld
    rec = np.array([(1 << 31) | (63 << 25) | 5, (0 << 25) | (10 << 10) | 3], np.uint32)
    ev = ptu.StreamDecoder(ptu.RT_HYDRAHARP_T3).decode(rec)
    assert ev.time.tolist() == [1024 + 3]
    ev2 = ptu.StreamDecoder(ptu.RT_HYDRAHARP2_T3).decode(rec)
    assert ev2.time.tolist() == [5 * 1024 + 3]


def test_marker_records_are_not_photons():
    rec = np.array([(1 << 31) | (2 << 25) | 7, (1 << 25) | (3 << 10) | 9], np.uint32)
    ev = ptu.StreamDecoder(ptu.RT_GENERIC_T3).decode(rec)
    assert len(ev) == 1 and ev.markers == 1 and ev.channel[0] == 2


def test_t2_roundtrip_sync_and_channels():
    t = np.array([0, 10, 500, 40_000_000, 40_000_100, 100_000_000], np.int64)
    c = np.array([0, 1, 2, 0, 4, 3])
    rec = ptu.encode_t2_generic(t, c)
    ev = ptu.StreamDecoder(ptu.RT_GENERIC_T2).decode(rec)
    assert ev.time.tolist() == t.tolist()
    assert ev.channel.tolist() == c.tolist()


def test_picoharp_t3():
    # PicoHarp 300 T3: ch(4) dtime(12) nsync(16); ch 15 + dtime 0 = Overflow (65536)
    rec = np.array([(1 << 28) | (100 << 16) | 50, (15 << 28) | 0, (2 << 28) | (7 << 16) | 1], np.uint32)
    ev = ptu.StreamDecoder(ptu.RT_PICOHARP300_T3).decode(rec)
    assert ev.time.tolist() == [50, 65536 + 1]
    assert ev.channel.tolist() == [1, 2]
    assert ev.dtime.tolist() == [100, 7]


def test_file_against_ptufile(t3_file):
    ptufile = pytest.importorskip("ptufile")
    _, ev = ptu.read_all(t3_file)
    with ptufile.PtuFile(t3_file) as f:
        r = f.decode_records()
    r = r[r["dtime"] >= 0]
    assert np.array_equal(r["time"].astype(np.int64), ev.time)
    assert np.array_equal(r["channel"].astype(np.int64) + 1, ev.channel.astype(np.int64))
    assert np.array_equal(r["dtime"].astype(np.int64), ev.dtime.astype(np.int64))


def test_wide_string_and_float_array(tmp_path):
    p = tmp_path / "w.ptu"
    tags = ptu.standard_tags(record_type=ptu.RT_GENERIC_T3, num_records=0, global_res_s=25e-9, res_s=1e-12,
                             mode=3, num_channels=4, acq_time_ms=1, sync_rate=4e7)
    tags.append(("Test_Wide", -1, "Grüße", ptu.TY_WIDESTRING))
    ptu.write_ptu(p, tags, np.zeros(0, np.uint32))
    h = ptu.read_header(p)
    assert h.tags["Test_Wide"] == "Grüße"
