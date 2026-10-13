"""FastAPI-Server: Live-Histogramme per WebSocket, Steuerbefehle, statische Web-App."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .auth import COOKIE, Auth, client_ip, is_https, login_page, origin_ok
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import BASE_DIR, Config, load_config
from .control import (ALLOWED_CMDS, CommandError, CommandLog, ControlLock, Settings, safe_file,
                      validate_set)
from .histogram import CORR_BINS, Accumulator, RateTracker, hist_len_from_code, rebin
from .sources import AcqParams, DataSource, SourceError, SourceInfo, create_source

log = logging.getLogger("harp")
WEB_DIR = BASE_DIR / "web"


SEND_TIMEOUT_S = 5.0     # länger hängendes Senden → Verbindung gilt als tot und wird geschlossen
MAX_QUEUED = 200         # mehr wartende Einzelnachrichten (Quittungen, Pong) → Client hängt


class Client:
    """Eine WebSocket-Verbindung mit eigener Sende-Warteschlange und eigenem Sende-Task.

    Niemand sonst ruft ws.send_text() auf: Broadcasts legen nur Nachrichten ab und kehren sofort
    zurück. Ein hängendes Handy blockiert so weder andere Clients noch die Befehlsverarbeitung.
    Frame und Status werden "latest-only" gehalten (veraltete werden überschrieben), Quittungen,
    Pong und Verlauf in Reihenfolge. Hängt ein Senden länger als SEND_TIMEOUT_S, wird die
    Verbindung aktiv geschlossen – das Handy merkt das und verbindet sich neu.
    """

    def __init__(self, ws: WebSocket, addr: str, on_dead=None, send_timeout: float = SEND_TIMEOUT_S) -> None:
        self.ws = ws
        self.addr = addr
        self.id = uuid.uuid4().hex[:8]
        self.dropped = 0
        self.closed = False
        self.send_timeout = send_timeout
        self._queue: deque[str] = deque()
        self._status: str | None = None
        self._frame: str | None = None
        self._wake = asyncio.Event()
        self._on_dead = on_dead
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    def post(self, txt: str) -> None:
        if self.closed:
            return
        if len(self._queue) >= MAX_QUEUED:
            asyncio.create_task(self.kill("Warteschlange voll"))
            return
        self._queue.append(txt)
        self._wake.set()

    def post_status(self, txt: str) -> None:
        if not self.closed:
            self._status = txt
            self._wake.set()

    def post_frame(self, txt: str) -> None:
        if self.closed:
            return
        if self._frame is not None:
            self.dropped += 1   # Backpressure: langsamer Client überspringt Frames
        self._frame = txt
        self._wake.set()

    def _next(self) -> str | None:
        if self._queue:
            return self._queue.popleft()
        if self._status is not None:
            t, self._status = self._status, None
            return t
        if self._frame is not None:
            t, self._frame = self._frame, None
            return t
        return None

    async def _run(self) -> None:
        try:
            while not self.closed:
                await self._wake.wait()
                self._wake.clear()
                while not self.closed and (txt := self._next()) is not None:
                    try:
                        await asyncio.wait_for(self.ws.send_text(txt), timeout=self.send_timeout)
                    except asyncio.TimeoutError:
                        await self.kill(f"Senden hing länger als {self.send_timeout:g} s")
                        return
                    except Exception as e:
                        await self.kill(f"Sendefehler: {type(e).__name__}")
                        return
        except asyncio.CancelledError:
            pass

    async def kill(self, reason: str) -> None:
        """Verbindung schließen (hängende Gegenstelle) und beim Dienst abmelden."""
        if self.closed:
            return
        self.closed = True
        log.warning("Client %s (%s) wird getrennt: %s", self.id, self.addr, reason)
        try:
            await asyncio.wait_for(self.ws.close(code=1011), timeout=1.0)
        except Exception:
            pass
        if self._on_dead:
            self._on_dead(self)

    def stop(self) -> None:
        self.closed = True
        self._wake.set()
        if self.task and not self.task.done() and self.task is not asyncio.current_task():
            self.task.cancel()


class HarpService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.settings = Settings(**cfg.defaults) if cfg.defaults else Settings()
        self.source: DataSource = create_source(cfg.source,
                                                snapi_windows_cumulative=cfg.snapi_windows_cumulative,
                                                snapi_base_window_s=cfg.snapi_base_window_s)
        self.info: SourceInfo | None = None
        self.file: str | None = None
        self.acc = Accumulator(self.settings.mode, self.settings.window_frames)
        self.cacc = Accumulator(self.settings.mode, self.settings.window_frames)   # Korrelation
        self.rates = RateTracker()
        self.lock = ControlLock(cfg.lock_timeout_s)
        self.cmdlog = CommandLog(cfg.log_dir)
        self.clients: dict[str, Client] = {}
        self.src_lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.running = False
        self.error = ""
        self.seq = 0
        self.last_bytes = 0
        self.bytes_hist: list[tuple[float, int]] = []
        self.last_frame_info: dict[str, Any] = {}

    # ------------------------------------------------------------ Dateien/Quelle
    def list_files(self) -> list[dict]:
        d = self.cfg.data_dir
        if not d.is_dir():
            return []
        # Eine Iteration über den Ordner; Endung unabhängig von Groß-/Kleinschreibung prüfen.
        # (Früher: glob("*.ptu") + glob("*.PTU") – unter Windows liefern beide Muster wegen des
        # case-insensitiven Dateisystems dieselben Dateien → jede Datei erschien doppelt.)
        seen: set[str] = set()
        out = []
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.suffix.lower() == ".ptu" and p.name.lower() not in seen:
                seen.add(p.name.lower())
                out.append({"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 2)})
        return out

    def _acq_params(self) -> AcqParams:
        return AcqParams(binning=self.settings.binning, hist_len_code=self.settings.hist_len_code,
                         loop=self.settings.loop, ref_channel=self.settings.ref_channel,
                         corr_binning=self.settings.corr_binning)

    def _start_binning(self, info) -> dict[str, int]:
        """Passende Startwerte beim Öffnen einer Datei.

        Histogramm: T3 → Standard (Auflösung × 8, 1024 Bins); T2 → 1024 Bins über ≈ 10 Sync-Perioden (Start = Referenzkanal).
        Korrelation (2048 Bins, symmetrisch): Fenster ≈ 10 Sync-Perioden (±5), ohne Sync-Angabe ±0,5 µs.
        Basis der Korrelations-Binbreite: T3 1 ps, T2 die Makrozeit-Auflösung.
        """
        period_ps = 1e12 / info.sync_rate_hz if info.sync_rate_hz > 0 else 1e5
        log2r = lambda x: int(np.round(np.log2(max(1.0, x))))   # noqa: E731
        if info.mode == "T3":
            d = Settings(**self.cfg.defaults) if self.cfg.defaults else Settings()
            upd = {"binning": min(d.binning, info.max_binning), "hist_len_code": d.hist_len_code}
            base = 1.0
        else:
            res = max(info.resolution_ps, 1e-3)
            # Start-Stopp ab Referenzkanal: 1024 Bins über ≈ 10 Sync-Perioden
            upd = {"binning": max(0, min(log2r(10 * period_ps / (1024 * res)), 15, info.max_binning)),
                   "hist_len_code": 0}
            base = res
        upd["corr_binning"] = max(0, min(log2r(10 * period_ps / (CORR_BINS * base)), 24))
        upd["ref_channel"] = 1
        if info.mode == "T2" and info.first_channel == 0:
            upd["ref_channel"] = 0      # Sync/Herald K0 als Referenz
            if not info.k0_periodic:    # K0 zufällig (Herald): Koinzidenzen im ns-Bereich
                upd["binning"] = max(0, min(log2r(50_000 / (1024 * res)), 15, info.max_binning))   # 0…≈50 ns
                upd["corr_binning"] = max(0, min(log2r(100_000 / (CORR_BINS * base)), 24))       # ±≈50 ns
        return upd

    async def open_file(self, name: str) -> None:
        path = safe_file(self.cfg.data_dir, name)
        was_running = self.running
        await self.stop()
        async with self.src_lock:
            info = await asyncio.to_thread(self.source.open, path)
            self.info, self.file = info, name
            # neue Datei: alle Kanäle anzeigen (Ein-/Ausblenden erfolgt in der Oberfläche)
            chans = list(range(info.first_channel, min(info.num_channels, 8) + 1))
            upd: dict[str, Any] = {"channels": chans} | self._start_binning(info)
            self.settings = Settings(**(self.settings.model_dump() | upd))
            self.source.configure(self._acq_params())
        self.acc.reset(); self.cacc.reset()
        self.rates.reset()
        self.error = ""
        if was_running:
            await self.start()

    # ------------------------------------------------------------ Ablauf
    async def start(self) -> None:
        if self.running:
            return
        if self.info is None:
            raise CommandError("Keine Datenquelle geöffnet")
        async with self.src_lock:
            self.source.configure(self._acq_params())
            await asyncio.to_thread(self.source.start)
        self.running = True
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        async with self.src_lock:
            await asyncio.to_thread(self.source.stop)

    async def _loop(self) -> None:
        try:
            while self.running:
                t0 = time.monotonic()
                s = self.settings
                async with self.src_lock:
                    fr = await asyncio.to_thread(self.source.read_frame, s.frame_meas_time_s)
                if fr is None:
                    self.running = False
                    self.error = "Ende der Datei erreicht"
                    await self.broadcast_status()
                    break
                if fr.wrapped:  # Datei beginnt von vorn: Akkumulation und Verlauf neu beginnen
                    self.acc.reset(); self.cacc.reset()
                    self.rates.reset()
                total = self.acc.add(fr)
                if fr.corr is not None:
                    self.cacc.add_array(fr.corr, fr.meas_time_s)
                rates = (fr.counts / fr.meas_time_s).tolist() if fr.meas_time_s > 0 else [0.0] * len(fr.counts)
                sync_rate = fr.sync_count / fr.meas_time_s if fr.meas_time_s > 0 else 0.0
                self.rates.add(fr.t_file_s, rates)
                calc_ms = (time.monotonic() - t0) * 1000
                await self.broadcast_frame(total, fr, rates, sync_rate, calc_ms)
                dt = s.refresh_ms / 1000.0 - (time.monotonic() - t0)
                await asyncio.sleep(max(0.0, dt))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # Quelle meldet Fehler → stoppen, Clients informieren
            log.exception("Fehler in der Messschleife")
            self.running = False
            self.error = f"Fehler: {e}"
            await self.broadcast_status()

    # ------------------------------------------------------------ Nachrichten
    def build_frame(self, total: np.ndarray, fr, rates: list[float], sync_rate: float,
                    calc_ms: float) -> str:
        s = self.settings
        first = self.info.first_channel if self.info else 1
        sel = [c for c in s.channels if 0 <= c - first < total.shape[0]]
        kind = getattr(fr, "hist_kind", "dtime")
        corr_msg: dict[str, Any] = {"corr": None}
        ct = self.cacc.total
        if getattr(fr, "corr", None) is not None and ct is not None:
            csel = [c for c in sel if c != s.ref_channel and c - first < ct.shape[0]]   # Referenz ohne eigene Kurve
            ch_ = ct[[c - first for c in csel]] if csel else np.zeros((0, ct.shape[1]), np.uint64)
            cdisp, cf = rebin(ch_, min(s.display_points, CORR_BINS))
            corr_msg = {"corr": cdisp.astype(np.int64).tolist(), "corr_channels": csel,
                        "corr_ref": s.ref_channel, "corr_bin_ps": fr.corr_bin_ps * cf,
                        "corr_x0_ps": fr.corr_x0_ps, "corr_n_bins": int(cdisp.shape[1])}
        if kind == "ref":   # T2: Referenzkanal ist Start, keine eigene Kurve
            sel = [c for c in sel if c != s.ref_channel]
        h = total[[c - first for c in sel]] if sel else np.zeros((0, total.shape[1]), np.uint64)
        disp, f = rebin(h, s.display_points)
        now = time.monotonic()
        self.bytes_hist = [(t, b) for t, b in self.bytes_hist if now - t < 5.0]
        kbps = sum(b for _, b in self.bytes_hist) / 5.0 / 1000.0
        self.seq += 1
        msg = {
            "type": "frame", "seq": self.seq,
            "t_file_s": round(fr.t_file_s, 4), "progress": round(fr.progress, 4),
            "acc_time_s": round(self.acc.total_time_s, 4), "mode": s.mode,
            "bin_width_ps": fr.bin_width_ps * f, "rebin_factor": f,
            "n_bins_full": int(total.shape[1]), "n_bins": int(disp.shape[1]),
            "channels": sel, "has_histogram": fr.has_histogram, "first_channel": first,
            "hist_kind": kind, **corr_msg,
            "hist": disp.astype(np.int64).tolist(),
            "rates": [round(r, 1) for r in rates], "sync_rate": round(sync_rate, 1),
            "total_counts": [int(x) for x in total.sum(axis=1)],
            "calc_ms": round(calc_ms, 2),
            "payload_bytes_prev": self.last_bytes, "payload_kBps": round(kbps, 2),
        }
        txt = json.dumps(msg, separators=(",", ":"))
        self.last_bytes = len(txt.encode())
        self.bytes_hist.append((now, self.last_bytes))
        self.last_frame_info = {k: msg[k] for k in ("seq", "t_file_s", "n_bins", "bin_width_ps",
                                                    "payload_bytes_prev", "payload_kBps")}
        return txt

    async def broadcast_frame(self, total, fr, rates, sync_rate, calc_ms) -> None:
        if not self.clients:
            self.seq += 1
            return
        txt = self.build_frame(total, fr, rates, sync_rate, calc_ms)
        for c in list(self.clients.values()):
            c.post_frame(txt)   # kehrt sofort zurück; Senden im Task des Clients

    def remove_client(self, c: Client) -> None:
        """Client abmelden (getrennt oder wegen Hängens geschlossen); gibt ggf. die Steuerung frei."""
        c.stop()
        if self.clients.pop(c.id, None) is not None:
            self.lock.release(c.id)
            log.info("Client %s getrennt", c.id)
            self.post_status_all()

    def status(self, c: Client | None = None) -> dict:
        return {
            "type": "status", "version": __version__, "running": self.running,
            "settings": self.settings.model_dump(), "info": self.info.to_dict() if self.info else None,
            "file": self.file, "files": self.list_files(), "source": self.cfg.source,
            "error": self.error, "clients": len(self.clients),
            "control": {"held": self.lock.holder is not None,
                        "mine": c is not None and self.lock.holder == c.id},
            "client_id": c.id if c else None,
            "hist_len": hist_len_from_code(self.settings.hist_len_code),
            "auth": bool(self.cfg.token),
        }

    def post_status_all(self) -> None:
        for c in list(self.clients.values()):
            c.post_status(json.dumps(self.status(c)))

    async def broadcast_status(self) -> None:
        self.post_status_all()   # nicht blockierend: jeder Client sendet in seinem eigenen Task

    # ------------------------------------------------------------ Befehle
    async def handle_command(self, c: Client, msg: dict) -> dict:
        cmd = msg.get("cmd")
        params = msg.get("params") or {}
        rid = msg.get("id")
        try:
            if cmd not in ALLOWED_CMDS:
                raise CommandError(f"Unbekannter Befehl: {cmd}")
            if cmd == "acquire_control":
                if not self.lock.acquire(c.id):
                    raise CommandError("Ein anderer Client steuert bereits")
            elif cmd == "release_control":
                self.lock.release(c.id)
            else:
                if not self.lock.check(c.id):
                    raise CommandError("Keine Steuerberechtigung – bitte zuerst „Steuerung übernehmen“")
                await self._exec(cmd, params)
            self.cmdlog.write(c.id, c.addr, str(cmd), params, True)
            return {"type": "ack", "id": rid, "cmd": cmd, "ok": True}
        except (CommandError, SourceError) as e:
            self.cmdlog.write(c.id, c.addr, str(cmd), params, False, str(e))
            return {"type": "ack", "id": rid, "cmd": cmd, "ok": False, "error": str(e)}

    async def _exec(self, cmd: str, params: dict) -> None:
        if cmd == "start":
            await self.start()
        elif cmd == "stop":
            await self.stop()
        elif cmd == "reset":
            self.acc.reset(); self.cacc.reset()
            self.rates.reset()
        elif cmd == "rewind":
            async with self.src_lock:
                await asyncio.to_thread(self.source.rewind)
            self.acc.reset(); self.cacc.reset()
            self.rates.reset()
            self.error = ""
        elif cmd == "open_file":
            await self.open_file(params.get("name", ""))
        elif cmd == "set":
            if self.info is None:
                raise CommandError("Keine Datenquelle geöffnet")
            old = self.settings
            new = validate_set(old, params, self.info.num_channels, self.info.max_binning,
                               self.info.first_channel)
            self.settings = new
            keys = ("binning", "hist_len_code", "loop", "ref_channel", "corr_binning")
            if any(getattr(new, k) != getattr(old, k) for k in keys):
                async with self.src_lock:
                    self.source.configure(self._acq_params())
                t2ref = self.info.mode == "T2" and new.ref_channel != old.ref_channel
                if (new.binning, new.hist_len_code) != (old.binning, old.hist_len_code) or t2ref:
                    self.acc.reset()
                if (new.ref_channel, new.corr_binning) != (old.ref_channel, old.corr_binning):
                    self.cacc.reset()
            self.acc.configure(new.mode, new.window_frames)
            self.cacc.configure(new.mode, new.window_frames)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    svc = HarpService(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            if cfg.default_file and (cfg.data_dir / cfg.default_file).is_file():
                await svc.open_file(cfg.default_file)
            elif svc.list_files():
                await svc.open_file(svc.list_files()[0]["name"])
            if svc.info and cfg.autostart:
                await svc.start()
        except Exception as e:
            svc.error = f"Start fehlgeschlagen: {e}"
            log.exception("Start fehlgeschlagen")
        yield
        await svc.stop()
        svc.source.close()

    app = FastAPI(title="ExSE Harp Mobil", version=__version__, lifespan=lifespan)
    app.state.svc = svc

    auth = Auth(cfg.token)
    app.state.auth = auth
    PUBLIC = {"/login", "/logout", "/api/health", "/static/icon.svg"}

    def _secure(request: Request) -> bool:
        return is_https(request.url.scheme, request.headers)

    def _set_cookie(resp, request: Request) -> None:
        resp.set_cookie(COOKIE, auth.session_value(), httponly=True, samesite="lax",
                        secure=_secure(request), max_age=7 * 24 * 3600, path="/")

    def _safe_next(n: str | None) -> str:
        return n if n and n.startswith("/") and not n.startswith("//") else "/"

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if not auth.enabled or request.url.path in PUBLIC:
            resp = await call_next(request)
        elif auth.check_cookie(request.cookies.get(COOKIE)):
            resp = await call_next(request)
        else:
            ip = client_ip(request.headers, request.client.host if request.client else "?")
            tok = request.query_params.get("token")
            if tok is not None and not auth.blocked(ip) and auth.check_code(tok):
                if request.url.path.startswith("/api/"):
                    resp = await call_next(request)
                else:   # Code aus der URL → Cookie setzen und Code aus der Adresszeile entfernen
                    q = {k: v for k, v in request.query_params.items() if k != "token"}
                    resp = RedirectResponse(request.url.path + ("?" + urlencode(q) if q else ""), 303)
                    _set_cookie(resp, request)
            else:
                if tok is not None:
                    auth.record_fail(ip)
                if request.url.path.startswith("/api/"):
                    resp = JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
                else:
                    resp = RedirectResponse("/login?" + urlencode({"next": request.url.path}), 303)
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        return resp

    @app.get("/login")
    async def login_get(next: str = "/"):
        if not auth.enabled:
            return RedirectResponse("/", 303)
        return HTMLResponse(login_page("", _safe_next(next)), headers={"Cache-Control": "no-store"})

    @app.post("/login")
    async def login_post(request: Request):
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        code = (form.get("code") or [""])[0]
        nxt = _safe_next((form.get("next") or ["/"])[0])
        ip = client_ip(request.headers, request.client.host if request.client else "?")
        if auth.blocked(ip):
            log.warning("Login gesperrt für %s", ip)
            return HTMLResponse(login_page("Zu viele Fehlversuche – bitte später erneut versuchen.", nxt), 429)
        if not auth.check_code(code):
            auth.record_fail(ip)
            log.warning("Falscher Zugangscode von %s", ip)
            await asyncio.sleep(1.0)
            return HTMLResponse(login_page("Falscher Zugangscode.", nxt), 401)
        log.info("Anmeldung erfolgreich von %s", ip)
        resp = RedirectResponse(nxt, 303)
        _set_cookie(resp, request)
        return resp

    @app.get("/logout")
    async def logout():
        resp = RedirectResponse("/login", 303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    def check_token(token: str | None) -> None:
        pass  # Prüfung erfolgt in auth_middleware

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    async def sw():
        return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript")

    @app.get("/api/health")
    async def health():
        return {"ok": True, "name": "ExSE Harp Mobil", "version": __version__}

    @app.get("/api/status")
    async def api_status(token: str | None = Query(None)):
        check_token(token)
        st = svc.status()
        st["last_frame"] = svc.last_frame_info
        return JSONResponse(st)

    @app.get("/api/files")
    async def api_files(token: str | None = Query(None)):
        check_token(token)
        return svc.list_files()

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, token: str | None = Query(None)):
        if auth.enabled:
            ip = client_ip(ws.headers, ws.client.host if ws.client else "?")
            ok = auth.check_cookie(ws.cookies.get(COOKIE)) or (
                token is not None and not auth.blocked(ip) and auth.check_code(token))
            if not origin_ok(ws.headers.get("origin"), ws.headers.get("host"),
                             ws.headers.get("x-forwarded-host")):
                log.warning("WebSocket mit fremder Origin abgewiesen: %s", ws.headers.get("origin"))
                ok = False   # Cross-Site-WebSocket-Hijacking verhindern
            if not ok:
                if token is not None:
                    auth.record_fail(ip)
                await ws.close(code=4401)
                return
        await ws.accept()
        addr = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
        c = Client(ws, addr, on_dead=svc.remove_client)
        svc.clients[c.id] = c
        c.start()
        log.info("Client %s verbunden (%s)", c.id, addr)
        try:
            c.post(json.dumps(svc.status(c)))
            if svc.rates.t:  # bisherigen Zählraten-Verlauf nachliefern
                c.post(json.dumps({"type": "trace", "t": list(svc.rates.t),
                                   "rates": [[round(x, 1) for x in r] for r in svc.rates.rates]}))
            while not c.closed:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    c.post(json.dumps({"type": "ack", "ok": False, "error": "kein JSON"}))
                    continue
                if msg.get("type") == "command":
                    ack = await svc.handle_command(c, msg)
                    c.post(json.dumps(ack))
                    svc.post_status_all()
                elif msg.get("type") == "ping":
                    c.post(json.dumps({"type": "pong", "t": msg.get("t")}))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            svc.remove_client(c)

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
