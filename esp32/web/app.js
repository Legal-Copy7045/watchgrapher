// WatchGrapher standalone -- browser side.
//
// The device measures and logs; this page draws, and owns the collection
// (the desktop app's collection.json schema, stored on the device as-is so
// fields this page does not know about survive a round trip).
"use strict";

const POSITIONS = ["Dial up", "Dial down", "Crown down", "Crown left", "Crown right", "Crown up"];
const STD_BPH = [12000, 14400, 16200, 18000, 19800, 21600, 25200, 28800, 36000, 43200];

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const fin = (v) => typeof v === "number" && isFinite(v);
const f = (v, d = 1, plus = false) => (fin(v) ? (plus && v > 0 ? "+" : "") + v.toFixed(d) : "--");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("wg." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("wg." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// The desktop writes NaN for missing numbers (Python's json), which JSON.parse
// rejects. Turn bare NaN / Infinity tokens -- never ones inside strings -- into null.
function parseLenient(text) {
  return JSON.parse(text.replace(/"(?:[^"\\]|\\.)*"|-?\bInfinity\b|\bNaN\b/g, (m) => (m[0] === '"' ? m : "null")));
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return parseLenient(await r.text());
}
async function post(url, fields) {
  const body = new URLSearchParams({ ...fields, now: Date.now() });
  const r = await fetch(url, { method: "POST", body });
  const j = await r.json().catch(() => ({ ok: r.ok }));
  if (j.ok === false) throw new Error(j.msg || "Request failed");
  return j;
}
const cmd = (action, fields = {}) => post("/api/cmd", { action, ...fields });

function confirmBox(msg) {
  const d = $("#dlg");
  $("#dlgMsg").textContent = msg;
  return new Promise((res) => {
    d.addEventListener("close", () => res(d.returnValue === "ok"), { once: true });
    d.showModal();
  });
}
let bannerTimer = 0;
function banner(msg, ms = 4000) {
  const b = $("#banner");
  b.textContent = msg;
  b.hidden = !msg;
  clearTimeout(bannerTimer);
  if (msg && ms) bannerTimer = setTimeout(() => (b.hidden = true), ms);
}
const uid = () => [...crypto.getRandomValues(new Uint8Array(6))].map((b) => b.toString(16).padStart(2, "0")).join("");
function isoLocal(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// ---------------------------------------------------------------- charts

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function setupCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.font = "11px system-ui, sans-serif";
  return { g, w, h };
}

function niceTicks(lo, hi, n = 5) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

// series: [{pts: [[x,y],...], color, kind: 'line'|'dot', axis: 'l'|'r'}]
function plot(cv, series, o = {}) {
  const { g, w, h } = setupCanvas(cv);
  const pad = { l: 40, r: o.right ? 40 : 18, t: 8, b: 20 };
  const all = (ax) => series.filter((s) => (s.axis || "l") === ax).flatMap((s) => s.pts.filter((p) => fin(p[0]) && fin(p[1])));
  const pl = all("l"), pr = all("r"), px = pl.concat(pr);
  const ext = (a, i, fx) => {
    if (fx) return fx;
    if (!a.length) return [0, 1];
    let lo = Math.min(...a.map((p) => p[i])), hi = Math.max(...a.map((p) => p[i]));
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const m = (hi - lo) * 0.08;
    return [lo - (i ? m : 0), hi + (i ? m : 0)];
  };
  const [x0, x1] = ext(px, 0, o.x), [y0, y1] = ext(pl, 1, o.y), [r0, r1] = ext(pr, 1, o.yr);
  const X = (x) => pad.l + ((x - x0) / (x1 - x0)) * (w - pad.l - pad.r);
  const Y = (y, a = "l") => {
    const [lo, hi] = a === "r" ? [r0, r1] : [y0, y1];
    return h - pad.b - ((y - lo) / (hi - lo)) * (h - pad.t - pad.b);
  };
  g.strokeStyle = css("--grid");
  g.fillStyle = css("--muted");
  g.lineWidth = 1;
  g.textAlign = "right";
  for (const t of niceTicks(y0, y1, 4)) {
    const y = Y(t);
    g.beginPath(); g.moveTo(pad.l, y); g.lineTo(w - pad.r, y); g.stroke();
    g.fillText(o.yfmt ? o.yfmt(t) : t, pad.l - 4, y + 4);
  }
  if (o.right) {
    g.textAlign = "left";
    for (const t of niceTicks(r0, r1, 4)) g.fillText(t, w - pad.r + 4, Y(t, "r") + 4);
  }
  g.textAlign = "center";
  for (const t of niceTicks(x0, x1, Math.max(3, Math.floor(w / 80)))) g.fillText(o.xfmt ? o.xfmt(t) : t, X(t), h - 5);
  for (const ln of o.hlines || []) {
    g.strokeStyle = ln.color; g.setLineDash([4, 4]);
    g.beginPath(); g.moveTo(pad.l, Y(ln.y, ln.axis)); g.lineTo(w - pad.r, Y(ln.y, ln.axis)); g.stroke();
    g.setLineDash([]);
  }
  for (const s of series) {
    g.strokeStyle = g.fillStyle = s.color;
    g.lineWidth = s.width || 1.6;
    if (s.kind === "dot") {
      for (const [x, y] of s.pts) if (fin(x) && fin(y)) { g.beginPath(); g.arc(X(x), Y(y, s.axis), s.r || 2, 0, 7); g.fill(); }
    } else {
      g.beginPath();
      let pen = false;
      for (const [x, y] of s.pts) {
        if (!fin(x) || !fin(y)) { pen = false; continue; }
        pen ? g.lineTo(X(x), Y(y, s.axis)) : g.moveTo(X(x), Y(y, s.axis));
        pen = true;
      }
      g.stroke();
    }
  }
  return { X, Y };
}

// ---------------------------------------------------------------- state

let coll = { version: 1, watches: [] };
let calibers = null;          // rows from /calibers.json
let calByKey = new Map();
let settings = {};
let lastSeq = -1;
let histSeq = -1;             // newest measurement already in hist
let lastM = null;
let lastState = null;
let hist = [];                // [[t, rate, amp, be]]
let curWatchId = store.get("watch", "");
let session = store.get("session", {});   // position -> reading

async function loadCalibers() {
  if (calibers) return calibers;
  calibers = await getJSON("/calibers.json");
  calByKey = new Map(calibers.map((r) => [r[0], r]));
  return calibers;
}
const calLabel = (r) => (r ? `${r[1]} ${r[2]}` : "");
function calInfo(r) {
  if (!r) return "";
  return `${calLabel(r)} &middot; ${r[3] ? r[3] + " bph" : "bph auto"} &middot; lift ${r[4]}&deg;` +
    (r[5] ? ` &middot; full wind ${r[5]}-${r[6]}&deg;` : "");
}
const watchById = (id) => coll.watches.find((w) => w.id === id);
const watchLabel = (w) => (w ? (w.nickname || [w.brand, w.model].filter(Boolean).join(" ") || "Unnamed watch") : "");

// ---------------------------------------------------------------- tabs

const charts = {};
function showTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + name));
  store.set("tab", name);
  if (name === "reserve") refreshPrList();
  if (name === "watches") renderWatches();
  if (name === "settings") loadSettings();
  if (name === "positions") renderPositions();
  if (name === "measure") redrawMeasure();
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------------------------------------------------------------- polling

let pollFail = 0;
let storageWasBusy = false;
async function poll() {
  try {
    const st = await getJSON(`/api/state?since=${lastSeq}&now=${Date.now()}&tzo=${new Date().getTimezoneOffset()}`);
    pollFail = 0;
    $("#conn").classList.add("ok");
    lastState = st;
    if (st.m !== undefined) {
      lastM = st.m;
      lastSeq = st.seq;
      // Forcing a resend (lastSeq = -1) repeats the current measurement; it
      // must not add a second point to the history.
      if (st.m && st.seq > histSeq) {
        histSeq = st.seq;
        hist.push(st.m.ok ? [st.up, st.m.rate, st.m.amp, st.m.be] : [st.up, NaN, NaN, NaN]);
        if (hist.length > 900) hist.shift();
      }
      redrawMeasure();
    }
    renderStatus(st);
    // First start after installing: storage formats in the background.
    if (st.storage === false) {
      storageWasBusy = true;
      banner("Preparing storage -- the first start after installing takes a few minutes. Measuring works already; watches and logs appear when it's done.", 0);
    } else if (st.storage && storageWasBusy) {
      storageWasBusy = false;
      banner("Storage ready.");
      loadCollection();
      refreshPrList();
    }
  } catch {
    if (++pollFail > 2) {
      $("#conn").classList.remove("ok");
      banner("Lost contact with the device -- reconnecting...", 0);
    }
  }
  setTimeout(poll, pollFail ? Math.min(5000, 700 * pollFail) : 700);
}

// ---------------------------------------------------------------- measure

function tile(id, val, sub, cls = "") {
  const t = $(id);
  $(".val", t).textContent = val;
  if (sub !== undefined) $(".sub", t).textContent = sub;
  t.className = "tile " + cls;
}

function renderStatus(st) {
  const listening = st.listening;
  const b = $("#btnListen");
  b.textContent = listening ? "Listening" : "Listen";
  b.classList.toggle("on", listening);
  b.classList.toggle("primary", !listening);
  const lvl = Math.max(0, Math.min(1, (st.level + 60) / 60));
  const bar = $("#meterBar");
  bar.style.width = st.capturing ? (lvl * 100).toFixed(0) + "%" : "0";
  bar.classList.toggle("hot", st.clips > 0);
  $("#gainTxt").textContent = `${st.src === "sim" ? "SIM " : st.src === "usb" ? "USB " : ""}${st.agc ? "auto " : ""}${f(st.gain, 0)} dB`;
  // What the input is doing: a USB mic's name and format, or why there is no audio.
  const inp = $("#inputInfo");
  inp.textContent = st.input || "";
  inp.className = "small " + (st.input_ready === false ? "warnText" : "muted");
  if (st.input_ready === false && st.capturing && !lastM) $("#msg").textContent =
    st.src === "usb" ? "Waiting for a USB microphone on the board's USB port." : "No audio from the input.";
  if (!st.capturing && !st.m) tile("#tRate", "--");

  // Timed run
  const r = st.run;
  const busy = r.state === "settle" || r.state === "measure";
  $("#btnRun").disabled = busy;
  $("#btnRunStop").disabled = !busy;
  let pct = 0, txt = "";
  if (r.state === "settle") {
    pct = 0;
    txt = `Settling ${f(Math.max(0, r.settle - r.elapsed), 0)} s`;
  } else if (r.state === "measure") {
    pct = Math.min(100, (100 * (r.measured || 0)) / r.duration);
    txt = `Measuring ${f(r.measured, 0)} / ${f(r.duration, 0)} s` + (r.position ? ` -- ${r.position}` : "");
  } else if (r.state === "done") {
    pct = 100;
    txt = "Finished";
  }
  $("#runBar").style.width = pct + "%";
  $("#runState").textContent = txt;
  renderRunResult(r);

  // Power reserve
  renderPrLive(st.pr);
}

let shownResult = "";
function renderRunResult(r) {
  const box = $("#runResult");
  if (r.state !== "done" || !r.result) {
    box.hidden = true;
    shownResult = "";
    return;
  }
  const key = JSON.stringify(r.result);
  if (key === shownResult) return;
  shownResult = key;
  const x = r.result;
  box.hidden = false;
  const w = watchById(x.watch_id);
  box.innerHTML = x.ok
    ? `<div class="result"><b>${esc(x.position || "Result")}</b> ${w ? "&middot; " + esc(watchLabel(w)) : ""}<br>
        Rate <b>${f(x.rate, 1, true)}</b> &plusmn;${f(x.rate_ci, 1)} s/day &middot; amplitude <b>${f(x.amplitude, 0)}</b>&deg;
        &middot; beat error <b>${f(x.beat_error, 1)}</b> ms &middot; ${x.bph} bph
        <div class="small muted">${f(x.seconds, 0)} s measured, ${x.windows} readings. ${esc(x.msg === "OK" ? "" : x.msg)}</div></div>
       <div class="row">
         ${x.position ? `<button id="rrPos" class="primary">Use for ${esc(x.position)}</button>` : ""}
         <button id="rrSave"${x.position ? "" : ' class="primary"'}>Save to watch</button>
         <button id="rrDiscard">Discard</button></div>`
    : `<div class="result">${esc(x.msg)}</div><div class="row"><button id="rrDiscard">OK</button></div>`;
  const reading = { position: x.position || "Dial up", wind: x.wind || "", rate: x.rate, amplitude: x.amplitude, beat_error: x.beat_error };
  $("#rrDiscard").onclick = () => cmd("run_clear");
  if ($("#rrPos")) $("#rrPos").onclick = async () => {
    session[x.position] = reading;
    store.set("session", session);
    await cmd("run_clear");
    showTab("positions");
  };
  if ($("#rrSave")) $("#rrSave").onclick = async () => {
    if (!(await saveTest([reading], x.lift_angle, x.caliber_key, ""))) return;
    await cmd("run_clear");
  };
}

function redrawMeasure() {
  const m = lastM;
  if (!m) return;
  if (m.ok) {
    tile("#tRate", f(m.rate, 1, true), fin(m.ci) ? `±${f(m.ci, 1)} s/day` : "s/day");
    const ampCls = !fin(m.amp) ? "stale" : m.amp < 180 ? "bad" : m.amp < 220 ? "warn" : "";
    tile("#tAmp", f(m.amp, 0), fin(m.amp_iqr) ? `deg · IQR ${f(m.amp_iqr, 0)}` : "deg", ampCls);
    tile("#tBe", f(m.be, 1), "ms", m.be > 1.2 ? "bad" : m.be > 0.6 ? "warn" : "");
    tile("#tBph", m.bph ? m.bph : "--", m.nominal && m.nominal !== m.bph ? `vs ${m.nominal} set` : "bph", m.nominal !== m.bph ? "warn" : "");
  } else {
    for (const id of ["#tRate", "#tAmp", "#tBe", "#tBph"]) $(id).classList.add("stale");
  }
  const msg = $("#msg");
  msg.textContent = m.msg === "OK" ? `${m.beats} beats over ${f(m.dur, 0)} s · SNR ${f(m.snr, 0)} dB` : m.msg;
  msg.className = "msg" + (m.msg === "OK" ? "" : " warn");

  if (!$("#tab-measure").classList.contains("on")) return;
  drawTrace(m);
  drawShape(m);
  drawHist();
  $("#diag").innerHTML = [
    ["Beats", m.beats], ["Window", f(m.dur, 1) + " s"], ["SNR", f(m.snr, 1) + " dB"], ["Template match", f(m.q, 2)],
    ["Noises per beat", f(3 + (m.extra || 0), 1)], ["Beats with amplitude", f(100 * (m.valid || 0), 0) + "%"],
    ["1st-3rd noise", f(m.dt_ms, 2) + " ms"], ["Raw beat rate", f(m.raw_bph, 1)],
    ["Tick/tock anchor fix", f(m.par_ms, 2) + " ms (seen " + f(m.par_seen_ms, 2) + ")"],
    ["Lift angle", f(m.lift, 1) + "°"], ["Analysis time", f(m.an_ms, 0) + " ms"],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
}

function drawTrace(m) {
  const win = +$("#traceWin").value;
  const tr = m.trace || [];
  const wrap = (d) => ((((d + win / 2) % win) + win) % win) - win / 2;
  const a = tr.filter((p) => !p[2]).map((p) => [wrap(p[0]), p[1]]);
  const b = tr.filter((p) => p[2]).map((p) => [wrap(p[0]), p[1]]);
  plot($("#cvTrace"), [
    { pts: a, color: css("--c1"), kind: "dot", r: 2.2 },
    { pts: b, color: css("--c2"), kind: "dot", r: 2.2 },
  ], { x: [-win / 2, win / 2], xfmt: (t) => t + " ms", yfmt: (t) => t + " s" });
}

function drawShape(m) {
  if (!m.shape) { setupCanvas($("#cvShape")); return; }
  const n = m.shape.length, dt = m.shape_ms / n;
  const pts = m.shape.map((v, i) => [i * dt - m.shape_pre_ms, v]);
  const p1 = m.p1_ms - m.shape_pre_ms, p3 = m.p3_ms - m.shape_pre_ms;
  const { X, Y } = plot($("#cvShape"), [{ pts, color: css("--c1") }], { y: [0, 1.05], xfmt: (t) => t + " ms" });
  const g = $("#cvShape").getContext("2d");
  g.strokeStyle = css("--c2");
  g.setLineDash([3, 3]);
  for (const x of [p1, p3]) if (fin(x)) { g.beginPath(); g.moveTo(X(x), Y(0)); g.lineTo(X(x), Y(1.05)); g.stroke(); }
  g.setLineDash([]);
}

function drawHist() {
  if (!hist.length) { setupCanvas($("#cvHist")); return; }
  const t0 = hist[0][0];
  // Seconds for a short history, minutes once it runs past a few minutes.
  const mins = hist[hist.length - 1][0] - t0 > 180;
  const k = mins ? 60 : 1;
  plot($("#cvHist"), [
    { pts: hist.map((h) => [(h[0] - t0) / k, h[1]]), color: css("--c1") },
    { pts: hist.map((h) => [(h[0] - t0) / k, h[2]]), color: css("--c3"), axis: "r" },
  ], { right: true, xfmt: (t) => t + (mins ? "m" : "s") });
}

$("#traceWin").addEventListener("change", () => lastM && drawTrace(lastM));
$("#btnListen").addEventListener("click", () => cmd("listen", { on: lastState && lastState.listening ? 0 : 1 }).then(() => { lastSeq = -1; }));
$("#btnHistClear").addEventListener("click", async () => { hist = []; await cmd("history_clear"); drawHist(); });
$("#btnRun").addEventListener("click", () => startRun($("#runPos").value));
$("#btnRunStop").addEventListener("click", () => cmd("run_stop"));
$("#runPos").innerHTML = POSITIONS.map((p) => `<option>${p}</option>`).join("");
for (const id of ["runSettle", "runDur", "runWind"]) {
  const el = $("#" + id);
  el.value = store.get(id, el.value);
  el.addEventListener("change", () => store.set(id, el.value));
}

async function startRun(position) {
  const w = watchById(curWatchId);
  try {
    await cmd("run_start", {
      settle: $("#runSettle").value, duration: $("#runDur").value, position,
      wind: $("#runWind").value, watch_id: w ? w.id : "", caliber_key: w ? w.caliber_key || "" : "",
    });
  } catch (e) { banner(e.message); }
}

// ---------------------------------------------------------------- collection

async function loadCollection() {
  try {
    coll = await getJSON("/api/collection");
    if (!Array.isArray(coll.watches)) coll.watches = [];
  } catch (e) {
    banner("Could not read the collection: " + e.message);
  }
  renderWatchSel();
}

async function saveCollection() {
  coll.saved = isoLocal();
  const r = await fetch("/api/collection", { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify(coll) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.msg || "Could not save the collection");
}

function renderWatchSel() {
  const sel = $("#watchSel");
  const live = coll.watches.filter((w) => !w.archived);
  sel.innerHTML = `<option value="">No watch selected</option>` +
    live.map((w) => `<option value="${esc(w.id)}">${esc(watchLabel(w))}</option>`).join("");
  if (!watchById(curWatchId)) curWatchId = "";
  sel.value = curWatchId;
  updateCalInfo();
}

$("#watchSel").addEventListener("change", async (e) => {
  curWatchId = e.target.value;
  store.set("watch", curWatchId);
  await applyWatchCaliber();
});

// Selecting a watch sets the analyzer's lift angle and beat rate to its
// caliber (its own lift-angle override wins).
async function applyWatchCaliber() {
  const w = watchById(curWatchId);
  if (!w) { updateCalInfo(); return; }
  await loadCalibers().catch(() => null);
  const c = calByKey.get(w.caliber_key);
  const lift = fin(+w.lift_angle) && w.lift_angle !== null && w.lift_angle !== "" ? +w.lift_angle : c ? c[4] : null;
  const fields = {};
  if (lift) fields.lift_angle = lift;
  fields.forced_bph = c && c[3] ? c[3] : 0;
  try {
    await post("/api/settings", fields);
    lastSeq = -1;
    loadSettings();   // or a later Save on the Settings tab writes the old values back
  } catch (e) { banner(e.message); }
  updateCalInfo();
}

function updateCalInfo() {
  const w = watchById(curWatchId);
  const c = w && calByKey.get(w.caliber_key);
  $("#calInfo").innerHTML = w ? (c ? calInfo(c) : "Caliber not set -- beat rate auto-detected, lift angle from Settings") : "";
}

// Save readings as one TestRecord on the current watch (desktop format).
async function saveTest(readings, lift, caliberKey, notes, serviceEvent = false) {
  let w = watchById(curWatchId);
  if (!w) { banner("Pick a watch at the top first."); return false; }
  const rates = readings.map((r) => r.rate).filter(fin);
  const amps = readings.map((r) => r.amplitude).filter(fin);
  const bes = readings.map((r) => r.beat_error).filter(fin);
  const rec = {
    when: isoLocal(), caliber_key: caliberKey || w.caliber_key || "", lift_angle: +lift || +settings.lift_angle || 52,
    readings, notes: notes || "", service_event: !!serviceEvent,
    mean_rate: rates.length ? rates.reduce((a, b) => a + b, 0) / rates.length : null,
    delta_rate: rates.length ? Math.max(...rates) - Math.min(...rates) : null,
    max_amplitude: amps.length ? Math.max(...amps) : null,
    min_amplitude: amps.length ? Math.min(...amps) : null,
    max_beat_error: bes.length ? Math.max(...bes) : null,
  };
  (w.history = w.history || []).push(rec);
  try {
    await saveCollection();
    banner(`Saved to ${watchLabel(w)}.`);
    return true;
  } catch (e) {
    w.history.pop();
    banner(e.message);
    return false;
  }
}

// ---------------------------------------------------------------- positions

function renderPositions() {
  const tb = $("#posTable tbody");
  const busy = lastState && (lastState.run.state === "settle" || lastState.run.state === "measure");
  tb.innerHTML = POSITIONS.map((p) => {
    const r = session[p];
    return `<tr><td>${p}</td><td>${r ? f(r.rate, 1, true) : ""}</td><td>${r ? f(r.amplitude, 0) : ""}</td>
      <td>${r ? f(r.beat_error, 1) : ""}</td><td><button class="small" data-pos="${p}" ${busy ? "disabled" : ""}>${r ? "Redo" : "Measure"}</button></td></tr>`;
  }).join("");
  $$("button[data-pos]", tb).forEach((b) => (b.onclick = () => { startRun(b.dataset.pos); showTab("measure"); }));
  const rs = Object.values(session);
  const rates = rs.map((r) => r.rate).filter(fin), amps = rs.map((r) => r.amplitude).filter(fin);
  const flat = ["Dial up", "Dial down"].map((p) => session[p] && session[p].amplitude).filter(fin);
  const vert = ["Crown down", "Crown left", "Crown right", "Crown up"].map((p) => session[p] && session[p].amplitude).filter(fin);
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  $("#posSummary").innerHTML = rates.length
    ? `Mean <b>${f(mean(rates), 1, true)}</b> s/day &middot; delta <b>${f(Math.max(...rates) - Math.min(...rates), 1)}</b> s/day` +
      (amps.length ? ` &middot; amplitude ${f(Math.min(...amps), 0)}-${f(Math.max(...amps), 0)}&deg;` : "") +
      (flat.length && vert.length ? ` &middot; flat-to-vertical drop ${f(mean(flat) - mean(vert), 0)}&deg;` : "")
    : `<span class="muted">No positions measured yet.</span>`;
}
$("#btnPosClear").addEventListener("click", async () => {
  if (!(await confirmBox("Clear all position readings?"))) return;
  session = {};
  store.set("session", session);
  renderPositions();
});
$("#btnPosSave").addEventListener("click", async () => {
  const rs = POSITIONS.filter((p) => session[p]).map((p) => session[p]);
  if (!rs.length) { banner("Measure at least one position first."); return; }
  if (await saveTest(rs, settings.lift_angle, "", $("#posNotes").value, $("#posService").checked)) {
    session = {};
    store.set("session", session);
    $("#posNotes").value = "";
    renderPositions();
  }
});

// ---------------------------------------------------------------- power reserve

function fmtH(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

let prWasActive = null;
function renderPrLive(pr) {
  // A log that ends on its own (watch ran down) must drop its "logging" state
  // in the list and in an open detail view.
  if (prWasActive !== null && prWasActive !== pr.active) {
    refreshPrList().then(() => {
      if (prOpen) { const r = prRuns.find((x) => x.meta && prOpen.meta && x.meta.id === prOpen.meta.id); if (r) openPr(r); }
    });
  }
  prWasActive = pr.active;
  $("#btnPrStart").disabled = pr.active;
  $("#btnPrStop").disabled = !pr.active;
  if (!pr.active) {
    $("#prStatus").innerHTML = `<span class="muted">Not logging.</span>`;
    setupCanvas($("#cvPrSpark"));
    return;
  }
  const L = pr.last || [];
  $("#prStatus").innerHTML = `<b>${fmtH(pr.elapsed)}</b> elapsed &middot; ${pr.samples} samples &middot; next in ${f(Math.max(0, pr.next), 0)} s<br>` +
    (pr.samples ? `Last: ${f(L[2], 0)}&deg; &middot; ${f(L[1], 1, true)} s/day &middot; ${f(L[3], 1)} ms` : "Waiting for the first sample.");
  if ($("#tab-reserve").classList.contains("on") && pr.spark && pr.spark.length > 1)
    plot($("#cvPrSpark"), [{ pts: pr.spark.map((v, i) => [((pr.spark0 || 0) + i + 1) * pr.interval / 3600, v]), color: css("--c3") }], { xfmt: (t) => t + "h" });
}

$("#btnPrStart").addEventListener("click", async () => {
  const w = watchById(curWatchId);
  if (!w && !(await confirmBox("No watch selected -- log anyway? You can attach it to a watch afterwards."))) return;
  try {
    await cmd("pr_start", {
      interval: $("#prInt").value, watch_id: w ? w.id : "", caliber_key: w ? w.caliber_key || "" : "",
      target_h: $("#prTarget").value || ratedHours(w) || 0, notes: $("#prNotes").value,
    });
    if (!lastState || !lastState.listening) banner("Logging started. The device keeps listening until the log ends.");
    refreshPrList();
  } catch (e) { banner(e.message); }
});
$("#btnPrStop").addEventListener("click", async () => {
  if (!(await confirmBox("Stop the power-reserve log?"))) return;
  await cmd("pr_stop");
  refreshPrList();
});

function ratedHours(w) {
  const c = w && calByKey.get(w.caliber_key);
  return c && c[8] ? c[8] : 0;
}

let prRuns = [];
async function refreshPrList() {
  try { prRuns = await getJSON("/api/pr"); } catch { return; }
  prRuns.sort((a, b) => String(b.meta && b.meta.id).localeCompare(String(a.meta && a.meta.id)));
  $("#prList").innerHTML = prRuns.length ? prRuns.map((r, i) => {
    const m = r.meta || {}, e = r.end || {};
    const w = watchById(m.watch_id);
    const n = Math.round(r.csv_bytes / 27);
    return `<button class="item" data-i="${i}"><div class="grow"><div class="t">${esc(w ? watchLabel(w) : "No watch")} &middot; ${esc((m.when || m.id || "").replace("T", " "))}</div>
      <div class="s">${r.active ? "logging now" : esc(e.reason || "")}${fin(e.hours) ? " &middot; " + f(e.hours, 1) + " h" : ""} &middot; ~${n} samples</div></div></button>`;
  }).join("") : `<div class="muted small">None yet.</div>`;
  $$("#prList .item").forEach((b) => (b.onclick = () => openPr(prRuns[+b.dataset.i])));
}

// Elapsed hours at which the half-hour-median amplitude trend crossed each
// level and stayed there (analysis.reserve_crossings).
function ampWindows(t, a, minutes = 30) {
  const w = minutes / 60, bt = [], ba = [];
  if (!t.length) return [bt, ba];
  for (let e = t[0]; e <= t[t.length - 1] + 1e-9; e += w) {
    const sel = t.map((v, i) => [v, a[i]]).filter(([v]) => v >= e && v < e + w + 1e-9);
    if (!sel.length) continue;
    bt.push(sel.reduce((s, p) => s + p[0], 0) / sel.length);
    const s = sel.map((p) => p[1]).sort((x, y) => x - y);
    ba.push(s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2);
  }
  return [bt, ba];
}
function median(a) {
  const s = a.slice().sort((x, y) => x - y);
  return s.length ? (s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2) : NaN;
}
function crossings(samples, levels = [220, 200, 135]) {
  const ok = samples.filter((s) => fin(s[0]) && fin(s[2]));
  const [bt, ba] = ampWindows(ok.map((s) => s[0] / 3600), ok.map((s) => s[2]));
  const out = {};
  if (bt.length < 3) return out;
  for (const lv of levels) for (let i = 1; i < bt.length; i++) {
    if (ba[i - 1] >= lv && lv >= ba[i] && median(ba.slice(i)) <= lv + 3) {
      const fr = ba[i - 1] !== ba[i] ? (ba[i - 1] - lv) / (ba[i - 1] - ba[i]) : 0;
      out[lv] = bt[i - 1] + fr * (bt[i] - bt[i - 1]);
      break;
    }
  }
  return out;
}
// Where a level was not reached, a straight-line projection of the last few
// hours of amplitude decay -- marked estimated. The desktop recomputes these
// figures from the samples with its fuller model when the run is merged.
function projected(samples, level) {
  const ok = samples.filter((s) => fin(s[2]));
  if (ok.length < 6) return NaN;
  const tEnd = ok[ok.length - 1][0], pts = ok.filter((s) => s[0] >= tEnd - 4 * 3600);
  if (pts.length < 4) return NaN;
  const n = pts.length, mx = pts.reduce((a, s) => a + s[0], 0) / n, my = pts.reduce((a, s) => a + s[2], 0) / n;
  let sxx = 0, sxy = 0;
  for (const s of pts) { sxx += (s[0] - mx) ** 2; sxy += (s[0] - mx) * (s[2] - my); }
  const slope = sxy / sxx;
  if (!(slope < -0.05 / 3600)) return NaN;   // not decaying (yet)
  return (mx + (level - my) / slope) / 3600;
}

let prOpen = null;
async function openPr(run) {
  prOpen = run;
  const m = run.meta || {};
  const text = await (await fetch(`/api/pr/csv?id=${encodeURIComponent(m.id)}`, { cache: "no-store" })).text();
  const samples = text.trim().split("\n").filter(Boolean).map((l) => l.split(",").map((v) => (v === "" ? NaN : +v)));
  run.samples = samples;
  const w = watchById(m.watch_id);
  $("#prDetail").hidden = false;
  $("#prTitle").textContent = `${w ? watchLabel(w) : "No watch"} -- ${(m.when || m.id || "").replace("T", " ")}`;
  const cr = crossings(samples);
  const last = samples.length ? samples[samples.length - 1][0] / 3600 : 0;
  const fig = (lv) => (cr[lv] !== undefined ? `<b>${f(cr[lv], 1)} h</b>` : fin(projected(samples, lv)) ? `<b>~${f(projected(samples, lv), 1)} h</b> <span class="muted">est.</span>` : "--");
  $("#prFigures").innerHTML = `Power reserve (to 135&deg;): ${fig(135)} &middot; good time to (200&deg;): ${fig(200)}<br>
    <span class="small muted">${samples.length} samples over ${f(last, 1)} h${m.target_h ? ` &middot; rated ${m.target_h} h` : ""} &middot; ${esc((run.end && run.end.reason) || (run.active ? "logging" : ""))}</span>`;
  const pts = (i) => samples.map((s) => [s[0] / 3600, s[i]]);
  plot($("#cvPrAmp"), [{ pts: pts(2), color: css("--c3") }], { xfmt: (t) => t + "h", hlines: [{ y: 200, color: css("--warn") }, { y: 135, color: css("--bad") }] });
  plot($("#cvPrRate"), [{ pts: pts(1), color: css("--c1") }], { xfmt: (t) => t + "h" });
  $("#prCsv").href = `/api/pr/csv?id=${encodeURIComponent(m.id)}`;
  $("#prCsv").setAttribute("download", `power_reserve_${m.id}.csv`);
  $("#btnPrSave").disabled = run.active;
  $("#btnPrDelete").disabled = run.active;
  $("#prDetail").scrollIntoView({ behavior: "smooth" });
}
$("#btnPrClose").addEventListener("click", () => { $("#prDetail").hidden = true; prOpen = null; });
$("#btnPrDelete").addEventListener("click", async () => {
  if (!prOpen || !(await confirmBox("Delete this log from the device? Save it to a watch first if you want to keep it."))) return;
  try { await cmd("pr_delete", { id: prOpen.meta.id }); } catch (e) { banner(e.message); }
  $("#prDetail").hidden = true;
  refreshPrList();
});
// Save as a ReserveRecord (desktop format). The desktop recomputes the
// headline figures from `samples` itself, so only the series has to be right.
$("#btnPrSave").addEventListener("click", async () => {
  const run = prOpen;
  if (!run) return;
  const m = run.meta || {};
  const w = watchById(m.watch_id) || watchById(curWatchId);
  if (!w) { banner("Pick the watch at the top first."); return; }
  const s = run.samples.filter((x) => fin(x[0]));
  const amps = s.map((x) => x[2]).filter(fin);
  const cr = crossings(s);
  const rec = {
    when: m.when || isoLocal(), caliber_key: m.caliber_key || w.caliber_key || "", lift_angle: +m.lift_angle || 52,
    interval_s: +m.interval_s || 300, stopped_early: !!(run.end && /user/.test(run.end.reason || "")),
    hours: s.length ? s[s.length - 1][0] / 3600 : null,
    samples: s.map((x) => x.map((v) => (fin(v) ? v : null))),
    amp_first: amps.length ? amps[0] : null, amp_last: amps.length ? amps[amps.length - 1] : null,
    power_reserve_h: cr[135] ?? (fin(projected(s, 135)) ? projected(s, 135) : null),
    practical_h: cr[200] ?? (fin(projected(s, 200)) ? projected(s, 200) : null),
    pr_estimated: cr[135] === undefined || cr[200] === undefined,
    hours_to_220: cr[220] ?? null, hours_to_200: cr[200] ?? null,
    notes: m.notes || "",
  };
  if ((w.reserves || []).some((r) => r.when === rec.when)) { banner("Already saved to this watch."); return; }
  (w.reserves = w.reserves || []).push(rec);
  try {
    await saveCollection();
    banner(`Saved to ${watchLabel(w)}. You can delete it from the device now.`);
  } catch (e) { w.reserves.pop(); banner(e.message); }
});

// ---------------------------------------------------------------- watches

let editing = null;
function renderWatches() {
  const showArch = $("#showArchived").checked;
  const ws = coll.watches.filter((w) => showArch || !w.archived);
  $("#watchList").innerHTML = ws.length ? ws.map((w) => {
    const c = calByKey.get(w.caliber_key);
    const last = (w.history || []).slice(-1)[0];
    return `<button class="item" data-id="${esc(w.id)}"><div class="grow"><div class="t">${esc(watchLabel(w))}${w.archived ? " (archived)" : ""}</div>
      <div class="s">${esc([w.brand, w.model, w.reference].filter(Boolean).join(" · "))}${c ? " · " + esc(calLabel(c)) : ""}
      ${last ? " · last " + esc((last.when || "").slice(0, 10)) + " " + f(last.mean_rate, 1, true) + " s/d" : ""}</div></div></button>`;
  }).join("") : `<div class="muted small">No watches yet. Add one, or load your desktop collection below.</div>`;
  $$("#watchList .item").forEach((b) => (b.onclick = () => editWatch(watchById(b.dataset.id))));
}
$("#showArchived").addEventListener("change", renderWatches);
$("#btnWatchAdd").addEventListener("click", () => editWatch(null));

function editWatch(w) {
  loadCalibers().catch(() => null);
  editing = w ? w : { id: "", brand: "", model: "", nickname: "", reference: "", caliber_key: "", lift_angle: null, target_rate: "", notes: "", history: [], services: [], reserves: [], archived: false };
  $("#watchEdit").hidden = false;
  $("#weTitle").textContent = w ? watchLabel(w) : "New watch";
  $("#weBrand").value = editing.brand || "";
  $("#weModel").value = editing.model || "";
  $("#weNick").value = editing.nickname || "";
  $("#weRef").value = editing.reference || "";
  $("#weTarget").value = editing.target_rate || "";
  $("#weLift").value = fin(editing.lift_angle) ? editing.lift_angle : "";
  $("#weNotes").value = editing.notes || "";
  $("#weCalSearch").value = "";
  $("#weCalHits").innerHTML = "";
  $("#weCal").innerHTML = calInfo(calByKey.get(editing.caliber_key)) || (editing.caliber_key ? esc(editing.caliber_key) : "No caliber set");
  $("#btnWeArchive").hidden = !w;
  $("#btnWeArchive").textContent = editing.archived ? "Unarchive" : "Archive";
  const h = (editing.history || []).slice().reverse();
  const rs = (editing.reserves || []).slice().reverse();
  $("#weHistory").innerHTML = (h.length || rs.length) ? `<h3>History</h3><table class="grid"><thead><tr><th>Date</th><th>Mean</th><th>Delta</th><th>Amp</th><th>BE</th></tr></thead><tbody>` +
    h.map((r) => `<tr><td>${esc((r.when || "").replace("T", " ").slice(0, 16))}</td><td>${f(r.mean_rate, 1, true)}</td><td>${f(r.delta_rate, 1)}</td>
      <td>${f(r.min_amplitude, 0)}${r.max_amplitude !== r.min_amplitude ? "-" + f(r.max_amplitude, 0) : ""}</td><td>${f(r.max_beat_error, 1)}</td></tr>`).join("") +
    `</tbody></table>` + rs.map((r) => `<div class="small">Reserve ${esc((r.when || "").slice(0, 10))}: ${f(r.hours, 1)} h logged, power reserve ${f(r.power_reserve_h, 1)} h${r.pr_estimated ? " (est.)" : ""}</div>`).join("") : "";
  $("#watchEdit").scrollIntoView({ behavior: "smooth" });
}

let calTimer = 0;
$("#weCalSearch").addEventListener("input", (e) => {
  clearTimeout(calTimer);
  calTimer = setTimeout(async () => {
    const q = e.target.value.toLowerCase().split(/\s+/).filter(Boolean);
    if (!q.length) { $("#weCalHits").innerHTML = ""; return; }
    const rows = await loadCalibers();
    const hits = rows.filter((r) => { const t = (r[1] + " " + r[2]).toLowerCase(); return q.every((w) => t.includes(w)); }).slice(0, 40);
    $("#weCalHits").innerHTML = hits.map((r) => `<button type="button" data-k="${esc(r[0])}">${esc(calLabel(r))} <span class="muted small">${r[3] || "auto"} bph &middot; ${r[4]}&deg;</span></button>`).join("") ||
      `<div class="muted small">No match.</div>`;
    $$("#weCalHits button").forEach((b) => (b.onclick = () => {
      editing.caliber_key = b.dataset.k;
      $("#weCal").innerHTML = calInfo(calByKey.get(b.dataset.k));
      $("#weCalHits").innerHTML = "";
      $("#weCalSearch").value = "";
    }));
  }, 150);
});

$("#btnWeCancel").addEventListener("click", () => { $("#watchEdit").hidden = true; editing = null; });
$("#btnWeSave").addEventListener("click", async () => {
  const w = editing;
  w.brand = $("#weBrand").value.trim();
  w.model = $("#weModel").value.trim();
  w.nickname = $("#weNick").value.trim();
  w.reference = $("#weRef").value.trim();
  w.target_rate = $("#weTarget").value.trim();
  const lift = parseFloat($("#weLift").value);
  w.lift_angle = isFinite(lift) ? lift : null;
  w.notes = $("#weNotes").value;
  const isNew = !w.id;
  if (isNew) { w.id = uid(); coll.watches.push(w); }
  try {
    await saveCollection();
    $("#watchEdit").hidden = true;
    renderWatches();
    renderWatchSel();
    if (isNew && !curWatchId) { curWatchId = w.id; store.set("watch", w.id); renderWatchSel(); }
    if (w.id === curWatchId) applyWatchCaliber();
  } catch (e) {
    if (isNew) coll.watches.pop();
    banner(e.message);
  }
});
$("#btnWeArchive").addEventListener("click", async () => {
  editing.archived = !editing.archived;
  try { await saveCollection(); } catch (e) { banner(e.message); }
  $("#watchEdit").hidden = true;
  renderWatches();
  renderWatchSel();
});

$("#collFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  let incoming;
  try { incoming = parseLenient(await file.text()); } catch { banner("That file is not valid JSON."); return; }
  if (!incoming || !Array.isArray(incoming.watches)) { banner("That does not look like a WatchGrapher collection.json."); return; }
  const n = incoming.watches.length;
  const msg = coll.watches.length
    ? `Merge ${n} watches from this file? Watches already on the device keep their results; new history from the file is added.`
    : `Load ${n} watches onto the device?`;
  if (!(await confirmBox(msg))) return;
  for (const w of incoming.watches) {
    const cur = watchById(w.id);
    if (!cur) { coll.watches.push(w); continue; }
    for (const k of ["history", "reserves", "services"]) {
      const have = new Set((cur[k] || []).map((r) => r.when));
      cur[k] = (cur[k] || []).concat((w[k] || []).filter((r) => !have.has(r.when)));
    }
    for (const [k, v] of Object.entries(w)) if (!["history", "reserves", "services"].includes(k)) cur[k] = v;
  }
  try {
    await saveCollection();
    banner(`Loaded ${n} watches.`);
  } catch (err) { banner(err.message); }
  renderWatches();
  renderWatchSel();
});

// ---------------------------------------------------------------- settings

const form = $("#setForm");
function fillBphSelect(sel, withAuto) {
  sel.innerHTML = (withAuto ? `<option value="0">Auto-detect</option>` : "") + STD_BPH.map((b) => `<option value="${b}">${b}</option>`).join("");
}
fillBphSelect(form.forced_bph, true);
fillBphSelect(form.sim_bph, false);

async function loadSettings() {
  try { settings = await getJSON("/api/settings"); } catch { return; }
  for (const el of form.elements) {
    if (!el.name || !(el.name in settings)) continue;
    if (el.type === "checkbox") el.checked = !!settings[el.name];
    else if (el.type !== "password") el.value = settings[el.name];
  }
  form.window_s.max = settings.window_cap;
  $("#simCard").hidden = settings.source !== "sim";
  $("#netStatus").textContent = settings.net_status || "";
  const mb = (b) => (b / 1048576).toFixed(1) + " MB";
  $("#devInfo").innerHTML = [
    ["Firmware", `${settings.fw} (${settings.variant})`], ["Hotspot", settings.ap_ssid_live], ["On network", settings.sta_ip || "--"],
    ["Longest window", f(settings.window_cap, 1) + " s"], ["PSRAM free", mb(settings.psram_free)],
    ["RAM free", (settings.heap_free / 1024).toFixed(0) + " kB"], ["Storage", `${mb(settings.fs_used)} of ${mb(settings.fs_total)}`],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join("");
}
form.source.addEventListener("change", () => ($("#simCard").hidden = form.source.value !== "sim"));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fields = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") fields[el.name] = el.checked ? 1 : 0;
    else if (el.type === "password") { if (el.value) fields[el.name] = el.value; }
    else fields[el.name] = el.value;
  }
  const newNet = fields.sta_ssid && fields.sta_ssid !== settings.sta_ssid;
  try {
    const r = await post("/api/settings", fields);
    banner(newNet
      ? `Saved. Restart the device: it then joins "${fields.sta_ssid}" and can be found at http://${fields.hostname || "watchgrapher"}.local` +
        (fields.ap_fallback ? " -- its hotspot switches off once it has joined." : ".")
      : r.msg, newNet ? 15000 : 4000);
    lastSeq = -1;
    loadSettings();
  } catch (err) { banner(err.message); }
});
$("#btnReboot").addEventListener("click", async () => {
  if (!(await confirmBox("Restart the device? A running power-reserve log will be closed as interrupted."))) return;
  post("/api/reboot", {}).catch(() => null);
  banner("Restarting...", 8000);
});
$("#btnFactory").addEventListener("click", async () => {
  if (!(await confirmBox("Reset all settings to defaults (pins, Wi-Fi, analysis)? Your watches and logs are kept."))) return;
  post("/api/factory_reset", {}).catch(() => null);
  banner("Resetting and restarting...", 8000);
});
// Firmware updates from the published installer site. Needs a browser with
// internet access (a computer on the same network as the device, or a phone
// whose mobile data is on while it is joined to the hotspot).
const SITE = "https://legal-copy7045.github.io/watchgrapher/";
const verNum = (v) => String(v || "0").split(/[.-]/).map((x) => parseInt(x, 10) || 0);
function newer(a, b) {
  const x = verNum(a), y = verNum(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
  return false;
}
$("#btnUpdCheck").addEventListener("click", async () => {
  const info = $("#updInfo");
  info.textContent = "Checking...";
  const base = SITE + (settings.variant === "n8r8" ? "n8r8/" : "");
  let man;
  try {
    man = await (await fetch(base + "manifest.json", { cache: "no-store" })).json();
  } catch {
    info.textContent = "Couldn't reach the update site -- this browser needs internet access.";
    return;
  }
  if (!newer(man.version, settings.fw)) { info.textContent = `Up to date (${settings.fw}).`; return; }
  const ota = man.builds && man.builds[0] && man.builds[0].ota;
  if (ota && !$("#fwKey").value) { info.textContent = `Version ${man.version} is available -- enter the hotspot password below to install it.`; return; }
  if (!ota || !(await confirmBox(`Install version ${man.version}? The device restarts afterwards. Watches, logs and settings are kept.`))) {
    info.textContent = `Version ${man.version} is available.`;
    return;
  }
  info.textContent = "Downloading...";
  try {
    const bin = await (await fetch(base + ota.path, { cache: "no-store" })).blob();
    uploadFirmware(new File([bin], "firmware.bin"));
    info.textContent = "Installing...";
  } catch {
    info.textContent = "Download failed.";
  }
});

$("#fwFile").addEventListener("change", (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (file) uploadFirmware(file);
});
// The .bin goes up as the raw request body (the device streams it to flash).
function uploadFirmware(file) {
  const x = new XMLHttpRequest();
  x.upload.onprogress = (ev) => ($("#fwBar").style.width = ((100 * ev.loaded) / ev.total).toFixed(0) + "%");
  x.onload = () => { try { banner(JSON.parse(x.responseText).msg, 10000); } catch { banner("Upload finished.", 10000); } };
  x.onerror = () => banner("Upload failed.");
  x.onloadend = () => { if (x.status === 403) banner("Wrong hotspot password -- firmware not changed.", 8000); };
  x.open("POST", "/api/update?key=" + encodeURIComponent($("#fwKey").value || ""));
  x.setRequestHeader("Content-Type", "application/octet-stream");
  x.send(file);
}

// ---------------------------------------------------------------- boot

window.addEventListener("resize", () => { if (lastM) redrawMeasure(); });

(async function boot() {
  showTab(store.get("tab", "measure"));
  try { hist = (await getJSON("/api/history")).map((h) => h.map((v) => (v === null ? NaN : v))); } catch { /* fine */ }
  await loadCollection();
  loadSettings();
  loadCalibers().then(updateCalInfo).catch(() => null);
  poll();
})();
