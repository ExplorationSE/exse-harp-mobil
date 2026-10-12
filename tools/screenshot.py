"""Headless-Screenshots der Web-Oberfläche (Playwright/Chromium) + kleine UI-Prüfungen.

Aufruf: python tools/screenshot.py [http://127.0.0.1:8765/]
Erzeugt screenshot-ui9-*.png im Projektordner (T2-Histogramm mit Referenz, Korrelogramme, 390 px).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
OUT = Path(__file__).resolve().parent.parent
T3 = "Demo_T3_Fluoreszenz_4Kanal_5min.ptu"
T2 = "Demo_T2_Einzelphotonen_HBT_90s.ptu"
P = {"width": 390, "height": 844}
HER = "Demo_T2_Heralded_K0_HBT_60s.ptu"
VIEWS = [("heralded-korrelation-ref-k0", P, HER, "corr"), ("heralded-histo", P, HER, "hist"),
         ("heralded-360-header", {"width": 360, "height": 780}, HER, "corr"),
         ("t3-360-header", {"width": 360, "height": 780}, T3, "hist")]
CENTER_JS = """v => { const u = v === 'trace' ? S.trace : S.lp[v].u, card = document.querySelector('.hist-card').getBoundingClientRect();
  const ov = u.over.getBoundingClientRect(), x = u.data[0], n = x.length;
  const first = ov.left + u.valToPos(x[0], 'x'), last = ov.left + u.valToPos(x[n - 1], 'x');
  return { cardL: Math.round(ov.left - card.left), cardR: Math.round(card.right - ov.right),
           firstGap: +(first - ov.left).toFixed(1), lastGap: +(ov.right - last).toFixed(1),
           xmin: u.scales.x.min, xmax: u.scales.x.max }; }"""

ROW_JS = """sel => { const r = [...document.querySelectorAll(sel)].map(e => e.getBoundingClientRect());
  return { n: r.length, tops: r.map(x => Math.round(x.top)), right: Math.round(Math.max(...r.map(x => x.right))),
           vw: innerWidth }; }"""

with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []
    for name, vp, file, view in VIEWS:
        ctx = b.new_context(viewport=vp, device_scale_factor=3, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL)
        pg.wait_for_selector("#histPlot canvas", timeout=15000)
        pg.evaluate("localStorage.setItem('harp.view','hist')")
        if pg.evaluate("S.status && S.status.file") != file:
            pg.evaluate(f"command('open_file', {{name: '{file}'}})")
            pg.wait_for_function(f"S.lastFrame && S.status.file === '{file}'", timeout=15000)
            pg.evaluate("send('release_control')")
        pg.evaluate(f"setView('{view}')")
        pg.wait_for_timeout(6000)  # einige Sekunden akkumulieren lassen
        res = {}
        for key, sel in (("Kopfzeile", "#viewBtn, .chbtn"), ("Zaehlraten", "#rates .rate")):
            r = pg.evaluate(ROW_JS, sel)
            res[key] = (r["n"], max(r["tops"]) - min(r["tops"]) <= 2 and r["right"] <= r["vw"], r["right"], r["vw"])
        m = pg.evaluate("({hh: S.lastFrame.has_histogram, ch: S.lastFrame.channels, cch: S.lastFrame.corr_channels, "
                        "ref: S.lastFrame.corr_ref, cbw: S.lastFrame.corr_bin_ps, cx0: S.lastFrame.corr_x0_ps, "
                        "hint: $('chartHint').hidden ? '' : $('chartHint').textContent, rates: [...document.querySelectorAll('#rates .v')].map(e=>e.textContent)})")
        labels = pg.evaluate("[$('binning').selectedOptions[0].text, $('speed').selectedOptions[0].text, "
                             "$('ref_channel').selectedOptions[0].text, $('corr_binning').selectedOptions[0].text]")
        print("  Zentrierung (px, Karte links/rechts, erster/letzter Punkt):", pg.evaluate(CENTER_JS, view))
        print("  Fortschritt direkt unter Start-Zeile:", pg.evaluate(
            "(() => { const r = document.querySelector('.btnrow').getBoundingClientRect(), "
            "p = $('progress').getBoundingClientRect(); return [Math.round(p.top - r.bottom), "
            "$('progress').previousElementSibling.className]; })()"))
        pg.screenshot(path=str(OUT / f"screenshot-ui9-{name}.png"), full_page=False)
        print(f"{name}: {res}  Frame={m}  Binbreite/Tempo/Ref/g2-Bin={labels}")
        ctx.close()
    # Zustand zurücksetzen
    ctx = b.new_context(); pg = ctx.new_page(); pg.goto(URL); pg.wait_for_selector("#histPlot canvas")
    pg.evaluate(f"command('open_file', {{name: '{T3}'}})"); pg.wait_for_timeout(1500); ctx.close()
    b.close()
    print("JS-Fehler:", errors or "keine")
