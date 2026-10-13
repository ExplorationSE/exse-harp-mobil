"""Gegenprobe des eigenen PTU-Readers mit unabhängigen Readern (ptufile, phconvert), falls installiert.

Aufruf: python tools/crosscheck.py data/Demo_Synthetisch_T3_Fluoreszenz_4Kanal_5min.ptu
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harp.ptu import read_all  # noqa: E402


def main(path: str) -> int:
    hdr, ev = read_all(path)
    print(f"eigener Reader: {hdr.record_type_name}, {len(ev)} Photonen, Kanäle "
          f"{dict(zip(*[x.tolist() for x in np.unique(ev.channel, return_counts=True)]))}")
    ok = True
    try:
        import ptufile
        with ptufile.PtuFile(path) as f:
            rec = f.decode_records()
        # ptufile: Felder 'time' (Makrozeit), 'dtime' (-1 für Nicht-Photonen), 'channel' (0-basiert, -1 = special)
        if hdr.mode == "T3":
            ph = rec[rec["dtime"] >= 0]
            t_ok = np.array_equal(ph["time"].astype(np.int64), ev.time)
            c_ok = np.array_equal(ph["channel"].astype(np.int64) + 1, ev.channel.astype(np.int64))
            d_ok = np.array_equal(ph["dtime"].astype(np.int64), ev.dtime.astype(np.int64))
            n_ok = len(ph) == len(ev)
        else:
            # T2: ptufile meldet Sync-Ereignisse ebenfalls als Kanal 0 (wie Eingang 1); PicoQuants
            # Read_PTU.py und dieser Reader unterscheiden Sync (0) und Eingänge (1..).
            ph = rec[rec["channel"] >= 0]
            t_ok = np.array_equal(ph["time"].astype(np.int64), ev.time)
            c_ok = np.array_equal(ph["channel"].astype(np.int64),
                                  np.maximum(ev.channel.astype(np.int64) - 1, 0))
            d_ok = True
            n_ok = len(ph) == len(ev)
        print(f"ptufile {ptufile.__version__}: {len(ph)} Ereignisse; Zeit gleich={t_ok}, Kanal gleich={c_ok}, "
              f"dtime gleich={d_ok}")
        ok &= bool(t_ok and c_ok and d_ok and n_ok)
    except ImportError:
        print("ptufile nicht installiert – übersprungen")
    try:
        import phconvert as phc
        if hdr.mode == "T3":
            ts, det, nanot = phc.pqreader.load_ptu(path)[:3]
            same = np.array_equal(np.asarray(ts, np.int64), ev.time) and \
                np.array_equal(np.asarray(nanot, np.int64), ev.dtime.astype(np.int64))
            print(f"phconvert {phc.__version__}: {len(ts)} Photonen; Zeit+dtime gleich={same}; "
                  f"Detektoren {sorted(set(np.unique(det).tolist()))}")
            ok &= bool(same)
    except ImportError:
        print("phconvert nicht installiert – übersprungen")
    except Exception as e:  # phconvert unterstützt evtl. nicht jeden Record-Typ
        print(f"phconvert: nicht auswertbar ({type(e).__name__}: {e})")
    print("ERGEBNIS:", "übereinstimmend" if ok else "ABWEICHUNG")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/Demo_Synthetisch_T3_Fluoreszenz_4Kanal_5min.ptu"))
