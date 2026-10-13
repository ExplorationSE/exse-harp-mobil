"""End-to-End-Prüfung gegen einen laufenden Server (WebSocket-Client).

Aufruf: python tools/ws_client_check.py [ws://127.0.0.1:8765/ws]
Prüft: Frames kommen an (Rate, Größe), Steuerbefehle wirken, Validierung, Steuer-Sperre.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import numpy as np
import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765/ws"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'OK ' if ok else 'FEHLER'}] {name} {detail}")


async def collect(ws, seconds: float) -> list[tuple[dict, int]]:
    frames, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.monotonic()))
        except asyncio.TimeoutError:
            break
        m = json.loads(raw)
        if m["type"] == "frame":
            frames.append((m, len(raw)))
    return frames


async def cmd(ws, c: str, params: dict | None = None) -> dict:
    await ws.send(json.dumps({"type": "command", "cmd": c, "params": params or {}, "id": c}))
    while True:
        m = json.loads(await ws.recv())
        if m["type"] == "ack" and m.get("id") == c:
            return m


async def main() -> int:
    async with websockets.connect(URL, max_size=2**24) as ws, websockets.connect(URL) as ws2:
        st = json.loads(await ws.recv())
        check("Status bei Verbindung", st["type"] == "status", f"Quelle={st['source']} Datei={st['file']}")
        if not st["running"]:
            await cmd(ws, "acquire_control"); await cmd(ws, "start")
        fr = await collect(ws, 3.0)
        rate = len(fr) / 3.0
        sizes = [n for _, n in fr]
        check("Frames kommen an (5–10 Hz)", 4.0 <= rate <= 11.0, f"{rate:.1f} Frames/s")
        m = fr[-1][0]
        h = np.array(m["hist"])
        check("Histogramm-Form", h.shape == (len(m["channels"]), m["n_bins"]),
              f"{h.shape}, Bin {m['bin_width_ps']} ps, Summe {int(h.sum())}")
        peak_ns = [(int(np.argmax(r)) + 0.5) * m["bin_width_ps"] / 1000 for r in h]
        check("Peak bei IRF ≈ 3 ns", all(2.5 < p < 4.5 for p in peak_ns), f"Peaks {np.round(peak_ns, 2)} ns")
        check("Zählraten plausibel", all(1e2 < r < 1e5 for r in m["rates"]), f"{m['rates']} cps, Sync {m['sync_rate']}")
        print(f"     Nutzlast: Ø {np.mean(sizes)/1000:.1f} kB/Frame, {m['payload_kBps']} kB/s (Server-Messung)")
        # Steuerung
        a = await cmd(ws2, "set", {"binning": 4})
        check("Ohne Steuerung abgelehnt", not a["ok"], a.get("error", ""))
        a = await cmd(ws, "acquire_control")
        check("Steuerung übernehmen", a["ok"])
        a = await cmd(ws2, "acquire_control")
        check("Zweiter Client bekommt keine Steuerung", not a["ok"], a.get("error", ""))
        a = await cmd(ws, "set", {"binning": 99})
        check("Validierung binning=99", not a["ok"], a.get("error", ""))
        a = await cmd(ws, "set", {"channels": [7]})
        check("Validierung Kanal 7", not a["ok"], a.get("error", ""))
        a = await cmd(ws, "set", {"refresh_ms": 50})
        check("Validierung refresh_ms=50", not a["ok"], a.get("error", ""))
        a = await cmd(ws, "open_file", {"name": "../config.toml"})
        check("Pfad-Traversal abgelehnt", not a["ok"], a.get("error", ""))
        a = await cmd(ws, "set", {"binning": 4, "channels": [1, 3], "display_points": 256,
                                  "hist_len_code": 1, "refresh_ms": 100})
        check("set binning/Kanäle/Punkte", a["ok"], a.get("error", ""))
        fr = await collect(ws, 2.0)
        m = fr[-1][0]
        check("Änderung wirkt", m["channels"] == [1, 3] and m["n_bins"] == 256 and m["n_bins_full"] == 2048
              and m["bin_width_ps"] == 4 * 16 * 8, f"Kanäle {m['channels']}, {m['n_bins']}/{m['n_bins_full']} Bins, "
              f"Bin {m['bin_width_ps']} ps, {len(fr)/2:.1f} Hz")
        a = await cmd(ws, "set", {"mode": "sliding", "window_frames": 5, "speed": 4})
        fr = await collect(ws, 1.5)
        m = fr[-1][0]
        check("Gleitendes Fenster", a["ok"] and abs(m["acc_time_s"] - 5 * 0.1 * 4) < 0.15, f"acc_time {m['acc_time_s']} s")
        a = await cmd(ws, "stop")
        await asyncio.sleep(0.3)
        fr = await collect(ws, 1.0)
        check("Stopp", a["ok"] and len(fr) == 0, f"{len(fr)} Frames nach Stopp")
        a = await cmd(ws, "open_file", {"name": "Demo_Synthetisch_T2_Einzelphotonen_HBT_90s.ptu"})
        a2 = await cmd(ws, "start")
        fr = await collect(ws, 1.5)
        m = fr[-1][0] if fr else {}
        check("T2-Datei öffnen + Start", a["ok"] and a2["ok"] and bool(fr) and m.get("has_histogram"),
              f"Ansicht {m.get('hist_kind')}, Kanäle {m.get('channels')}, Korr. Ref K{m.get('corr_ref')} → {m.get('corr_channels')}, {m.get('corr_bin_ps')} ps/Bin, "
              f"Sync {m.get('sync_rate')}")
        # Zurück auf Standard
        await cmd(ws, "open_file", {"name": "Demo_Synthetisch_T3_Fluoreszenz_4Kanal_5min.ptu"})
        await cmd(ws, "set", {"binning": 3, "channels": [1, 2, 3, 4], "display_points": 1024, "hist_len_code": 0,
                              "refresh_ms": 150, "mode": "accumulate", "speed": 1,
                              "window_frames": 20})
        await cmd(ws, "release_control")
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} Prüfungen bestanden")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
