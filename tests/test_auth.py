import json
import shutil

import pytest
from fastapi.testclient import TestClient

from harp.auth import ALPHABET, Auth, client_ip, generate_code, is_https, normalize, origin_ok
from harp.config import Config
from harp.server import create_app


def test_generate_code_unambiguous():
    for _ in range(200):
        c = generate_code(10)
        assert len(c) == 10 and set(c) <= set(ALPHABET)
    assert not set("01OIL") & set(ALPHABET)


def test_normalize_and_check():
    a = Auth("K7QM-4XPR9")
    assert normalize(" k7qm 4xpr9 ") == "K7QM4XPR9"
    assert a.check_code("k7qm4xpr9") and a.check_code("K7QM-4XPR9")
    assert not a.check_code("K7QM4XPR8") and not a.check_code("") and not a.check_code(None)
    assert not Auth("").enabled


@pytest.mark.parametrize("origin,host,fwd,ok", [
    ("https://abc.trycloudflare.com", "abc.trycloudflare.com", None, True),
    ("https://ABC.trycloudflare.com", "abc.trycloudflare.com:443", None, True),
    ("https://abc.trycloudflare.com", "127.0.0.1:8765", "abc.trycloudflare.com", True),
    ("http://192.168.1.50:8765", "192.168.1.50:8765", None, True),
    ("http://localhost:8765", "localhost:8765", None, True),
    ("https://evil.example", "abc.trycloudflare.com", None, False),
    ("https://abc.trycloudflare.com.evil.example", "abc.trycloudflare.com", None, False),
    ("null", "abc.trycloudflare.com", None, False),
    (None, "abc.trycloudflare.com", None, True),
])
def test_origin_ok(origin, host, fwd, ok):
    assert origin_ok(origin, host, fwd) is ok


def test_client_ip_trusts_cf_only_from_loopback():
    h = {"cf-connecting-ip": "203.0.113.7"}
    assert client_ip(h, "127.0.0.1") == "203.0.113.7"
    assert client_ip(h, "192.168.1.20") == "192.168.1.20"   # im LAN nicht fälschbar
    assert client_ip({}, "127.0.0.1") == "127.0.0.1"


def test_is_https():
    assert is_https("https", {})
    assert is_https("http", {"x-forwarded-proto": "https"})
    assert is_https("http", {"cf-visitor": '{"scheme":"https"}'})
    assert not is_https("http", {})


def _app(t3_file, tmp_path, token="ABCD234567"):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(t3_file, d / "demo.ptu")
    return create_app(Config(data_dir=d, default_file="demo.ptu", log_dir=tmp_path / "logs", token=token))


def next_of(ws, typ):
    while True:
        m = json.loads(ws.receive_text())
        if m["type"] == typ:
            return m


def test_behind_tunnel_host_and_origin(t3_file, tmp_path):
    """Simuliert cloudflared: Host = trycloudflare-Name, X-Forwarded-Proto https, Origin = https://<Host>."""
    host = "abc.trycloudflare.com"
    tunnel = {"host": host, "x-forwarded-proto": "https", "cf-connecting-ip": "203.0.113.9",
              "cf-visitor": '{"scheme":"https"}'}
    with TestClient(_app(t3_file, tmp_path), base_url=f"http://{host}") as c:
        r = c.get("/", headers=tunnel, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        r = c.post("/login", content="code=abcd234567&next=/", headers={
            **tunnel, "content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)
        assert r.status_code == 303
        sc = r.headers["set-cookie"].lower()
        assert "harp_session=" in sc and "secure" in sc and "httponly" in sc and "samesite=lax" in sc
        cookie = r.cookies.get("harp_session") or sc.split("harp_session=")[1].split(";")[0]
        hdr = {**tunnel, "origin": f"https://{host}", "cookie": f"harp_session={cookie}"}
        with c.websocket_connect("/ws", headers=hdr) as ws:
            assert next_of(ws, "status")["auth"]
            assert len(next_of(ws, "frame")["hist"]) == 4
        with pytest.raises(Exception):
            with c.websocket_connect("/ws", headers={**hdr, "origin": "https://evil.example"}) as ws:
                ws.receive_text()
        with pytest.raises(Exception):   # ohne Cookie
            with c.websocket_connect("/ws", headers={**tunnel, "origin": f"https://{host}"}) as ws:
                ws.receive_text()
