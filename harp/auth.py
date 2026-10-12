"""Einfacher Zugriffsschutz per Zugangscode (für den temporären Online-Betrieb).

* Code-Eingabe-Seite /login (POST setzt ein HttpOnly-Session-Cookie), alternativ ?token=<Code> in der URL.
* Geschützt: Web-Seite, statische Dateien, /api/* und der WebSocket /ws.
* Code-Vergleich in konstanter Zeit, unabhängig von Groß-/Kleinschreibung, Leer- und Bindestrichen.
* Fehlversuche werden je Client-IP (bei Cloudflare: CF-Connecting-IP) und global begrenzt.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import time
from collections import defaultdict, deque

COOKIE = "harp_session"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # ohne 0/O, 1/I/L
MAX_FAIL_PER_IP = 8          # je 10 Minuten
MAX_FAIL_GLOBAL = 100        # je 10 Minuten
WINDOW_S = 600


def generate_code(n: int = 10) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def normalize(code: str | None) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


class Auth:
    def __init__(self, token: str) -> None:
        self.code = normalize(token)
        self.enabled = bool(self.code)
        self._salt = secrets.token_bytes(16)    # Sessions werden beim Neustart ungültig
        self._fails: dict[str, deque] = defaultdict(deque)
        self._gfails: deque = deque()

    def session_value(self) -> str:
        return hmac.new(self._salt, self.code.encode(), hashlib.sha256).hexdigest()

    def check_code(self, code: str | None) -> bool:
        return self.enabled and hmac.compare_digest(normalize(code).encode(), self.code.encode())

    def check_cookie(self, value: str | None) -> bool:
        return bool(value) and hmac.compare_digest(value.encode(), self.session_value().encode())

    def _prune(self, dq: deque) -> None:
        now = time.monotonic()
        while dq and now - dq[0] > WINDOW_S:
            dq.popleft()

    def blocked(self, ip: str) -> bool:
        self._prune(self._fails[ip])
        self._prune(self._gfails)
        return len(self._fails[ip]) >= MAX_FAIL_PER_IP or len(self._gfails) >= MAX_FAIL_GLOBAL

    def record_fail(self, ip: str) -> None:
        now = time.monotonic()
        self._fails[ip].append(now)
        self._gfails.append(now)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def client_ip(headers, peer: str) -> str:
    """Client-IP für die Fehlversuch-Sperre.

    CF-Connecting-IP (von cloudflared gesetzt) wird nur akzeptiert, wenn die Verbindung von
    localhost kommt – also vom lokal laufenden Tunnel. Im LAN-Betrieb könnte ein Client den Header
    sonst fälschen.
    """
    if peer in LOOPBACK:
        cf = headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
    return peer


def _hostport(value: str) -> str:
    v = value.strip().lower().rstrip("/")
    for suffix in (":443", ":80"):
        if v.endswith(suffix):
            v = v[: -len(suffix)]
    return v


def origin_ok(origin: str | None, host: str | None, forwarded_host: str | None = None) -> bool:
    """Schutz gegen Cross-Site-WebSocket-Hijacking: Origin muss zum eigenen Host passen.

    Hinter cloudflared ist Host (bzw. X-Forwarded-Host) der trycloudflare-Hostname und Origin
    https://<derselbe Hostname>; das wird akzeptiert. Ohne Origin-Header (Nicht-Browser-Client)
    entscheidet allein die Anmeldung.
    """
    if not origin:
        return True
    if origin == "null":
        return False
    o = _hostport(origin.split("://", 1)[-1])
    allowed = {_hostport(h) for h in (host, forwarded_host) if h}
    return o in allowed


def is_https(scheme: str, headers) -> bool:
    if scheme in ("https", "wss"):
        return True
    if (headers.get("x-forwarded-proto") or "").lower().split(",")[0].strip() == "https":
        return True
    return '"scheme":"https"' in (headers.get("cf-visitor") or "").replace(" ", "")


def login_page(error: str = "", next_url: str = "/") -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>ExSE Harp Mobile – Anmeldung</title><style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0f172a;color:#e2e8f0;
font:16px system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
form{{background:#111c33;border:1px solid #22304f;border-radius:16px;padding:24px;width:min(340px,90vw)}}
h1{{font-size:20px;margin:0 0 6px}}p{{color:#94a3b8;font-size:14px;margin:0 0 16px}}
input{{width:100%;box-sizing:border-box;font-size:22px;letter-spacing:3px;text-transform:uppercase;padding:12px;
border-radius:10px;border:1px solid #22304f;background:#0b1222;color:#e2e8f0;text-align:center}}
button{{width:100%;margin-top:14px;padding:13px;font-size:17px;border:0;border-radius:10px;background:#0e7490;color:#fff}}
.err{{color:#fecaca;margin-top:12px}}</style></head><body>
<form method="post" action="/login"><h1>ExSE Harp Mobile</h1><p>Bitte Zugangscode eingeben.</p>
<input name="code" autocomplete="one-time-code" autocapitalize="characters" autocorrect="off" spellcheck="false" autofocus required>
<input type="hidden" name="next" value="{html.escape(next_url)}">
<button type="submit">Anmelden</button>{err}</form></body></html>"""
