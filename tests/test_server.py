import json
import shutil

import pytest
from fastapi.testclient import TestClient

from harp.config import Config
from harp.server import create_app


@pytest.fixture()
def app(t3_file, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(t3_file, d / "demo.ptu")
    cfg = Config(port=0, data_dir=d, default_file="demo.ptu", log_dir=tmp_path / "logs",
                 defaults={"refresh_ms": 100, "speed": 1.0})
    return create_app(cfg), tmp_path


def next_of(ws, typ):
    while True:
        m = json.loads(ws.receive_text())
        if m["type"] == typ:
            return m


def test_ws_frames_and_commands(app):
    a, tmp = app
    with TestClient(a) as c:
        assert c.get("/api/health").json()["ok"]
        assert "uPlot" in c.get("/static/vendor/uPlot.iife.min.js").text[:200]
        with c.websocket_connect("/ws") as ws:
            st = next_of(ws, "status")
            assert st["running"] and st["file"] == "demo.ptu"
            f = next_of(ws, "frame")
            assert len(f["hist"]) == 4 and f["n_bins"] == 1024 and f["bin_width_ps"] == 32.0
            ws.send_text(json.dumps({"type": "command", "cmd": "set", "params": {"binning": 2}, "id": 1}))
            ack = next_of(ws, "ack")
            assert not ack["ok"]                                   # noch keine Steuerung
            ws.send_text(json.dumps({"type": "command", "cmd": "acquire_control", "id": 2}))
            assert next_of(ws, "ack")["ok"]
            ws.send_text(json.dumps({"type": "command", "cmd": "set",
                                     "params": {"binning": 2, "channels": [2], "display_points": 256}, "id": 3}))
            assert next_of(ws, "ack")["ok"]
            for _ in range(20):
                f = next_of(ws, "frame")
                if f["channels"] == [2]:
                    break
            assert f["channels"] == [2] and f["n_bins"] == 256 and f["bin_width_ps"] == 16.0 * 4
    log = (tmp / "logs" / "commands.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"cmd": "set"' in line and '"ok": true' in line for line in log)


def test_token_required(t3_file, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(t3_file, d / "demo.ptu")
    cfg = Config(data_dir=d, default_file="demo.ptu", log_dir=tmp_path / "logs", token="geheim", autostart=False)
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status?token=geheim").status_code == 200
        with c.websocket_connect("/ws?token=geheim") as ws:
            assert next_of(ws, "status")["auth"]
        with pytest.raises(Exception):
            with c.websocket_connect("/ws") as ws:
                ws.receive_text()


def test_login_flow(t3_file, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(t3_file, d / "demo.ptu")
    cfg = Config(data_dir=d, default_file="demo.ptu", log_dir=tmp_path / "logs", token="AB3D-EF7H", autostart=True)
    with TestClient(create_app(cfg)) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.get("/static/app.js", follow_redirects=False).status_code == 303
        assert "Zugangscode" in c.get("/login").text
        r = c.post("/login", content="code=falsch&next=/", headers={"content-type": "application/x-www-form-urlencoded"},
                   follow_redirects=False)
        assert r.status_code == 401
        with pytest.raises(Exception):
            with c.websocket_connect("/ws") as ws:
                ws.receive_text()
        r = c.post("/login", content="code=ab3def7h&next=/", headers={"content-type": "application/x-www-form-urlencoded"},
                   follow_redirects=False)       # Groß-/Kleinschreibung und Bindestrich egal
        assert r.status_code == 303 and "harp_session" in r.headers["set-cookie"]
        assert c.get("/").status_code == 200       # Cookie im Client gespeichert
        with c.websocket_connect("/ws") as ws:
            assert next_of(ws, "status")["auth"]
            assert len(next_of(ws, "frame")["hist"]) == 4
        with pytest.raises(Exception):           # fremde Origin wird abgewiesen
            with c.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_text()


def test_url_token_sets_cookie_and_bruteforce_limit(t3_file, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(t3_file, d / "demo.ptu")
    cfg = Config(data_dir=d, default_file="demo.ptu", log_dir=tmp_path / "logs", token="XYZ2345678", autostart=False)
    with TestClient(create_app(cfg)) as c:
        r = c.get("/?token=xyz2345678", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/" and "harp_session" in r.headers["set-cookie"]
    with TestClient(create_app(cfg)) as c2:
        for _ in range(8):
            c2.get("/api/status?token=wrong")
        assert c2.get("/api/status?token=XYZ2345678").status_code == 401   # IP gesperrt


def test_version_single_source(tmp_path):
    import harp
    from harp.server import create_app as _ca
    from harp.config import Config as _C
    from fastapi.testclient import TestClient as _TC
    assert harp.__version__ == "1.0.5"
    with _TC(_ca(_C(data_dir=tmp_path, log_dir=tmp_path / "logs"))) as c:
        r = c.get("/api/health").json()
    assert r == {"ok": True, "name": "ExSE Harp Mobil", "version": harp.__version__}
