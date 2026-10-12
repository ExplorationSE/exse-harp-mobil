import time

import pytest

from harp.control import CommandError, ControlLock, Settings, safe_file, validate_set


def test_defaults_valid():
    s = Settings()
    assert s.refresh_ms == 150 and s.frame_meas_time_s == pytest.approx(0.15)


@pytest.mark.parametrize("params", [
    {"refresh_ms": 99}, {"refresh_ms": 2001}, {"speed": 0}, {"speed": 101}, {"binning": -1},
    {"binning": 16}, {"hist_len_code": 8}, {"channels": []}, {"channels": [1, 1]}, {"channels": [0]},
    {"mode": "foo"}, {"window_frames": 1}, {"display_points": 10}, {"unknown": 1}, {"refresh_ms": "abc"},
])
def test_invalid_rejected(params):
    with pytest.raises(CommandError):
        validate_set(Settings(), params, num_channels=4, max_binning=15)


def test_channel_beyond_source():
    with pytest.raises(CommandError, match="nicht vorhanden"):
        validate_set(Settings(), {"channels": [1, 5]}, num_channels=4, max_binning=15)


def test_valid_set_merges():
    s = validate_set(Settings(), {"binning": 5, "channels": [3, 1], "speed": 2.5}, 4, 15)
    assert s.binning == 5 and s.channels == [1, 3] and s.speed == 2.5 and s.refresh_ms == 150
    assert s.frame_meas_time_s == pytest.approx(0.375)


def test_empty_params():
    with pytest.raises(CommandError):
        validate_set(Settings(), {}, 4, 15)


def test_safe_file(tmp_path):
    (tmp_path / "a.ptu").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    assert safe_file(tmp_path, "a.ptu").name == "a.ptu"
    for bad in ["../a.ptu", "b.txt", "nope.ptu", "/etc/passwd", ".hidden.ptu", "sub\\a.ptu"]:
        with pytest.raises(CommandError):
            safe_file(tmp_path, bad)


def test_lock():
    lk = ControlLock(timeout_s=0.2)
    assert lk.acquire("a") and lk.check("a")
    assert not lk.acquire("b") and not lk.check("b")
    lk.release("b")              # fremde Freigabe wirkungslos
    assert lk.holder == "a"
    time.sleep(0.25)             # Inaktivität → Sperre verfällt
    assert lk.acquire("b") and not lk.check("a")
    lk.release("b")
    assert lk.holder is None
