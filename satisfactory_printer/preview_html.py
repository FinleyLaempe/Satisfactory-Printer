"""Interactive 3D browser preview (single HTML page, three.js from a CDN).

The same page is used two ways:
* static  - `build` writes preview.html with the beams embedded
* live    - `serve` hosts it with LIVE settings; sliders for size/mode/... ask the local
            server to rebuild and the page re-renders the new beams
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .beams import Beam

KIND_COLORS = {
    "voxel": "#e8a33d",
    "plane": "#4f9de8",
    "outline": "#2f6fb5",
    "ring": "#4fc48a",
    "arc": "#d9c84a",
    "rib": "#b06be0",
    "edge": "#f2f2f2",
    "": "#cccccc",
}


def make_preview_data(
    beams: list[Beam],
    tris: np.ndarray,
    offset: tuple[int, int, int],
    stats: dict,
    keys: list[str],
    title: str = "Satisfactory Printer preview",
    max_tris: int = 120_000,
) -> dict:
    kinds = sorted({b.kind for b in beams})
    if len(tris) > max_tris:
        tris = tris[np.linspace(0, len(tris) - 1, max_tris).astype(int)]
    return {
        "beams": [[*b.start, *b.end, kinds.index(b.kind)] for b in beams],
        "kinds": kinds,
        "colors": [KIND_COLORS.get(k, "#cccccc") for k in kinds],
        "keys": keys,
        "mesh": np.round(tris.reshape(-1), 2).tolist(),
        "anchor": [-o for o in offset],
        "stats": stats,
        "title": title,
    }


def _js(obj) -> str:
    # safe to embed inside <script>
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


def render_page(data: dict, live: dict | None = None) -> str:
    html = _TEMPLATE.replace("__DATA__", _js(data)).replace("__LIVE__", _js(live))
    return html.replace("__TITLE__", str(data.get("title", "preview")).replace("<", "&lt;"))


def export_html(path: str | Path, data: dict) -> None:
    Path(path).write_text(render_page(data), encoding="utf-8")


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#14161a; --panel:#1e2127ee; --text:#e6e6e6; --muted:#9aa0a8; --accent:#f0a030; --line:#33373f; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
    font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; overflow:hidden; }
  #view { position:fixed; inset:0; }
  #panel { position:fixed; top:12px; left:12px; width:310px; max-width:calc(100vw - 24px);
    max-height:calc(100vh - 24px); overflow:auto; background:var(--panel); border:1px solid var(--line);
    border-radius:10px; padding:12px 14px; }
  h1 { font-size:15px; margin:0 0 8px; }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:14px 0 6px; font-weight:600; }
  .stats { display:grid; grid-template-columns:auto auto; gap:2px 12px; font-variant-numeric:tabular-nums; }
  .stats div:nth-child(odd) { color:var(--muted); }
  .legend { display:flex; flex-wrap:wrap; gap:6px 12px; margin:10px 0; }
  .legend span { display:inline-flex; align-items:center; gap:5px; cursor:pointer; user-select:none; }
  .legend i { width:12px; height:12px; border-radius:3px; display:inline-block; }
  .legend span.off { opacity:.35; }
  label { display:flex; align-items:center; gap:6px; margin:4px 0; color:var(--muted); }
  label output { margin-left:auto; color:var(--text); font-variant-numeric:tabular-nums; }
  input[type=range] { width:100%; accent-color:var(--accent); margin:0 0 4px; }
  .row { display:flex; gap:6px; align-items:center; margin:6px 0; }
  button, select { background:#2a2e36; color:var(--text); border:1px solid var(--line); border-radius:6px;
    padding:4px 10px; font:inherit; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent); color:#1a1a1a; border-color:var(--accent); font-weight:600; }
  #cur { font-family: ui-monospace, Consolas, monospace; font-size:12px; color:var(--muted);
    white-space:pre-wrap; word-break:break-all; margin-top:6px; }
  #status { font-size:12px; color:var(--muted); min-height:1.4em; }
  #status.busy::before { content:""; display:inline-block; width:10px; height:10px; margin-right:6px; border-radius:50%;
    border:2px solid var(--accent); border-right-color:transparent; animation:spin .7s linear infinite; vertical-align:-1px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #live, #runbox { display:none; }
  input[type=number] { width:70px; background:#2a2e36; color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:3px 6px; font:inherit; margin-left:auto; }
  #runbar { height:6px; background:#2a2e36; border-radius:3px; overflow:hidden; margin:6px 0 2px; }
  #runbar div { height:100%; width:0; background:#4fc48a; transition:width .2s; }
  #runstatus { font-size:13px; font-variant-numeric:tabular-nums; min-height:1.4em; }
  #runstatus.active { color:#4fc48a; } #runstatus.warn { color:var(--accent); }
  button:disabled, input:disabled, select:disabled { opacity:.45; cursor:default; }
  #hint { position:fixed; right:12px; bottom:10px; color:var(--muted); font-size:12px; }
  #err { position:fixed; inset:0; display:none; place-items:center; color:var(--muted); }
  @media (max-width: 600px) { #panel { width:auto; right:12px; max-height:50vh; } #hint { display:none; } }
</style>
</head>
<body>
<div id="view"></div>
<div id="err">Couldn't load three.js from the CDN. An internet connection is needed for the preview.</div>
<div id="panel">
  <h1 id="title"></h1>

  <div id="live">
    <h2>Settings</h2>
    <label>Size (largest side) <output id="sizeOut"></output></label>
    <input type="range" id="size" min="5" max="120" step="1">
    <label>Mode
      <select id="mode" style="margin-left:auto"></select>
    </label>
    <label>Tolerance <output id="tolOut"></output></label>
    <input type="range" id="tolerance" min="0.25" max="3" step="0.25">
    <label>Beam spacing <output id="spacingOut"></output></label>
    <input type="range" id="spacing" min="1" max="5" step="1">
    <div class="row">
      <button class="primary" id="save">Save plan</button>
      <span id="status"></span>
    </div>
    <h2>Result</h2>
  </div>

  <label>Typing speed <input type="number" id="kps" min="0.5" max="100" step="0.5"> keys/s</label>
  <div class="stats" id="stats"></div>
  <div class="legend" id="legend"></div>
  <label>Build progress <output id="count"></output></label>
  <input type="range" id="progress" min="0" value="0">
  <div class="row">
    <button id="play">▶ Play</button>
    <select id="speed"><option value="1">1×</option><option value="5" selected>5×</option><option value="25">25×</option><option value="100">100×</option></select>
    <button id="step">+1</button>
  </div>
  <label><input type="checkbox" id="pending" checked> Show remaining beams (faint)</label>
  <label><input type="checkbox" id="ghost" checked> Show original model</label>
  <label><input type="checkbox" id="anchor" checked> Show anchor &amp; grid</label>
  <label>Colour by
    <select id="colorby" style="margin-left:auto"><option value="kind">method</option><option value="length">length</option><option value="order">build order</option></select>
  </label>
  <div id="cur"></div>

  <div id="runbox">
    <h2>Build in game</h2>
    <label>Start at beam <input type="number" id="startAt" min="0" value="0"></label>
    <label>Countdown <input type="number" id="countdown" min="0" max="60" value="5"> s</label>
    <label><input type="checkbox" id="simulate"> Simulate (don't press any keys)</label>
    <div class="row">
      <button class="primary" id="runStart">▶ Start</button>
      <button id="runPause" disabled>❚❚ Pause</button>
      <button id="runStop" disabled>■ Stop</button>
    </div>
    <div id="runbar"><div></div></div>
    <div id="runstatus"></div>
    <div style="font-size:12px;color:var(--muted)">In game: F8 pauses after the current beam, F9 aborts at once.</div>
  </div>
</div>
<div id="hint">drag: rotate · right-drag / shift-drag: pan · wheel: zoom</div>
<script src="https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js"></script>
<script>
const LIVE = __LIVE__;
let DATA = __DATA__;
if (typeof THREE === "undefined") { document.getElementById("err").style.display = "grid"; throw new Error("no three"); }
const $ = id => document.getElementById(id);

// game coords (x right, y forward, z up) -> three.js (x, z, -y)
const g2t = (x, y, z) => new THREE.Vector3(x, z, -y);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
$("view").appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14161a);
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 10000);
scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 0.9));
const sun = new THREE.DirectionalLight(0xffffff, 0.8); sun.position.set(50, 120, 80); scene.add(sun);
const boxGeo = new THREE.BoxGeometry(1, 1, 1);
const highlight = new THREE.Color(0xff3b30);

// ---- per-model state (rebuilt by render())
let B = [], N = 0, inst = null, pendingInst = null, mats = [], lengths = [], maxLen = 1, ghost = null, helpers = null;
const hidden = new Set();
const target = new THREE.Vector3();
let radius = 30, theta = -0.7, phi = 1.0, lastDiag = 0;

function disposeObj(o) {
  if (!o) return;
  scene.remove(o);
  o.traverse(c => { if (c.geometry && c.geometry !== boxGeo) c.geometry.dispose(); if (c.material) c.material.dispose(); });
}

function render(data) {
  DATA = data; B = data.beams; N = B.length;
  disposeObj(inst); disposeObj(pendingInst); disposeObj(ghost); disposeObj(helpers);

  // beams as instanced boxes
  inst = new THREE.InstancedMesh(boxGeo, new THREE.MeshLambertMaterial({ color: 0xffffff }), Math.max(N, 1));
  mats = []; lengths = [];
  const q = new THREE.Quaternion(), up = new THREE.Vector3(0, 0, 1);
  const lo = new THREE.Vector3(Infinity, Infinity, Infinity), hi = lo.clone().negate();
  B.forEach((b, i) => {
    const s = g2t(b[0], b[1], b[2]), e = g2t(b[3], b[4], b[5]);
    const dir = e.clone().sub(s), len = dir.length(); lengths.push(len);
    q.setFromUnitVectors(up, dir.clone().normalize());
    const m = new THREE.Matrix4().compose(s.clone().add(e).multiplyScalar(0.5), q, new THREE.Vector3(1, 1, Math.max(len, 0.05)));
    mats.push(m); inst.setMatrixAt(i, m);
    lo.min(s); lo.min(e); hi.max(s); hi.max(e);
  });
  if (!N) { lo.set(0, 0, 0); hi.set(1, 1, 1); }
  maxLen = Math.max(1, ...lengths);
  scene.add(inst);
  // all beams, faint: what is still to be placed shows through
  pendingInst = new THREE.InstancedMesh(boxGeo, new THREE.MeshLambertMaterial({ color: 0xffffff, transparent: true,
    opacity: 0.13, depthWrite: false }), Math.max(N, 1));
  mats.forEach((m, i) => pendingInst.setMatrixAt(i, m));
  pendingInst.count = N;
  scene.add(pendingInst);

  // ghost of the original model
  ghost = null;
  if (data.mesh.length) {
    const M = data.mesh, pos = new Float32Array(M.length);
    for (let i = 0; i < M.length; i += 3) { pos[i] = M[i]; pos[i + 1] = M[i + 2]; pos[i + 2] = -M[i + 1]; }
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.computeVertexNormals();
    ghost = new THREE.Mesh(g, new THREE.MeshLambertMaterial({ color: 0x8899aa, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }));
    ghost.visible = $("ghost").checked;
    scene.add(ghost);
  }

  // anchor, grid and player
  helpers = new THREE.Group();
  const a = data.anchor, ap = g2t(a[0], a[1], a[2]);
  const anchorDot = new THREE.Mesh(new THREE.SphereGeometry(0.6, 16, 12), new THREE.MeshBasicMaterial({ color: 0xff3b30 }));
  anchorDot.position.copy(ap); helpers.add(anchorDot);
  const span = Math.ceil(Math.max(hi.x - lo.x, hi.z - lo.z, Math.abs(ap.x - lo.x), Math.abs(ap.z - lo.z)) + 10);
  const grid = new THREE.GridHelper(span * 2, span * 2, 0x444a55, 0x262a31);
  grid.position.set((lo.x + hi.x) / 2, lo.y - 0.5, (lo.z + hi.z) / 2); helpers.add(grid);
  helpers.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), g2t(a[0], a[1] - 6, a[2]), 4, 0xf0a030, 1.2, 0.8));
  helpers.visible = $("anchor").checked;
  scene.add(helpers);

  // camera: frame the model; on re-render keep the viewing angle and scale the distance
  const diag = Math.max(hi.clone().sub(lo).length(), 5);
  target.copy(lo).add(hi).multiplyScalar(0.5);
  radius = lastDiag ? radius * diag / lastDiag : diag * 1.4;
  lastDiag = diag;
  placeCamera();

  // panel
  $("title").textContent = data.title;
  showStats(data.stats);
  const legend = $("legend"); legend.innerHTML = "";
  hidden.clear();
  data.kinds.forEach((k, i) => {
    const n = B.filter(b => b[6] === i).length;
    const s = document.createElement("span");
    const sw = document.createElement("i"); sw.style.background = data.colors[i];
    s.append(sw, `${k || "beam"} (${n})`);
    s.onclick = () => { hidden.has(i) ? hidden.delete(i) : hidden.add(i); s.classList.toggle("off"); refresh(); };
    legend.append(s);
  });
  stop();
  $("progress").max = N; $("progress").value = N;
  refresh();
}

let lastStats = {};
function fmtTime(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
  return h ? `${h} h ${m} min` : m ? `${m} min ${sec}s` : `${sec}s`;
}
function estimate() {
  const t = DATA.timing; if (!t) return null;
  return t.presses / Math.max(+$("kps").value || t.kps, 0.1) + t.wait_s;
}
function showStats(stats) {
  lastStats = stats;
  const st = $("stats"); st.innerHTML = "";
  const est = estimate();
  if (est !== null) stats = Object.assign({}, stats, { "est. build time": fmtTime(est) });
  for (const [k, v] of Object.entries(stats)) {
    const a = document.createElement("div"); a.textContent = k;
    const b = document.createElement("div"); b.textContent = v;
    st.append(a, b);
  }
}

function colorFor(i) {
  const mode = $("colorby").value;
  if (mode === "length") return new THREE.Color().setHSL(0.66 - 0.66 * lengths[i] / maxLen, 0.7, 0.55);
  if (mode === "order") return new THREE.Color().setHSL(0.75 * i / Math.max(N - 1, 1), 0.65, 0.55);
  return new THREE.Color(DATA.colors[B[i][6]]);
}

// beams [0, n) are placed (solid), beam n is next (red), the rest are faint
function refresh() {
  const n = +$("progress").value;
  const zero = new THREE.Matrix4().makeScale(0, 0, 0);
  B.forEach((b, i) => {
    const hide = hidden.has(b[6]);
    inst.setMatrixAt(i, hide ? zero : mats[i]);
    inst.setColorAt(i, i === n ? highlight : colorFor(i));
    pendingInst.setMatrixAt(i, hide || i <= n ? zero : mats[i]);
    pendingInst.setColorAt(i, colorFor(i));
  });
  inst.count = Math.min(n + 1, N);
  inst.instanceMatrix.needsUpdate = true;
  if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
  pendingInst.visible = $("pending").checked;
  pendingInst.instanceMatrix.needsUpdate = true;
  if (pendingInst.instanceColor) pendingInst.instanceColor.needsUpdate = true;
  $("count").textContent = n + " / " + N + " placed";
  if (n < N) {
    const b = B[n];
    $("cur").textContent = `next: #${n}  (${b[0]},${b[1]},${b[2]}) → (${b[3]},${b[4]},${b[5]})  ${lengths[n].toFixed(2)} m  [${DATA.kinds[b[6]] || "beam"}]\n${DATA.keys[n] || ""}`;
  } else $("cur").textContent = N ? "all beams placed" : "";
}

// ---- tiny orbit controls
function placeCamera() {
  camera.position.set(target.x + radius * Math.sin(phi) * Math.sin(theta), target.y + radius * Math.cos(phi),
    target.z + radius * Math.sin(phi) * Math.cos(theta));
  camera.lookAt(target);
}
let drag = null;
const cv = renderer.domElement;
cv.addEventListener("contextmenu", e => e.preventDefault());
cv.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey }; cv.setPointerCapture(e.pointerId); });
cv.addEventListener("pointerup", () => drag = null);
cv.addEventListener("pointermove", e => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
  if (drag.pan) {
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0), upv = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
    target.add(right.multiplyScalar(-dx * radius / 800)).add(upv.multiplyScalar(dy * radius / 800));
  } else { theta -= dx * 0.008; phi = Math.min(Math.max(phi - dy * 0.008, 0.05), Math.PI - 0.05); }
  placeCamera();
});
cv.addEventListener("wheel", e => { e.preventDefault(); radius *= Math.exp(e.deltaY * 0.001); placeCamera(); }, { passive: false });
function resize() { renderer.setSize(innerWidth, innerHeight); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); }
addEventListener("resize", resize);

// ---- playback UI
$("progress").oninput = () => { stop(); refresh(); };
$("ghost").onchange = e => { if (ghost) ghost.visible = e.target.checked; };
$("pending").onchange = refresh;
$("anchor").onchange = e => { if (helpers) helpers.visible = e.target.checked; };
$("colorby").onchange = refresh;
$("step").onclick = () => { stop(); $("progress").value = Math.min(+$("progress").value + 1, N); refresh(); };
let playing = false, acc = 0, last = 0;
function stop() { playing = false; $("play").textContent = "▶ Play"; }
$("play").onclick = () => {
  if (playing) return stop();
  if (+$("progress").value >= N) $("progress").value = 0;
  playing = true; $("play").textContent = "❚❚ Pause"; last = performance.now();
};
function loop(t) {
  if (playing) {
    acc += (t - last) / 1000 * +$("speed").value; last = t;
    if (acc >= 1) { $("progress").value = Math.min(+$("progress").value + Math.floor(acc), N); acc %= 1; refresh(); if (+$("progress").value >= N) stop(); }
  } else last = t;
  renderer.render(scene, camera);
  requestAnimationFrame(loop);
}

// ---- live settings (only when served by `satisfactory_printer serve`)
const status = (text, busy) => { $("status").textContent = text; $("status").classList.toggle("busy", !!busy); };
let liveParams = null;
if (LIVE) {
  $("live").style.display = "block";
  const p = LIVE.params;
  for (const m of LIVE.modes) { const o = document.createElement("option"); o.value = o.textContent = m; $("mode").append(o); }
  $("size").value = p.size; $("mode").value = p.mode; $("tolerance").value = p.tolerance; $("spacing").value = p.spacing;
  liveParams = (extra = {}) => new URLSearchParams(Object.assign({ size: $("size").value, mode: $("mode").value,
    tolerance: $("tolerance").value, spacing: $("spacing").value, kps: $("kps").value }, extra));
  const labels = () => {
    $("sizeOut").textContent = $("size").value + " m";
    $("tolOut").textContent = (+$("tolerance").value).toFixed(2) + " m";
    $("spacingOut").textContent = $("spacing").value + " m";
  };
  labels();

  let seq = 0, timer = null;
  async function rebuild() {
    const my = ++seq, qs = liveParams();
    status("building…", true);
    try {
      const data = await (await fetch("/api/build?" + qs)).json();
      if (my !== seq) return;               // a newer request is on its way
      if (data.error) { status(data.error); return; }
      render(data);
      await measure(qs, () => my === seq);
    } catch (e) { if (my === seq) status("server not reachable – is `serve` still running?"); }
  }
  for (const id of ["size", "tolerance", "spacing"]) {
    $(id).addEventListener("input", () => { labels(); clearTimeout(timer); timer = setTimeout(rebuild, 300); });
  }
  $("mode").onchange = rebuild;
  $("save").onclick = async () => {
    status("saving…", true);
    try {
      const r = await (await fetch("/api/save?" + liveParams(), { method: "POST" })).json();
      status(r.error ? r.error : "saved to " + r.folder);
    } catch (e) { status("server not reachable – is `serve` still running?"); }
  };
}

// ---- building in the game (live mode): poll the server and show placed beams
let polling = null;
const RUNNING = ["ready", "countdown", "running", "paused"];
function setLocked(locked) {
  for (const id of ["size", "mode", "tolerance", "spacing", "kps", "progress", "startAt", "countdown", "simulate", "runStart", "save"])
    $(id).disabled = locked;
  $("runPause").disabled = $("runStop").disabled = !locked;
}
function showRun(st) {
  const el = $("runstatus");
  el.className = "";
  if (!st || st.status === "idle") { el.textContent = ""; setLocked(false); return; }
  const active = RUNNING.includes(st.status);
  setLocked(active);
  $("progress").value = st.placed; refresh();
  $("runbar").firstElementChild.style.width = (100 * st.placed / Math.max(st.total, 1)) + "%";
  $("runPause").textContent = st.status === "paused" ? "▶ Resume" : "❚❚ Pause";
  const left = fmtTime(st.eta_s);
  const text = {
    ready: "starting…",
    countdown: `switch to the game! starting in ${st.countdown}s`,
    running: `placing beam ${st.placed + 1} / ${st.total} · ${left} left`,
    paused: `paused at beam ${st.placed} / ${st.total} · ${left} left`,
    stopped: `stopped at beam ${st.placed} / ${st.total}`,
    aborted: `aborted at beam ${st.placed} / ${st.total}`,
    done: `done – all ${st.total} beams placed`,
    error: "error",
  }[st.status] || st.status;
  el.textContent = text + (st.message && !active ? ` (${st.message})` : "") + (st.backend && active ? ` · ${st.backend}` : "");
  el.className = active ? (st.status === "running" ? "active" : "warn") : (st.status === "done" ? "active" : "warn");
  if (!active) { $("startAt").value = st.placed < st.total ? st.placed : 0; stopPolling(); }
}
function startPolling() {
  if (polling) return;
  polling = setInterval(async () => {
    try { showRun(await (await fetch("/api/run/status")).json()); }
    catch (e) { $("runstatus").textContent = "server not reachable"; stopPolling(); setLocked(false); }
  }, 250);
}
function stopPolling() { clearInterval(polling); polling = null; }
if (LIVE) {
  $("runbox").style.display = "block";
  $("runStart").onclick = async () => {
    const qs = liveParams({ start_at: $("startAt").value, countdown: $("countdown").value, simulate: $("simulate").checked });
    const st = await (await fetch("/api/run/start?" + qs, { method: "POST" })).json();
    if (st.error) { $("runstatus").textContent = st.error; $("runstatus").className = "warn"; return; }
    showRun(st); startPolling();
  };
  $("runPause").onclick = () => fetch("/api/run/" + ($("runPause").textContent.includes("Resume") ? "resume" : "pause"), { method: "POST" });
  $("runStop").onclick = () => fetch("/api/run/stop", { method: "POST" });
  if (LIVE.run && RUNNING.includes(LIVE.run.status)) { showRun(LIVE.run); startPolling(); }
}

async function measure(qs, current) {
  status("measuring accuracy…", true);
  try {
    const m = await (await fetch("/api/metrics?" + qs)).json();
    if (!current()) return;
    showStats(Object.assign({}, DATA.stats, m));
    status("");
  } catch (e) { if (current()) status(""); }
}

$("kps").value = (LIVE && LIVE.params.kps) || (DATA.timing && DATA.timing.kps) || 10;
$("kps").addEventListener("input", () => showStats(lastStats));

resize(); render(DATA); requestAnimationFrame(loop);
if (LIVE) measure(liveParams(), () => true);
</script>
</body>
</html>
"""
