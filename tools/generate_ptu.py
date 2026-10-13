"""Erzeugt realistische synthetische PTU-Dateien (HydraHarp-500-ähnlich) zum Testen.

T3 (Standard): Sync 40 MHz, Auflösung 4 ps, 4 Kanäle mit verschiedenen Lebensdauern,
Gauß-IRF, Untergrund und zeitlich variierenden Intensitäten (Blinken, Sinus, Rampe).
Record-Typ: Generic T3 (0x00010307) bzw. optional HydraHarp V2 T3 (0x01010304).

Zusätzlich (--hbt): T2-Datei einer gepulst angeregten Einzelphotonenquelle im
Hanbury-Brown-Twiss-Aufbau (Strahlteiler, zwei Detektoren K1/K2): Peaks im Abstand der
Laserperiode, unterdrückter Mittelpeak (Antibunching, g²(0) < 0,5) und Bunching-Schultern
durch Blinken der Quelle (µs-Zeitskala), plus Dunkelzählungen.

Demo-Dateien erzeugen:
  python tools/generate_ptu.py --out data/Demo_Synthetisch_T3_Fluoreszenz_4Kanal_5min.ptu --duration 300 --rates 5000,7500,10000,3500
  python tools/generate_ptu.py --hbt --out data/Demo_Synthetisch_T2_Einzelphotonen_HBT_90s.ptu --duration 90
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harp.ptu import (RT_GENERIC_T2, RT_GENERIC_T3, RT_HYDRAHARP2_T3, encode_t2_generic,  # noqa: E402
                      encode_t3_generic, standard_tags, write_ptu)

DEFAULT_TAUS_NS = [0.6, 1.8, 3.2, 4.5]
DEFAULT_RATES = [40_000, 60_000, 90_000, 25_000]


def modulation(kind: str, t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if kind == "blink":  # Telegraphen-Rauschen: an/aus mit mittlerer Dauer 0,4 s
        state = np.empty(t.size)
        cur, i = 1.0, 0
        while i < t.size:
            n = max(1, int(rng.exponential(400)))
            state[i:i + n] = cur
            cur = 0.15 if cur == 1.0 else 1.0
            i += n
        return state
    if kind == "sine":
        return 0.6 + 0.4 * np.sin(2 * np.pi * 0.2 * t)
    if kind == "ramp":
        return 0.5 + 0.5 * (t / max(t[-1], 1e-9))
    return np.ones_like(t)


def gen_t3(duration_s: float, sync_hz: float, res_ps: float, taus_ns: list[float], rates: list[float],
           bg_frac: float, irf_ns: float, irf_sigma_ps: float, seed: int):
    rng = np.random.default_rng(seed)
    period_ps = 1e12 / sync_hz
    syncs_per_ms = int(round(sync_hz / 1000))
    nb = int(round(duration_s * 1000))
    tb = (np.arange(nb) + 0.5) / 1000.0
    kinds = ["blink", "sine", "ramp", "const"]
    all_ns, all_ch, all_dt = [], [], []
    for i, (tau, rate) in enumerate(zip(taus_ns, rates)):
        mod = modulation(kinds[i % 4], tb, rng)
        counts = rng.poisson(rate * mod / 1000.0)
        n = int(counts.sum())
        msidx = np.repeat(np.arange(nb), counts)
        nsync = msidx.astype(np.int64) * syncs_per_ms + rng.integers(0, syncs_per_ms, n)
        is_bg = rng.random(n) < bg_frac
        t_ps = irf_ns * 1000 + rng.normal(0, irf_sigma_ps, n) + rng.exponential(tau * 1000, n)
        t_ps = np.where(is_bg, rng.random(n) * period_ps, t_ps) % period_ps
        dt = np.floor(t_ps / res_ps).astype(np.int64)
        all_ns.append(nsync)
        all_ch.append(np.full(n, i + 1, np.int64))
        all_dt.append(dt)
    ns = np.concatenate(all_ns)
    ch = np.concatenate(all_ch)
    dt = np.concatenate(all_dt)
    order = np.argsort(ns, kind="stable")
    return ns[order], ch[order], dt[order]


def gen_t2(duration_s: float, sync_hz: float, taus_ns: list[float], rates: list[float], seed: int):
    """T2: Sync-Ereignisse (Kanal 0) + Photonen mit Verzögerung relativ zum Sync (Zeit in ps)."""
    rng = np.random.default_rng(seed)
    period = 1e12 / sync_hz
    nsync = int(duration_s * sync_hz)
    sync_t = (np.arange(nsync) * period).astype(np.int64)
    times, chans = [sync_t], [np.zeros(nsync, np.int64)]
    for i, (tau, rate) in enumerate(zip(taus_ns, rates)):
        n = rng.poisson(rate * duration_s)
        k = rng.integers(0, nsync, n)
        delay = (2000 + rng.normal(0, 50, n) + rng.exponential(tau * 1000, n)) % period
        times.append(sync_t[k] + delay.astype(np.int64))
        chans.append(np.full(n, i + 1, np.int64))
    t = np.concatenate(times)
    c = np.concatenate(chans)
    o = np.argsort(t, kind="stable")
    return t[o], c[o]


def gen_hbt(duration_s: float, rep_hz: float, rate_on: float, on_us: float, off_us: float,
            multi_frac: float, dark_cps: float, lifetime_ns: float, jitter_ps: float, delay2_ps: float,
            seed: int):
    """Einzelphotonenquelle (gepulst) + 50:50-Strahlteiler + 2 Detektoren, Zeiten in ps.

    - Höchstens ein Photon pro Laserpuls (Antibunching); Anteil ``multi_frac`` der detektierten Pulse
      liefert zusätzlich ein zweites Photon auf dem anderen Detektor (Mehrphotonen-Rest → g²(0) > 0;
      multi_frac ≈ g²(0)·p/2 mit p = detektierte Photonen pro Puls im An-Zustand).
    - Blinken: Telegraphen-Prozess an/aus mit Mittelwerten on_us/off_us (→ Bunching-Schultern).
    - Detektierte Rate im An-Zustand: ``rate_on`` (beide Detektoren zusammen).
    """
    rng = np.random.default_rng(seed)
    period_ps = 1e12 / rep_hz
    t_cur = 0.0                    # s
    times, chans = [], []
    while t_cur < duration_s:
        seg = min(1.0, duration_s - t_cur)
        n = int(seg / ((on_us + off_us) * 1e-6) * 1.2) + 50
        on = rng.exponential(on_us * 1e-6, n)
        off = rng.exponential(off_us * 1e-6, n)
        start = t_cur + np.concatenate([[0.0], np.cumsum(on + off)[:-1]])
        m = start < t_cur + seg
        on, off, start = on[m], off[m], start[m]
        t_cur = float(start[-1] + on[-1] + off[-1])
        k = rng.poisson(rate_on * on)
        tot = int(k.sum())
        tem = np.repeat(start, k) + rng.random(tot) * np.repeat(on, k)
        pulse = np.unique(np.floor(tem * rep_hz).astype(np.int64))   # max. 1 Photon pro Puls
        det = rng.integers(1, 3, pulse.size)
        multi = rng.random(pulse.size) < multi_frac
        pulse = np.concatenate([pulse, pulse[multi]])
        det = np.concatenate([det, 3 - det[multi]])
        t_ps = pulse * period_ps + 3000.0 + rng.exponential(lifetime_ns * 1000, pulse.size) \
            + rng.normal(0, jitter_ps, pulse.size) + np.where(det == 2, delay2_ps, 0.0)
        seg_end = t_cur
        seg_start = seg_end - (seg_end - (start[0] if start.size else seg_end))
        for d in (1, 2):
            nd = rng.poisson(dark_cps * (seg_end - seg_start))
            t_ps = np.concatenate([t_ps, (seg_start + rng.random(nd) * (seg_end - seg_start)) * 1e12])
            det = np.concatenate([det, np.full(nd, d)])
        times.append(t_ps.astype(np.int64))
        chans.append(det.astype(np.int64))
    t = np.concatenate(times)
    c = np.concatenate(chans)
    keep = t < duration_s * 1e12
    t, c = t[keep], c[keep]
    o = np.argsort(t, kind="stable")
    return t[o], c[o]


def gen_heralded(duration_s: float, pair_rate: float, eta_idler: float, eta_signal: float,
                 dark0: float, dark12: float, delay1_ps: float, delay2_ps: float, jitter_ps: float, seed: int):
    """Herald-Einzelphotonen (SPDC-artige Paarquelle, Dauerstrich-Pumpe), Zeiten in ps.

    K0 = Herald-Detektor (Idler, an den Sync-Eingang angeschlossen), K1/K2 = Signalarm hinter
    50:50-Strahlteiler (HBT). Paare entstehen zufällig (Poisson, kein Puls-Takt). Idler mit Effizienz
    eta_idler auf K0, Signal mit eta_signal auf K1 oder K2 (je 50 %), Verzögerung delay1/2,
    Detektor-Jitter ``jitter_ps`` (Gauß) je Detektor, Dunkelzählraten dark0/dark12.
    Erwartung: scharfe Koinzidenzpeaks K0–K1 bei +delay1, K0–K2 bei +delay2 auf flachem Untergrund
    (Zufallskoinzidenzen R0·R1·Δτ). K1–K2 ohne Herald-Bedingung: praktisch flach (Signal-Photonen
    verschiedener Paare sind unkorreliert; thermisches Bunching nur innerhalb der ps-Kohärenzzeit,
    hier vernachlässigt). Bedingtes Antibunching (Dreifach-Koinzidenzen) wird nicht ausgewertet.
    """
    rng = np.random.default_rng(seed)
    times, chans = [], []
    for k in range(int(np.ceil(duration_s))):
        seg = min(1.0, duration_s - k)
        t0 = k * 1e12
        n = rng.poisson(pair_rate * seg)
        tp = t0 + rng.random(n) * seg * 1e12
        idl = rng.random(n) < eta_idler
        sig = rng.random(n) < eta_signal
        arm = rng.integers(1, 3, n)
        parts_t = [tp[idl] + rng.normal(0, jitter_ps, idl.sum())]
        parts_c = [np.zeros(idl.sum(), np.int64)]
        ts = tp[sig] + np.where(arm[sig] == 1, delay1_ps, delay2_ps) + rng.normal(0, jitter_ps, sig.sum())
        parts_t.append(ts); parts_c.append(arm[sig].astype(np.int64))
        for ch, dk in ((0, dark0), (1, dark12), (2, dark12)):
            nd = rng.poisson(dk * seg)
            parts_t.append(t0 + rng.random(nd) * seg * 1e12); parts_c.append(np.full(nd, ch, np.int64))
        t = np.concatenate(parts_t); c = np.concatenate(parts_c)
        times.append(np.rint(t).astype(np.int64)); chans.append(c)
    t = np.concatenate(times); c = np.concatenate(chans)
    keep = (t >= 0) & (t < duration_s * 1e12)
    t, c = t[keep], c[keep]
    o = np.argsort(t, kind="stable")
    return t[o], c[o]


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/Demo_Synthetisch_T3_Fluoreszenz_4Kanal_5min.ptu")
    ap.add_argument("--duration", type=float, default=20.0, help="Messdauer in s")
    ap.add_argument("--sync", type=float, default=40e6, help="Sync-Rate in Hz")
    ap.add_argument("--res", type=float, default=4.0, help="T3-Auflösung in ps (1 ps × 2^n)")
    ap.add_argument("--rates", type=str, default=",".join(map(str, DEFAULT_RATES)))
    ap.add_argument("--taus", type=str, default=",".join(map(str, DEFAULT_TAUS_NS)))
    ap.add_argument("--bg", type=float, default=0.03, help="Untergrundanteil")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hydraharp2", action="store_true", help="Record-Typ HydraHarp V2 T3 statt Generic T3")
    ap.add_argument("--t2", action="store_true", help="T2-Datei erzeugen (Sync + Abklingkurven)")
    ap.add_argument("--hbt", action="store_true",
                    help="T2-Datei: Einzelphotonenquelle im HBT-Aufbau (2 Detektoren, gepulst 10 MHz)")
    ap.add_argument("--heralded", action="store_true",
                    help="T2-Datei: Herald-Einzelphotonen, K0 = Herald (Sync-Eingang), K1/K2 = HBT, Dauerstrich")
    a = ap.parse_args(argv)
    rates = [float(x) for x in a.rates.split(",")]
    taus = [float(x) for x in a.taus.split(",")]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.heralded:
        t, c = gen_heralded(a.duration, pair_rate=600_000, eta_idler=0.115, eta_signal=0.03, dark0=500,
                            dark12=1000, delay1_ps=12_000, delay2_ps=15_000, jitter_ps=25, seed=a.seed)
        rec = encode_t2_generic(t, c)
        r = [float(np.count_nonzero(c == k)) / a.duration for k in (0, 1, 2)]
        tags = standard_tags(record_type=RT_GENERIC_T2, num_records=rec.size, global_res_s=1e-12,
                             res_s=1e-12, mode=2, num_channels=2,
                             acq_time_ms=int(a.duration * 1000), sync_rate=r[0], input_rates=r[1:],
                             comment="Synthetisch: Herald-Einzelphotonen (SPDC, Dauerstrich). K0 = Herald "
                                     "(Sync-Eingang), K1/K2 = HBT; Koinzidenzen K0-K1 +12 ns, K0-K2 +15 ns")
    elif a.hbt:
        rep = 10e6
        t, c = gen_hbt(a.duration, rep_hz=rep, rate_on=50_000, on_us=1.5, off_us=1.0, multi_frac=0.12 * 50_000 / rep / 2,
                       dark_cps=250, lifetime_ns=1.5, jitter_ps=60, delay2_ps=1200, seed=a.seed)
        rec = encode_t2_generic(t, c)
        r = [float(np.count_nonzero(c == 1)) / a.duration, float(np.count_nonzero(c == 2)) / a.duration]
        tags = standard_tags(record_type=RT_GENERIC_T2, num_records=rec.size, global_res_s=1e-12,
                             res_s=1e-12, mode=2, num_channels=2,
                             acq_time_ms=int(a.duration * 1000), sync_rate=rep, input_rates=r,
                             comment="Synthetisch: Einzelphotonenquelle, HBT (K1/K2), Laser 10 MHz "
                                     "(Sync nicht aufgezeichnet), Antibunching + Blinken")
    elif a.t2:
        sync = a.sync if a.sync <= 5e6 else 2e6
        t, c = gen_t2(a.duration, sync, taus, rates, a.seed)
        rec = encode_t2_generic(t, c)
        tags = standard_tags(record_type=RT_GENERIC_T2, num_records=rec.size, global_res_s=1e-12,
                             res_s=1e-12, mode=2, num_channels=len(rates),
                             acq_time_ms=int(a.duration * 1000), sync_rate=sync, input_rates=rates,
                             comment="Synthetische T2-Testdaten")
    else:
        ns, ch, dt = gen_t3(a.duration, a.sync, a.res, taus, rates, a.bg, 3.0, 50.0, a.seed)
        rec = encode_t3_generic(ns, ch, dt)
        binning = int(round(np.log2(a.res)))
        rt = RT_HYDRAHARP2_T3 if a.hydraharp2 else RT_GENERIC_T3
        tags = standard_tags(record_type=rt, num_records=rec.size, global_res_s=1.0 / a.sync,
                             res_s=a.res * 1e-12, mode=3, num_channels=len(rates),
                             acq_time_ms=int(a.duration * 1000), sync_rate=a.sync, binning=binning,
                             input_rates=rates,
                             comment=f"Synthetisch: tau={taus} ns, rates={rates} cps, bg={a.bg}")
    write_ptu(out, tags, rec)
    print(f"geschrieben: {out}  ({rec.size} Records, {out.stat().st_size/1e6:.1f} MB)")
    return out


if __name__ == "__main__":
    main()
