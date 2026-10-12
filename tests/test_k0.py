"""T2: Sync-Eingang als Datenkanal K0 (Herald-Datei, Laser-Sync-Datei, Datei ohne Sync)."""
import json
import shutil

import numpy as np
import pytest
from fastapi.testclient import TestClient

from harp.config import Config
from harp.control import CommandError, Settings, validate_set
from harp.server import create_app
from harp.sources.base import AcqParams
from harp.sources.pure_ptu import PurePythonPtuSource


def test_k0_detection(heralded_file, t2_file, hbt_file, t3_file):
    s = PurePythonPtuSource()
    i = s.open(heralded_file)
    assert i.first_channel == 0 and not i.k0_periodic and i.num_channels == 2
    i = s.open(t2_file)                         # Laser-Sync aufgezeichnet → K0, regelmäßig
    assert i.first_channel == 0 and i.k0_periodic
    assert s.open(hbt_file).first_channel == 1  # Sync nicht aufgezeichnet → kein K0
    assert s.open(t3_file).first_channel == 1   # T3: Sync bleibt Sync


def test_k0_source_rows_and_peaks(heralded_file):
    s = PurePythonPtuSource()
    s.open(heralded_file)
    s.configure(AcqParams(binning=6, hist_len_code=0, ref_channel=0, corr_binning=6, loop=False))
    h = c = None
    cnt = np.zeros(3)
    while (fr := s.read_frame(0.5)) is not None:
        assert fr.hist.shape[0] == 3 and fr.corr.shape[0] == 3 and fr.counts.shape == (3,)
        h = fr.hist if h is None else h + fr.hist
        c = fr.corr if c is None else c + fr.corr
        cnt += fr.counts
    assert h[0].sum() == 0 and c[0].sum() == 0          # Referenz K0 ohne eigene Kurve
    rates = cnt / 3.0
    assert 50e3 < rates[0] < 100e3 and 7e3 < rates[1] < 13e3 and 7e3 < rates[2] < 13e3
    bw = 64 / 1000                                      # ns
    for row, d in ((1, 12.0), (2, 15.0)):
        assert abs((np.argmax(h[row]) + 0.5) * bw - d) < 0.2                  # t nach K0
        assert abs(-1024 * bw + (np.argmax(c[row]) + 0.5) * bw - d) < 0.2     # τ = t(Kn) − t(K0)
        assert c[row].max() > 50 * np.median(c[row]) + 50


def test_validate_k0():
    assert validate_set(Settings(), {"ref_channel": 0, "channels": [0, 1, 2]}, 2, 20, 0).ref_channel == 0
    with pytest.raises(CommandError):
        validate_set(Settings(), {"ref_channel": 0}, 2, 20, 1)
    with pytest.raises(CommandError):
        validate_set(Settings(), {"channels": [0, 1]}, 2, 20, 1)


def test_server_k0_defaults(heralded_file, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(heralded_file, d / "her.ptu")
    app = create_app(Config(data_dir=d, default_file="her.ptu", log_dir=tmp_path / "logs"))

    def until(ws, pred):
        while True:
            m = json.loads(ws.receive_text())
            if pred(m):
                return m

    with TestClient(app) as cl, cl.websocket_connect("/ws") as ws:
        st = until(ws, lambda m: m["type"] == "status")
        s = st["settings"]
        assert s["ref_channel"] == 0 and s["channels"] == [0, 1, 2]
        assert s["binning"] == 6 and s["corr_binning"] == 6            # 64 ps: 0…65 ns bzw. ±65 ns
        assert st["info"]["first_channel"] == 0
        m = until(ws, lambda m: m["type"] == "frame")
        assert m["first_channel"] == 0 and len(m["rates"]) == 3
        assert m["channels"] == [1, 2] and m["corr_channels"] == [1, 2] and m["corr_ref"] == 0
        ws.send_text(json.dumps({"type": "command", "cmd": "acquire_control", "id": 0}))
        ws.send_text(json.dumps({"type": "command", "cmd": "set", "params": {"ref_channel": 1}, "id": 1}))
        m = until(ws, lambda m: m["type"] == "frame" and m.get("corr_ref") == 1)
        assert m["corr_channels"] == [0, 2] and m["channels"] == [0, 2]
