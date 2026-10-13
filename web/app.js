/* Harp Mobil – Web-Client (ohne Build-Schritt, uPlot lokal eingebunden) */
"use strict";
// Kanalfarben: Blau, Amber-Orange, Grün, Rot (kein Pink); 5–8 für größere Geräte
const COLORS = ["#38bdf8", "#f5a623", "#34d399", "#ef4444", "#22d3ee", "#a78bfa", "#d9f99d", "#cbd5e1"];
const K0_COLOR = "#a78bfa";   // T2: Sync-Eingang als Datenkanal K0 (violett)
const chColor = (c) => (c === 0 ? K0_COLOR : COLORS[(c - 1) % COLORS.length]);
// Kanalnummern der Datei: T2 mit Sync-Ereignissen beginnt bei K0
const chanIds = (first, n) => Array.from({ length: n - first + 1 }, (_, i) => first + i);
const Y_FLOOR = 0.5;            // Nullzählungen werden auf diesen Wert geklemmt (log-Achse)
const AXIS = { stroke: "#9fb0c8", font: "11px system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
  labelFont: "600 11px system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
  grid: { stroke: "rgba(148,163,184,0.13)", width: 1 }, ticks: { stroke: "rgba(148,163,184,0.35)", width: 1, size: 4 } };
const SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹";
function pow10Label(v) {
  if (v == null || !(v > 0)) return "";                       // uPlot übergibt ausgefilterte Ticks als null
  const e = Math.round(Math.log10(v));
  if (Math.abs(v - 10 ** e) > 1e-9 * 10 ** e) return "";        // nur Dekaden beschriften
  if (e === 0) return "1"; if (e === 1) return "10";
  return "10" + (e < 0 ? "⁻" : "") + String(Math.abs(e)).split("").map((d) => SUP[d]).join("");
}
function fmtNum(v) { const a = Math.abs(v); return a >= 1e6 ? (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M" : a >= 1e3 ? (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k" : String(Math.round(v * 100) / 100); }
function fmtCount(v) { return v >= 1e4 ? v.toExponential(2).replace("e+", "e") : String(v); }
const $ = (id) => document.getElementById(id);
const token = new URLSearchParams(location.search).get("token") || "";
const S = { ws: null, status: null, mine: false, hidden: loadHidden(), view: ["hist", "trace", "corr"].includes(localStorage.getItem("harp.view")) ? localStorage.getItem("harp.view") : "hist",
  lp: {}, hist: null, trace: null, histKey: "",
  traceKey: "", tr: { t: [], r: [] }, lastT: -1, frames: 0, fpsT: performance.now(), fps: 0, cmdId: 0,
  lastFrame: null, pendingCtrl: null };

function loadHidden() { try { return new Set(JSON.parse(localStorage.getItem("harp.hidden") || "[]")); } catch (e) { return new Set(); } }
function saveHidden() { try { localStorage.setItem("harp.hidden", JSON.stringify([...S.hidden])); } catch (e) { /* privat */ } }
function toast(msg, err) {
  const t = $("toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._h); toast._h = setTimeout(() => (t.className = "toast"), err ? 4000 : 1800);
}
function fmtRate(r) {
  if (r >= 1e6) return [(r / 1e6).toFixed(2), "Mcps"];
  if (r >= 1e3) return [(r / 1e3).toFixed(1), "kcps"];
  return [r.toFixed(0), "cps"];
}
// Binbreite: nur die Breite anzeigen (intern bleibt der Binning-Code der Wert)
function fmtWidth(ps) {
  if (ps >= 1e6) return +(ps / 1e6).toPrecision(4) + " µs";
  if (ps >= 1000) return +(ps / 1000).toPrecision(4) + " ns";
  return +ps.toPrecision(4) + " ps";
}
function fmtSpeed(v) { return Number(v) === 1 ? "Echtzeit" : String(v).replace(".", ",") + "×"; }
// kompakte Zählrate: 40.0M, 13.9k, 850
function fmtCompact(r) {
  if (r >= 1e6) return (r / 1e6).toFixed(r >= 1e8 ? 0 : 1) + "M";
  if (r >= 1e3) return (r / 1e3).toFixed(r >= 1e5 ? 0 : 1) + "k";
  return r.toFixed(0);
}
function fmtBytes(b) { return b > 1e6 ? (b / 1e6).toFixed(2) + " MB" : (b / 1e3).toFixed(1) + " kB"; }

// ------------------------------------------------------------------ WebSocket
// Verbindungsüberwachung: Ping alle 5 s; kommt 10 s lang nichts (auch kein Frame), gilt die Verbindung
// als tot und wird neu aufgebaut. Beim Zurückkehren in den Vordergrund sofort prüfen bzw. neu verbinden.
const PING_MS = 5000, DEAD_MS = 10000;
const C = { lastRx: 0, everOpen: false, hadControl: false, retryT: 0 };
function setConnUI(on) {
  $("conn").className = "dot " + (on ? "on" : "off");
  $("connbar").hidden = on || !C.everOpen;
}
function dropSocket() {
  const ws = S.ws; S.ws = null;
  if (ws) { ws.onopen = ws.onclose = ws.onmessage = ws.onerror = null; try { ws.close(); } catch (e) { /* egal */ } }
}
function lostConnection() {
  if (S.mine) C.hadControl = true;
  S.mine = false;
  if (S.status) S.status.control = { held: false, mine: false };   // unbekannt, bis der Server wieder meldet
  updateCtrl(); setConnUI(false);
}
function reconnect(delay) {
  clearTimeout(C.retryT);
  C.retryT = setTimeout(connect, delay || 0);
}
function connect() {
  clearTimeout(C.retryT);
  dropSocket();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws${token ? "?token=" + encodeURIComponent(token) : ""}`);
  S.ws = ws;
  ws.onopen = () => { C.lastRx = Date.now(); C.everOpen = true; setConnUI(true); };
  ws.onclose = (e) => {
    if (S.ws !== ws) return;
    S.ws = null; lostConnection();
    if (e.code === 4401) { location.href = "/login"; return; }   // nicht (mehr) angemeldet
    reconnect(1500);   // automatische Wiederverbindung
  };
  ws.onmessage = (ev) => {
    C.lastRx = Date.now();
    const m = JSON.parse(ev.data);
    if (m.type === "frame") onFrame(m, ev.data.length);
    else if (m.type === "status") onStatus(m);
    else if (m.type === "ack") onAck(m);
    else if (m.type === "trace") { S.tr = { t: m.t.slice(-600), r: m.rates.slice(-600).map((r) => r.map((x) => x / 1000)) };
      S.lastT = m.t.length ? m.t[m.t.length - 1] : -1; }
  };
}
function watchdog() {
  const ws = S.ws;
  if (!ws || ws.readyState !== 1) return;
  if (Date.now() - C.lastRx > DEAD_MS) { lostConnection(); reconnect(0); return; }   // tote/hängende Verbindung
  ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
}
setInterval(watchdog, PING_MS);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  const ws = S.ws;
  if (!ws || ws.readyState > 1) { lostConnection(); reconnect(0); return; }
  if (ws.readyState !== 1) return;   // baut gerade auf
  const t = Date.now();              // Probe: antwortet der Server binnen 3 s? Sonst neu verbinden
  try { ws.send(JSON.stringify({ type: "ping", t })); } catch (e) { /* tot */ }
  setTimeout(() => { if (S.ws === ws && C.lastRx < t) { lostConnection(); reconnect(0); } }, 3000);
});
window.addEventListener("pageshow", (e) => { if (e.persisted) { lostConnection(); reconnect(0); } });
window.addEventListener("online", () => { if (!S.ws || S.ws.readyState !== 1) reconnect(0); });
function send(cmd, params) {
  if (!S.ws || S.ws.readyState !== 1) { toast("Keine Verbindung – wird wiederhergestellt …", true); reconnect(0); return; }
  S.ws.send(JSON.stringify({ type: "command", cmd, params: params || {}, id: ++S.cmdId }));
}
// Steuerbefehl: falls noch keine Steuerung, zuerst übernehmen
function command(cmd, params) {
  if (!S.mine) { S.pendingCtrl = [cmd, params]; send("acquire_control"); return; }
  send(cmd, params);
}
function onAck(m) {
  if (!m.ok) { toast(m.error || "Fehler", true); S.pendingCtrl = null; if (S.status) fillControls(S.status); return; }
  if (m.cmd === "acquire_control" && S.pendingCtrl) { const [c, p] = S.pendingCtrl; S.pendingCtrl = null; S.mine = true; send(c, p); return; }
  if (m.cmd !== "acquire_control" && m.cmd !== "release_control") toast("OK: " + m.cmd);
}

// ------------------------------------------------------------------ Status / Steuerelemente
function onStatus(st) {
  S.status = st; S.mine = !!(st.control && st.control.mine);
  if (C.hadControl) {   // erste Statusmeldung nach Wiederverbindung
    C.hadControl = false;
    if (!S.mine) toast(st.control && st.control.held ? "Verbindung war unterbrochen – ein anderes Gerät steuert jetzt"
      : "Verbindung war unterbrochen – Steuerung wurde abgegeben", true);
  }
  const rs = $("runstate");
  rs.textContent = st.running ? "läuft" : "gestoppt"; rs.className = "pill " + (st.running ? "run" : "stop");
  updateCtrl(); fillControls(st);
  if (st.error) toast(st.error, !st.error.startsWith("Ende"));
}
function updateCtrl() {
  const st = S.status, b = $("ctrlBtn"), other = !S.mine && !!(st && st.control && st.control.held);
  b.classList.toggle("mine", S.mine); b.classList.toggle("viewer", other);
  b.textContent = S.mine ? "Sie steuern" : (other ? "Nur Anzeige" : "Steuerung übernehmen");
  b.title = other ? "Ein anderes Gerät steuert – dieses Gerät zeigt nur an" : "Steuerung übernehmen/abgeben";
  const li = $("lockinfo"); li.classList.toggle("viewer", other);
  li.textContent = S.mine ? "Sie haben die Steuerung" :
    (other ? "Nur Anzeige – ein anderes Gerät steuert" : "Änderung übernimmt die Steuerung automatisch");
}
function fillControls(st) {
  const s = st.settings, info = st.info || {};
  const fsel = $("file");
  const names = (st.files || []).map((f) => f.name);
  if (fsel.dataset.k !== names.join("|")) {
    fsel.innerHTML = names.map((n, i) => `<option value="${n}">${n} (${st.files[i].size_mb} MB)</option>`).join("");
    fsel.dataset.k = names.join("|");
  }
  fsel.value = st.file || "";
  const res = info.resolution_ps || 1, maxb = Math.min(info.max_binning ?? 12, 15);
  const bsel = $("binning"), bk = res + "|" + maxb;
  if (bsel.dataset.k !== bk) {
    let h = ""; for (let b = 0; b <= maxb; b++) { const w = res * 2 ** b;
      h += `<option value="${b}">${fmtWidth(w)}</option>`; }
    bsel.innerHTML = h; bsel.dataset.k = bk;
  }
  const lsel = $("hist_len_code");
  if (!lsel.options.length) for (let c = 0; c <= 7; c++) lsel.add(new Option(String(1024 * 2 ** c) + " Bins", c));
  for (const k of ["refresh_ms", "speed", "binning", "hist_len_code", "mode", "display_points"]) {
    const el = $(k); if (document.activeElement !== el) el.value = String(s[k]);
  }
  if ($("speed").value !== String(s.speed)) { $("speed").add(new Option(fmtSpeed(s.speed), String(s.speed))); $("speed").value = String(s.speed); }
  const nch = info.num_channels || 4, first = info.first_channel ?? 1, ids = chanIds(first, nch), rsel = $("ref_channel");
  if (rsel.dataset.n !== ids.join(",")) {
    rsel.innerHTML = ids.map((c) => `<option value="${c}">K${c}</option>`).join("");
    rsel.dataset.n = ids.join(",");
  }
  if (document.activeElement !== rsel) rsel.value = String(s.ref_channel ?? 1);
  const cbase = info.mode === "T3" ? 1 : res, csel = $("corr_binning"), ck = String(cbase);
  if (csel.dataset.k !== ck) {
    let h = ""; for (let b = 0; b <= 24; b++) { const w = cbase * 2 ** b; if (w >= 1 && w <= 1e7) h += `<option value="${b}">${fmtWidth(w)}</option>`; }
    csel.innerHTML = h; csel.dataset.k = ck;
  }
  if (document.activeElement !== csel) csel.value = String(s.corr_binning);
  if (document.activeElement !== $("window_frames")) $("window_frames").value = s.window_frames;
  $("window_frames").disabled = s.mode !== "sliding";
  $("loop").checked = s.loop;
  buildChanButtons(ids);
  renderInfo();
}
function bindControls() {
  $("bStart").onclick = () => command("start");
  $("bStop").onclick = () => command("stop");
  $("bReset").onclick = () => { command("reset"); S.tr = { t: [], r: [] }; };
  $("bRewind").onclick = () => { command("rewind"); S.tr = { t: [], r: [] }; };
  $("file").onchange = (e) => { command("open_file", { name: e.target.value }); S.tr = { t: [], r: [] }; };
  const num = { refresh_ms: parseInt, speed: parseFloat, binning: parseInt, hist_len_code: parseInt, display_points: parseInt, window_frames: parseInt };
  for (const k of Object.keys(num)) $(k).addEventListener("change", (e) => command("set", { [k]: num[k](e.target.value) }));
  $("mode").onchange = (e) => command("set", { mode: e.target.value });
  $("ref_channel").onchange = (e) => command("set", { ref_channel: parseInt(e.target.value) });
  $("corr_binning").onchange = (e) => command("set", { corr_binning: parseInt(e.target.value) });
  $("loop").onchange = (e) => command("set", { loop: e.target.checked });
  $("viewBtn").onclick = (e) => {   // Tippen auf ein Segment wählt es; sonst zur nächsten Ansicht
    const sp = e.target.closest("span[data-v]"), order = ["hist", "corr", "trace"];
    setView(sp ? sp.dataset.v : order[(order.indexOf(S.view) + 1) % 3]);
  };
  $("ctrlBtn").onclick = () => send(S.mine ? "release_control" : "acquire_control");
}
// Ansicht im großen Diagramm: Histogramm (log) oder Zählraten-Zeitverlauf (linear, kcps)
function setView(v) {
  S.view = v; try { localStorage.setItem("harp.view", v); } catch (e) { /* privat */ }
  document.querySelectorAll("#viewBtn span").forEach((x) => x.classList.toggle("on", x.dataset.v === v));
  $("histPlot").hidden = v !== "hist"; $("tracePlot").hidden = v !== "trace"; $("corrPlot").hidden = v !== "corr";
  $("readout").innerHTML = "&nbsp;";
  updateHint();
  onResize(0);
}
// Kanal-Toggles: blenden Kurven nur in der Anzeige ein/aus (kein Steuerbefehl, auch für Zuschauer)
function buildChanButtons(ids) {
  const cb = $("chanbox");
  if (cb.dataset.n === ids.join(",")) return;
  cb.dataset.n = ids.join(",");
  cb.classList.toggle("many", ids.length >= 5);   // 5 Tasten: etwas schmaler, Kopfzeile bleibt einzeilig
  cb.innerHTML = ids.map((c) =>
    `<button type="button" class="chbtn" data-ch="${c}" style="--c:${chColor(c)}" aria-pressed="true">K${c}</button>`).join("");
  cb.querySelectorAll(".chbtn").forEach((b) => b.addEventListener("click", () => {
    const c = +b.dataset.ch;
    if (S.hidden.has(c)) S.hidden.delete(c); else S.hidden.add(c);
    saveHidden(); applyVisibility();
  }));
  applyVisibility();
}
function applyVisibility() {
  document.querySelectorAll(".chbtn").forEach((b) => {
    const off = S.hidden.has(+b.dataset.ch); b.classList.toggle("off", off); b.setAttribute("aria-pressed", String(!off));
  });
  for (const p of Object.values(S.lp)) if (p.u) p.ch.forEach((c, i) => { const show = !S.hidden.has(c);
    if (p.u.series[i + 1].show !== show) p.u.setSeries(i + 1, { show }); });
  if (S.trace) for (let i = 1; i < S.trace.series.length; i++) { const show = !S.hidden.has(S.trCh[i - 1]);
    if (S.trace.series[i].show !== show) S.trace.setSeries(i, { show }); }
  document.querySelectorAll(".rate[data-ch]").forEach((t) => t.classList.toggle("dim", S.hidden.has(+t.dataset.ch)));
}

// ------------------------------------------------------------------ Plots
function plotWidth(el) { return Math.max(240, Math.floor(el.clientWidth)); }
function histHeight() {
  const W = window.innerWidth, H = window.innerHeight;
  const head = document.querySelector(".hist-card .card-head"), top = document.querySelector(".topbar");
  const used = (head ? head.offsetHeight : 40) + 30;             // Kopfzeile + Messwertzeile
  if (W > H && H < 560) return Math.max(160, H - used - 12);     // Handy quer: fast volle Höhe
  if (W >= 1000) return Math.max(320, Math.min(720, H - (top ? top.offsetHeight : 50) - used - 40));
  return Math.round(Math.max(260, Math.min(H * 0.56, W * 1.15)));   // Handy hoch
}
function histRange(u, mn, mx) {
  const top = Math.max(10, (mx || 1) * 1.6);
  return [Y_FLOOR, 10 ** Math.ceil(Math.log10(top))];
}
// Log-Diagramme: 'hist' (Zeit seit Sync: T3 dtime, T2 seit letztem Sync) und 'corr' (Kreuzkorrelation)
const LOGPLOT = {
  hist: { el: "histPlot", xlab: (m) => (m.hist_kind === "ref" ? `t nach K${m.corr_ref} / ns` : "t nach Sync / ns"),
          sym: "t", suffix: () => "" },
  corr: { el: "corrPlot", xlab: (m) => `τ = t(Kn) − t(K${m.corr_ref}) / ns`, sym: "τ", suffix: (m) => "×K" + m.corr_ref },
};
function makeLog(kind, chans, m) {
  const def = LOGPLOT[kind], el = $(def.el); el.innerHTML = "";
  const p = S.lp[kind] = { ch: chans.slice(), raw: null, key: "", m };
  const series = [{}];
  chans.forEach((c) => series.push({ label: "K" + c, stroke: chColor(c), width: 1.6, show: !S.hidden.has(c),
    points: { show: false }, spanGaps: true }));
  const narrow = chartBox().clientWidth < 500, ysz = narrow ? 34 : 40, corr = kind === "corr";
  // Korrelogramm zentriert: rechts derselbe Rand wie die y-Achse links, x-Bereich symmetrisch um τ = 0
  const xr = corr ? { range: () => { const W = -(S.lp.corr ? S.lp.corr.m.corr_x0_ps : m.corr_x0_ps) / 1000; return [-W, W]; } }
    : { range: (u, mn, mx) => (mn == null || mn === mx ? [0, 1] : [mn, mx]) };   // Daten über volle Breite
  p.u = new uPlot({ width: plotWidth(chartBox()), height: histHeight(), series, pxAlign: false,
    padding: [8, ysz, 0, 0],   // rechts so breit wie die y-Achse links → Zeichenfläche zentriert
    legend: { show: false },
    cursor: { drag: { x: true, y: false }, points: { size: 7 } },
    scales: { x: { time: false, ...xr }, y: { distr: 3, log: 10, range: histRange } },
    axes: [{ ...AXIS, label: def.xlab(m), labelSize: 16, labelGap: 0, size: 30, gap: 3,
             values: (u, sp) => sp.map((v) => String(+v.toFixed(2))) },
           { ...AXIS, size: ysz, gap: 3, values: (u, sp) => sp.map(pow10Label) }],
    hooks: { setCursor: [(u) => logReadout(kind, u)] } }, [[]], el);
  return p;
}
function logReadout(kind, u) {
  if (S.view !== kind) return;
  const p = S.lp[kind], i = u.cursor.idx, r = $("readout");
  if (i == null || !p || !p.raw) { r.innerHTML = "&nbsp;"; return; }
  const def = LOGPLOT[kind];
  const parts = [`<b>${def.sym} = ${u.data[0][i].toFixed(3)} ns</b>`];
  p.ch.forEach((c, k) => { if (!S.hidden.has(c) && p.raw[k])
    parts.push(`<span style="color:${chColor(c)}">K${c}${def.suffix(p.m)} ${fmtCount(p.raw[k][i])}</span>`); });
  r.innerHTML = parts.join(" · ");
}
function drawLog(kind, chans, rows, x0ps, bwps, m) {
  let p = S.lp[kind];
  const key = chans.join(",") + "|" + m.corr_ref + "|" + m.hist_kind + "|" + (kind === "corr" ? m.corr_x0_ps : "");
  if (!p || p.key !== key) { p = makeLog(kind, chans, m); p.key = key; }
  p.m = m;
  const n = rows.length ? rows[0].length : 0, bw = bwps / 1000, x0 = x0ps / 1000;
  const x = new Float64Array(n); for (let i = 0; i < n; i++) x[i] = x0 + (i + 0.5) * bw;
  p.raw = rows;
  const ys = rows.map((row) => row.map((v) => (v > Y_FLOOR ? v : Y_FLOOR)));   // 0 → Boden, Linie bleibt geschlossen
  p.u.setData([x, ...ys]);
  if (p.u.cursor.idx != null) logReadout(kind, p.u);
}
function drawHist(m) {
  drawLog("hist", m.channels, m.hist, 0, m.bin_width_ps, m);
  if (m.corr) drawLog("corr", m.corr_channels, m.corr, m.corr_x0_ps, m.corr_bin_ps, m);
  S.hist = S.lp.hist && S.lp.hist.u;
  updateHint();
}
function updateHint() {
  const m = S.lastFrame, h = $("chartHint");
  let txt = "";
  if (m && S.view === "hist" && !m.has_histogram)
    txt = "Kein Histogramm verfügbar – bitte „Zeit“ oder „g²“ wählen.";
  else if (m && S.view === "corr" && !m.corr) txt = "Korrelation mit dieser Datenquelle nicht verfügbar.";
  else if (m && S.view === "corr" && m.corr && !m.corr_channels.length) txt = "Keine Kanäle außer dem Referenzkanal.";
  h.textContent = txt; h.hidden = !txt;
}
function makeTrace(ids) {
  const el = $("tracePlot"); el.innerHTML = "";
  const series = [{}];
  S.trCh = ids.slice();
  ids.forEach((c) => series.push({ label: "K" + c, stroke: chColor(c), width: 1.6, points: { show: false },
    show: !S.hidden.has(c) }));
  S.trace = new uPlot({ width: plotWidth(chartBox()), height: histHeight(), series, pxAlign: false, padding: [8, 52, 0, 0],   // = y-Achse 38 + Beschriftung 14
    legend: { show: false }, cursor: { points: { size: 6 } },
    scales: { x: { time: false, range: (u, mn, mx) => (mn === mx ? [mn - 1, mx + 1] : [mn, mx]) },
              y: { range: (u, mn, mx) => [0, Math.max(1, (mx || 0) * 1.1)] } }, hooks: { setCursor: [traceReadout] },
    axes: [{ ...AXIS, label: "t / s", labelSize: 16, labelGap: 0, size: 28, gap: 3, values: (u, sp) => sp.map((v) => String(+v.toFixed(1))) },
           { ...AXIS, label: "kcps", labelSize: 14, labelGap: 0, size: 38, gap: 3, values: (u, sp) => sp.map(fmtNum) }] }, [[]], el);
}
function drawTrace(m) {
  const n = m.rates.length, ids = chanIds(m.first_channel ?? 1, (m.first_channel ?? 1) + n - 1);
  if (S.traceKey !== ids.join(",") || !S.trace) { makeTrace(ids); S.traceKey = ids.join(","); }
  if (m.t_file_s < S.lastT) S.tr = { t: [], r: [] };      // Datei neu begonnen
  S.lastT = m.t_file_s;
  S.tr.t.push(m.t_file_s); S.tr.r.push(m.rates.map((r) => r / 1000));
  const MAX = 600; if (S.tr.t.length > MAX) { S.tr.t.splice(0, S.tr.t.length - MAX); S.tr.r.splice(0, S.tr.r.length - MAX); }
  const cols = [S.tr.t]; for (let i = 0; i < n; i++) cols.push(S.tr.r.map((r) => r[i]));
  S.trace.setData(cols);
}
// Breite des sichtbaren Diagrammbereichs (auch wenn die andere Ansicht gerade versteckt ist)
function chartBox() { return $({ hist: "histPlot", trace: "tracePlot", corr: "corrPlot" }[S.view]); }
function traceReadout(u) {
  if (S.view !== "trace") return;
  const i = u.cursor.idx, r = $("readout");
  if (i == null || !u.data[0].length) { r.innerHTML = "&nbsp;"; return; }
  const parts = [`<b>t = ${u.data[0][i].toFixed(2)} s</b>`];
  for (let k = 1; k < u.series.length; k++) if (!S.hidden.has(S.trCh[k - 1]) && u.data[k])
    parts.push(`<span style="color:${chColor(S.trCh[k - 1])}">K${S.trCh[k - 1]} ${(u.data[k][i] ?? 0).toFixed(1)} kcps</span>`);
  r.innerHTML = parts.join(" · ");
}
function drawRates(m) {
  // T2 mit K0: Sync-Eingang ist Datenkanal K0 (gemessene Rate), sonst Kachel „Sync“
  const f = m.first_channel ?? 1, tiles = f === 0 ? [] : [["Sync", m.sync_rate, "#cbd5e1", null]];
  m.rates.forEach((r, i) => tiles.push(["K" + (f + i), r, chColor(f + i), f + i]));
  const box = $("rates"); box.style.setProperty("--n", Math.min(tiles.length, 5));
  box.innerHTML = tiles.map(([k, r, c, ch]) =>
    `<div class="rate${ch != null && S.hidden.has(ch) ? " dim" : ""}"${ch != null ? ` data-ch="${ch}"` : ""} style="border-color:${ch != null ? c + "66" : ""}" title="${fmtRate(r).join(" ")}"><div class="k"><i style="background:${c}"></i>${k}</div><div class="v" style="color:${ch != null ? c : ""}">${fmtCompact(r)}</div></div>`).join("");
  $("accinfo").textContent = (m.mode === "sliding" ? "Fenster " : "akkumuliert ") + m.acc_time_s.toFixed(2) + " s";
}
function renderInfo() {
  const st = S.status || {}, info = st.info || {}, m = S.lastFrame;
  const rows = [["Quelle", st.source + (info.record_type ? " · " + info.record_type : "")], ["Datei", st.file || "–"],
    ["Modus", (info.mode || "–") + ", " + (info.num_channels || "?") + " Kanäle, Sync " + fmtRate(info.sync_rate_hz || 0).join(" ")],
    ["Auflösung", (info.resolution_ps || "–") + " ps · Dauer " + (info.duration_s || 0).toFixed(2) + " s"]];
  if (m) rows.push(["Frame", `#${m.seq} · t = ${m.t_file_s.toFixed(2)} s · ${S.fps.toFixed(1)} fps`],
    ["Anzeige", `${m.n_bins} von ${m.n_bins_full} Bins (×${m.rebin_factor}) à ${(m.bin_width_ps / 1000).toFixed(3)} ns`],
    ["Datenmenge", `${fmtBytes(S.lastBytes || 0)}/Frame · ${m.payload_kBps.toFixed(1)} kB/s · Server ${m.calc_ms} ms`]);
  rows.push(["Clients", String(st.clients ?? "–")]);
  if (st.version) $("appver").textContent = "ExSE Harp Mobil " + st.version;
  $("infolist").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  if (m) $("progress").firstElementChild.style.width = (m.progress * 100).toFixed(1) + "%";
}
let infoTick = 0;
function onFrame(m, len) {
  S.lastFrame = m; S.lastBytes = len; S.frames++;
  const now = performance.now(); if (now - S.fpsT > 1000) { S.fps = S.frames * 1000 / (now - S.fpsT); S.frames = 0; S.fpsT = now; }
  drawHist(m); drawRates(m); drawTrace(m);
  if (++infoTick % 3 === 0) renderInfo();
}
let resizeT = 0;
function onResize(delay) {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => {
    const w = plotWidth(chartBox()), h = histHeight();
    for (const p of Object.values(S.lp)) if (p.u) p.u.setSize({ width: w, height: h });
    if (S.trace) S.trace.setSize({ width: w, height: h });
  }, typeof delay === "number" ? delay : 80);
}
window.addEventListener("resize", () => onResize());
window.addEventListener("orientationchange", () => onResize());
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost"))
  navigator.serviceWorker.register("/sw.js").catch(() => {});
bindControls(); setView(S.view); connect();
