"""Screenshots Kopfzeile + Infobereich (Version) für 390 und 360 px. Aufruf: python tools/screenshot_version.py"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
OUT = Path(__file__).resolve().parent.parent
ROW = """() => { const r = [...document.querySelectorAll('#viewBtn, .chbtn')].map(e => e.getBoundingClientRect());
  return { einzeilig: Math.max(...r.map(x => x.top)) - Math.min(...r.map(x => x.top)) <= 2,
           rechts: Math.round(Math.max(...r.map(x => x.right))), breite: innerWidth,
           reihenfolge: [...document.querySelectorAll('#viewBtn span')].map(e => e.textContent) }; }"""
with sync_playwright() as p:
    b = p.chromium.launch()
    errors = []
    for w, h in ((390, 844), (360, 780)):
        ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=3, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL)
        pg.wait_for_function("S.lastFrame && $('appver').textContent.includes('1.')", timeout=15000)
        pg.wait_for_timeout(1500)
        print(w, pg.evaluate(ROW), "| Titel:", pg.title(), "| Info:", pg.text_content("#appver"))
        if w == 390:
            pg.locator(".topbar").screenshot(path=str(OUT / "screenshot-v100-kopf.png"))
            hb = pg.locator(".hist-card .card-head").bounding_box(); tb = pg.locator(".topbar").bounding_box()
            pg.screenshot(path=str(OUT / "screenshot-v100-header.png"),
                          clip={"x": 0, "y": 0, "width": w, "height": hb["y"] + hb["height"] + 8})
            pg.locator("section.info").scroll_into_view_if_needed()
            pg.locator("section.info").screenshot(path=str(OUT / "screenshot-v100-info.png"))
        ctx.close()
    b.close()
    print("JS-Fehler:", errors or "keine")
