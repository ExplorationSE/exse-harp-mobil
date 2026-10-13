"""Zwei Clients gleichzeitig, einer hängt (Handy im Hintergrund/Funkloch)."""
import asyncio
import json
import tempfile
import time
from pathlib import Path

from harp.config import Config
from harp.server import Client, HarpService


class HangingWS:
    """send_text kehrt nie zurück; close() wird protokolliert."""
    def __init__(self):
        self.closed_code = None

    async def send_text(self, txt):
        await asyncio.sleep(3600)

    async def close(self, code=1000):
        self.closed_code = code


class OkWS:
    def __init__(self):
        self.msgs = []
        self.closed_code = None

    async def send_text(self, txt):
        self.msgs.append(json.loads(txt))

    async def close(self, code=1000):
        self.closed_code = code

    def types(self):
        return [m.get("type") for m in self.msgs]


def make(svc, ws, timeout=0.3):
    c = Client(ws, "test", on_dead=svc.remove_client, send_timeout=timeout)
    svc.clients[c.id] = c
    c.start()
    return c


def run(coro):
    return asyncio.run(coro)


def test_haengender_client_blockiert_andere_nicht_und_wird_geschlossen():
    async def main():
        svc = HarpService(Config(log_dir=Path(tempfile.mkdtemp())))
        svc.build_frame = lambda *a: json.dumps({"type": "frame"})
        hang, ok = HangingWS(), OkWS()
        a, b = make(svc, hang), make(svc, ok)
        assert svc.lock.acquire(a.id)                 # hängendes Handy hatte die Steuerung
        t0 = time.monotonic()
        for _ in range(5):
            await svc.broadcast_frame(None, None, None, 0, 0)
            await svc.broadcast_status()              # darf nicht auf das hängende Handy warten
            await asyncio.sleep(0.02)
        assert time.monotonic() - t0 < 0.3
        await asyncio.sleep(0.5)                      # Sende-Timeout des hängenden Clients läuft ab
        assert hang.closed_code == 1011               # Verbindung aktiv geschlossen → Handy verbindet neu
        assert a.id not in svc.clients and a.closed
        assert svc.lock.holder is None                # Steuerung freigegeben
        n = ok.types().count("frame")
        for _ in range(3):
            await svc.broadcast_frame(None, None, None, 0, 0)
            await asyncio.sleep(0.02)
        assert ok.types().count("frame") > n >= 1     # anderer Client läuft weiter
        st = [m for m in ok.msgs if m.get("type") == "status"][-1]
        assert st["clients"] == 1 and st["control"]["held"] is False
        assert b.id in svc.clients and ok.closed_code is None
        svc.remove_client(b)
    run(main())


def test_latest_only_und_reihenfolge():
    async def main():
        svc = HarpService(Config(log_dir=Path(tempfile.mkdtemp())))
        ok = OkWS()
        c = Client(ok, "test", on_dead=svc.remove_client)   # noch nicht gestartet: alles wird gepuffert
        svc.clients[c.id] = c
        for i in range(10):
            c.post_frame(json.dumps({"type": "frame", "i": i}))
        c.post_status(json.dumps({"type": "status", "n": 1}))
        c.post_status(json.dumps({"type": "status", "n": 2}))
        c.post(json.dumps({"type": "ack"}))
        c.start()
        await asyncio.sleep(0.05)
        assert ok.types() == ["ack", "status", "frame"]   # Quittung vor Status vor Frame
        assert ok.msgs[1]["n"] == 2 and ok.msgs[2]["i"] == 9 and c.dropped == 9
        svc.remove_client(c)
    run(main())


def test_zwei_websocket_clients_ende_zu_ende(tmp_path):
    """Echter Server (TestClient): zwei Verbindungen, Steuerung bei einer, Ping/Pong bei beiden."""
    from fastapi.testclient import TestClient
    from harp.server import create_app
    app = create_app(Config(port=0, data_dir=tmp_path, log_dir=tmp_path / "logs", autostart=False))
    with TestClient(app) as tc, tc.websocket_connect("/ws") as w1, tc.websocket_connect("/ws") as w2:
        assert w1.receive_json()["type"] == "status"
        assert w2.receive_json()["type"] == "status"
        w1.send_text(json.dumps({"type": "command", "cmd": "acquire_control", "id": 1}))
        def until(ws, pred):
            for _ in range(50):
                m = ws.receive_json()
                if pred(m):
                    return m
            raise AssertionError("Nachricht nicht erhalten")
        assert until(w1, lambda m: m["type"] == "ack")["ok"] is True
        st2 = until(w2, lambda m: m["type"] == "status" and m["control"]["held"])
        assert st2["control"]["mine"] is False and st2["clients"] == 2   # w2 = Zuschauer
        w2.send_text(json.dumps({"type": "command", "cmd": "stop", "id": 2}))
        assert until(w2, lambda m: m["type"] == "ack")["ok"] is False
        for w in (w1, w2):
            w.send_text(json.dumps({"type": "ping", "t": 7}))
            assert until(w, lambda m: m["type"] == "pong")["t"] == 7
