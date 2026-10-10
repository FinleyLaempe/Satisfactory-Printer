"""The browser page: 3D view of the beams (three.js from a CDN), used two ways:

* app     - served by server.py with all settings, model upload, export and keyboard build
* preview - exports write preview.html with the beams embedded (view only)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .beams import Beam

KINDS = {  # beam kind -> (label, colour)
    "plane": ("Flächen", "#4f9de8"),
    "stave": ("Dauben", "#3fc1d9"),
    "edge": ("Kanten", "#f2f2f2"),
    "fill": ("Lückenfüller", "#e8613d"),
}


def make_preview_data(
    beams: list[Beam],
    tris: np.ndarray,
    offset: tuple[int, int, int],
    stats: dict,
    keys: list[str],
    title: str = "Satisfactory Printer",
    max_tris: int = 120_000,
) -> dict:
    kinds = sorted({b.kind for b in beams})
    if len(tris) > max_tris:
        tris = tris[np.linspace(0, len(tris) - 1, max_tris).astype(int)]
    r = lambda v: round(float(v), 3)  # noqa: E731
    return {
        "beams": [[*map(r, b.start), *map(r, b.end), kinds.index(b.kind)] for b in beams],
        "kinds": [KINDS.get(k, (k, ""))[0] for k in kinds],
        "colors": [KINDS.get(k, ("", "#cccccc"))[1] for k in kinds],
        "keys": keys,
        "mesh": np.round(tris.reshape(-1), 2).tolist(),
        "anchor": [-o for o in offset],
        "stats": stats,
        "title": title,
    }


def _js(obj) -> str:
    # safe to embed inside <script>
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


def _page(data: dict | None, app: dict | None, title: str) -> str:
    html = _TEMPLATE.replace("__DATA__", _js(data)).replace("__APP__", _js(app))
    return html.replace("__TITLE__", title.replace("<", "&lt;"))


def render_page(data: dict) -> str:
    """Stand-alone preview of one result."""
    return _page(data, None, f"{data.get('title', 'Vorschau')} – Satisfactory Printer")


def render_app(boot: dict) -> str:
    """The app (data is loaded from the server)."""
    return _page(None, boot, "Satisfactory Printer")


def export_html(path: str | Path, data: dict) -> None:
    Path(path).write_text(render_page(data), encoding="utf-8")


_TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#14161a; --panel:#1e2127f2; --text:#e6e6e6; --muted:#9aa0a8; --accent:#f0a030; --line:#33373f;
          --ok:#4fc48a; --warn:#f0a030; --field:#2a2e36; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
    font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; overflow:hidden; }
  #view { position:fixed; inset:0; }
  #panel { position:fixed; top:12px; left:12px; bottom:12px; width:330px; max-width:calc(100vw - 24px);
    overflow:auto; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  h1 { font-size:16px; margin:0 0 4px; }
  h2 { font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin:16px 0 6px; font-weight:600; }
  .sub { color:var(--muted); font-size:12px; margin-bottom:6px; }
  label { display:flex; align-items:center; gap:6px; margin:5px 0; color:var(--muted); }
  label output, label .val { margin-left:auto; color:var(--text); font-variant-numeric:tabular-nums; }
  input[type=range] { width:100%; accent-color:var(--accent); margin:0 0 2px; }
  input[type=number], select { background:var(--field); color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:4px 8px; font:inherit; }
  input[type=number] { width:76px; margin-left:auto; }
  select { margin-left:auto; max-width:190px; }
  .row { display:flex; gap:6px; align-items:center; margin:6px 0; flex-wrap:wrap; }
  button { background:var(--field); color:var(--text); border:1px solid var(--line); border-radius:6px;
    padding:5px 10px; font:inherit; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent); color:#1a1a1a; border-color:var(--accent); font-weight:600; }
  button:disabled, input:disabled, select:disabled { opacity:.45; cursor:default; }
  .tabs { display:flex; gap:4px; margin:4px 0 8px; }
  .tabs button { flex:1; }
  .tabs button.on { border-color:var(--accent); color:var(--accent); }
  .stats { display:grid; grid-template-columns:auto auto; gap:2px 12px; font-variant-numeric:tabular-nums; }
  .stats div:nth-child(odd) { color:var(--muted); }
  .legend { display:flex; flex-wrap:wrap; gap:6px 12px; margin:8px 0; }
  .legend span { display:inline-flex; align-items:center; gap:5px; cursor:pointer; user-select:none; }
  .legend i { width:12px; height:12px; border-radius:3px; display:inline-block; }
  .legend span.off { opacity:.35; }
  .note { font-size:12px; color:var(--muted); }
  .msg { font-size:13px; min-height:1.3em; white-space:pre-wrap; }
  .msg.ok { color:var(--ok); } .msg.warn { color:var(--warn); }
  .msg a { color:var(--accent); }
  #status.busy::before, .busy::before { content:""; display:inline-block; width:10px; height:10px; margin-right:6px;
    border-radius:50%; border:2px solid var(--accent); border-right-color:transparent; animation:spin .7s linear infinite;
    vertical-align:-1px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #runbar { height:6px; background:var(--field); border-radius:3px; overflow:hidden; margin:6px 0 2px; }
  #runbar div { height:100%; width:0; background:var(--ok); transition:width .2s; }
  #cur { font-family:ui-monospace, Consolas, monospace; font-size:12px; color:var(--muted); white-space:pre-wrap;
    word-break:break-all; margin-top:4px; }
  .app-only, .keys-only, .bp-only { display:none; }
  #drop { position:fixed; inset:0; display:none; place-items:center; background:#000a; color:var(--accent);
    font-size:22px; border:3px dashed var(--accent); z-index:5; }
  #err { position:fixed; inset:0; display:none; place-items:center; color:var(--muted); }
  #hint { position:fixed; right:12px; bottom:10px; color:var(--muted); font-size:12px; }
  @media (max-width: 700px) { #panel { bottom:auto; max-height:55vh; width:auto; right:12px; } #hint { display:none; } }
</style>
</head>
<body>
<div id="view"></div>
<div id="err">three.js konnte nicht geladen werden – für die 3D-Ansicht wird eine Internetverbindung gebraucht.</div>
<div id="drop">Modell hier ablegen (.stl / .obj)</div>
<div id="panel">
  <h1>Satisfactory Printer</h1>
  <div class="sub" id="title"></div>

  <div class="app-only">
    <h2>Modell</h2>
    <label>Modell <select id="model"></select></label>
    <div class="row"><button id="uploadBtn">Hochladen…</button><span class="note">oder Datei ins Fenster ziehen</span>
      <input type="file" id="file" accept=".stl,.obj" hidden></div>
    <label><input type="checkbox" id="baseOn"> Sockel entfernen <output id="baseOut"></output></label>
    <input type="range" id="base" min="0.05" max="0.5" step="0.01" value="0.16" disabled>

    <h2>Form</h2>
    <label>Größe (längste Seite) <output id="sizeOut"></output></label>
    <input type="range" id="size" min="5" max="200" step="1" value="40">
    <label>Detail (Toleranz) <output id="tolOut"></output></label>
    <input type="range" id="tolerance" min="0.25" max="3" step="0.25" value="1">
    <label>Trägerabstand <output id="spacingOut"></output></label>
    <input type="range" id="spacing" min="1" max="4" step="1" value="1">

    <h2>Aussehen</h2>
    <label>Lack-Finish <select id="finish">
      <option value="">ohne</option><option value="caterium">Caterium (gold)</option>
      <option value="copper">Kupfer</option><option value="chrome">Chrom</option>
      <option value="carbonsteel">Carbonstahl</option></select></label>

    <h2>Ausgabe</h2>
    <div class="tabs"><button id="tabBp" class="on">Blueprint</button><button id="tabKeys">Tastatur</button></div>
    <div class="bp-only">
      <div class="note">Exakte Träger, mit einem Klick platziert.</div>
      <label>Blueprint-Größe <select id="designer">
        <option value="auto">automatisch (ein Blueprint)</option>
        <option value="12">Teile à 12×12×12 (96 m)</option>
        <option value="6">Mk.3-Teile (48 m)</option>
        <option value="5">Mk.2-Teile (40 m)</option>
        <option value="4">Mk.1-Teile (32 m)</option></select></label>
      <label>Stahlträger pro m <input type="number" id="cost" min="0" step="0.05" value="0.25"></label>
      <div class="row"><button class="primary" id="exportBp">Blueprint erstellen</button></div>
    </div>
    <div class="keys-only">
      <div class="note">Reserve: tippt die Träger im Spiel ein (1-m-Raster). Auf dem Spiele-PC starten.</div>
      <label>Tippgeschwindigkeit <input type="number" id="kps" min="0.5" max="100" step="0.5" value="10"> /s</label>
      <label>Start bei Träger <input type="number" id="startAt" min="0" value="0"></label>
      <label>Countdown <input type="number" id="countdown" min="0" max="60" value="5"> s</label>
      <label><input type="checkbox" id="simulate"> Simulieren (keine Tasten drücken)</label>
      <div class="row">
        <button class="primary" id="runStart">▶ Start</button>
        <button id="runPause" disabled>❚❚ Pause</button>
        <button id="runStop" disabled>■ Stopp</button>
        <button id="exportKeys">Plan speichern</button>
      </div>
      <div id="runbar"><div></div></div>
      <div class="msg" id="runstatus"></div>
      <div class="note">Im Spiel: F8 pausiert nach dem aktuellen Träger, F9 bricht sofort ab.</div>
    </div>
    <div class="msg" id="status"></div>
  </div>

  <h2>Ergebnis</h2>
  <div class="stats" id="stats"></div>
  <div class="legend" id="legend"></div>

  <h2>Ansicht</h2>
  <label>Baufortschritt <output id="count"></output></label>
  <input type="range" id="progress" min="0" value="0">
  <div class="row">
    <button id="play">▶ Abspielen</button>
    <select id="speed" style="margin-left:0"><option value="5">5×</option><option value="25" selected>25×</option><option value="100">100×</option><option value="500">500×</option></select>
    <button id="step">+1</button>
  </div>
  <label><input type="checkbox" id="pending" checked> Noch fehlende Träger blass zeigen</label>
  <label><input type="checkbox" id="ghost"> Originalmodell einblenden</label>
  <label class="keys-only"><input type="checkbox" id="anchor" checked> Anker &amp; Raster zeigen</label>
  <label>Färben nach <select id="colorby"><option value="kind">Art</option><option value="length">Länge</option><option value="order">Reihenfolge</option></select></label>
  <div id="cur"></div>
</div>
<div id="hint">ziehen: drehen · rechts/Shift ziehen: verschieben · Mausrad: zoomen</div>
<script src="https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js"></script>
<script>
const APP = __APP__;
let DATA = __DATA__;
const $ = id => document.getElementById(id);
const show = (cls, on) => document.querySelectorAll("." + cls).forEach(e =>
  e.style.display = on ? (e.tagName === "LABEL" ? "flex" : "block") : "none");
if (typeof THREE === "undefined") { $("err").style.display = "grid"; throw new Error("no three"); }

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

let B = [], N = 0, inst = null, pendingInst = null, mats = [], lengths = [], maxLen = 1, ghost = null, helpers = null;
const hidden = new Set();
const target = new THREE.Vector3();
let radius = 30, theta = -0.7, phi = 1.1, lastDiag = 0;

function disposeObj(o) {
  if (!o) return;
  scene.remove(o);
  o.traverse(c => { if (c.geometry && c.geometry !== boxGeo) c.geometry.dispose(); if (c.material) c.material.dispose(); });
}

function render(data) {
  DATA = data; B = data.beams; N = B.length;
  disposeObj(inst); disposeObj(pendingInst); disposeObj(ghost); disposeObj(helpers);
  inst = new THREE.InstancedMesh(boxGeo, new THREE.MeshLambertMaterial({ color: 0xffffff }), Math.max(N, 1));
  mats = []; lengths = [];
  const q = new THREE.Quaternion();
  const lo = new THREE.Vector3(Infinity, Infinity, Infinity), hi = lo.clone().negate();
  const rolls = data.rolls || [], faces = data.faces || [];
  B.forEach((b, i) => {
    const s = g2t(b[0], b[1], b[2]), e = g2t(b[3], b[4], b[5]);
    const dir = e.clone().sub(s), len = dir.length(); lengths.push(len);
    // box z along the beam, box x = the face that lies on the surface
    const z = dir.clone().normalize();
    const f = faces[i];
    let x;
    if (f && (f[0] || f[1] || f[2])) {  // blueprint: exact face direction
      x = g2t(f[0], f[1], f[2]); x.addScaledVector(z, -x.dot(z)).normalize();
    } else {  // keyboard: default face ("up", same rule as orient.py) turned by the wheel notches
      const g = new THREE.Vector3(b[3] - b[0], b[4] - b[1], b[5] - b[2]).normalize();
      let ref = new THREE.Vector3(1, 0, 0);
      for (const r of [[0, 0, 1], [0, 1, 0]]) {
        const v = new THREE.Vector3(...r); v.addScaledVector(g, -v.dot(g));
        if (v.length() > 0.2) { ref = v.normalize(); break; }
      }
      x = g2t(ref.x, ref.y, ref.z).applyAxisAngle(z, (rolls[i] || 0) * Math.PI / 180);
    }
    if (!isFinite(x.x) || x.length() < 0.5) x = new THREE.Vector3(0, 1, 0).cross(z).normalize();
    const y = new THREE.Vector3().crossVectors(z, x);
    q.setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, z));
    const m = new THREE.Matrix4().compose(s.clone().add(e).multiplyScalar(0.5), q, new THREE.Vector3(1, 1, Math.max(len, 0.05)));
    mats.push(m); inst.setMatrixAt(i, m);
    lo.min(s); lo.min(e); hi.max(s); hi.max(e);
  });
  if (!N) { lo.set(0, 0, 0); hi.set(1, 1, 1); }
  maxLen = Math.max(1, ...lengths);
  scene.add(inst);
  pendingInst = new THREE.InstancedMesh(boxGeo, new THREE.MeshLambertMaterial({ color: 0xffffff, transparent: true,
    opacity: 0.13, depthWrite: false }), Math.max(N, 1));
  mats.forEach((m, i) => pendingInst.setMatrixAt(i, m));
  scene.add(pendingInst);

  ghost = null;
  if (data.mesh.length) {
    const M = data.mesh, pos = new Float32Array(M.length);
    for (let i = 0; i < M.length; i += 3) { pos[i] = M[i]; pos[i + 1] = M[i + 2]; pos[i + 2] = -M[i + 1]; }
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.computeVertexNormals();
    ghost = new THREE.Mesh(g, new THREE.MeshLambertMaterial({ color: 0x8899aa, transparent: true, opacity: 0.18,
      side: THREE.DoubleSide, depthWrite: false }));
    ghost.visible = $("ghost").checked;
    scene.add(ghost);
  }

  helpers = new THREE.Group();
  const span = Math.ceil(Math.max(hi.x - lo.x, hi.z - lo.z) + 20);
  const grid = new THREE.GridHelper(span * 2, span * 2 / 8 | 0, 0x444a55, 0x262a31);  // 8 m = one foundation
  grid.position.set((lo.x + hi.x) / 2, lo.y - 0.5, (lo.z + hi.z) / 2); helpers.add(grid);
  const keysMode = data.output === "keys";
  if (keysMode) {
    const a = data.anchor, ap = g2t(a[0], a[1], a[2]);
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.6, 16, 12), new THREE.MeshBasicMaterial({ color: 0xff3b30 }));
    dot.position.copy(ap); helpers.add(dot);
    helpers.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), g2t(a[0], a[1] - 6, a[2]), 4, 0xf0a030, 1.2, 0.8));
  }
  helpers.visible = !keysMode || $("anchor").checked;
  scene.add(helpers);
  show("keys-only", keysMode);
  if (APP) show("bp-only", !keysMode);

  const diag = Math.max(hi.clone().sub(lo).length(), 5);
  target.copy(lo).add(hi).multiplyScalar(0.5);
  radius = lastDiag ? radius * diag / lastDiag : diag * 1.3;
  lastDiag = diag;
  placeCamera();

  $("title").textContent = data.title;
  showStats(data.stats);
  const legend = $("legend"); legend.innerHTML = "";
  hidden.clear();
  data.kinds.forEach((k, i) => {
    const n = B.filter(b => b[6] === i).length;
    const s = document.createElement("span");
    const sw = document.createElement("i"); sw.style.background = data.colors[i];
    s.append(sw, `${k} (${n})`);
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
  return h ? `${h} h ${m} min` : m ? `${m} min ${sec} s` : `${sec} s`;
}
function showStats(stats) {
  lastStats = stats;
  const st = $("stats"); st.innerHTML = "";
  const t = DATA && DATA.timing;
  if (t) stats = Object.assign({}, stats, { "Bauzeit (geschätzt)": fmtTime(t.presses / Math.max(+$("kps").value || t.kps, 0.1) + t.wait_s) });
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
  $("count").textContent = `${n} / ${N}`;
  if (n < N) {
    const b = B[n], f = v => (+v).toFixed(1);
    $("cur").textContent = `nächster: #${n}  (${f(b[0])}, ${f(b[1])}, ${f(b[2])}) → (${f(b[3])}, ${f(b[4])}, ${f(b[5])})  ${lengths[n].toFixed(2)} m` +
      (DATA.keys[n] ? `\n${DATA.keys[n]}` : "");
  } else $("cur").textContent = N ? "alle Träger platziert" : "";
}

// ---- orbit controls
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

// ---- view controls
$("progress").oninput = () => { stop(); refresh(); };
$("ghost").onchange = e => { if (ghost) ghost.visible = e.target.checked; };
$("anchor").onchange = e => { if (helpers) helpers.visible = e.target.checked; };
$("pending").onchange = refresh;
$("colorby").onchange = refresh;
$("step").onclick = () => { stop(); $("progress").value = Math.min(+$("progress").value + 1, N); refresh(); };
let playing = false, acc = 0, last = 0;
function stop() { playing = false; $("play").textContent = "▶ Abspielen"; }
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

// ---- the app (only when served by the program)
const status = (text, cls) => { const el = $("status"); el.innerHTML = text; el.className = "msg " + (cls || ""); };
let output = "blueprint";
const params = (extra = {}) => new URLSearchParams(Object.assign({
  model: $("model").value, size: $("size").value, drop_base: $("baseOn").checked ? $("base").value : 0,
  tolerance: $("tolerance").value, spacing: $("spacing").value, finish: $("finish").value, output,
  designer: $("designer").value, cost_per_m: $("cost").value, kps: $("kps").value }, extra));

if (APP) {
  show("app-only", true);
  const fillModels = (models, select) => {
    const m = $("model"); m.innerHTML = "";
    const groups = {};
    for (const x of models) {
      if (!groups[x.group]) { groups[x.group] = document.createElement("optgroup"); groups[x.group].label = x.group; m.append(groups[x.group]); }
      const o = document.createElement("option"); o.value = x.id; o.textContent = x.name; groups[x.group].append(o);
    }
    if (select) m.value = select;
  };
  fillModels(APP.models);
  const labels = () => {
    $("sizeOut").textContent = $("size").value + " m";
    $("tolOut").textContent = (+$("tolerance").value).toFixed(2) + " m";
    $("spacingOut").textContent = $("spacing").value + " m";
    $("baseOut").textContent = $("baseOn").checked ? Math.round($("base").value * 100) + " % der Höhe" : "";
    $("base").disabled = !$("baseOn").checked;
  };
  labels();

  let seq = 0, timer = null;
  async function rebuild() {
    if (!$("model").value) { status("Noch kein Modell – lade eine .stl oder .obj hoch.", "warn"); return; }
    const my = ++seq, qs = params();
    status("berechne Träger…", "busy");
    try {
      const data = await (await fetch("/api/build?" + qs)).json();
      if (my !== seq) return;
      if (data.error) { status(data.error, "warn"); return; }
      render(data);
      status("messe Genauigkeit…", "busy");
      const m = await (await fetch("/api/accuracy?" + qs)).json();
      if (my !== seq) return;
      showStats(Object.assign({}, data.stats, m));
      status("");
    } catch (e) { if (my === seq) status("Programm nicht erreichbar – läuft es noch?", "warn"); }
  }
  const later = () => { labels(); clearTimeout(timer); timer = setTimeout(rebuild, 350); };
  for (const id of ["size", "tolerance", "spacing", "base"]) $(id).addEventListener("input", later);
  for (const id of ["model", "finish", "baseOn"]) $(id).addEventListener("change", later);
  $("kps").addEventListener("input", () => showStats(lastStats));

  const setOutput = o => {
    output = o;
    $("tabBp").classList.toggle("on", o === "blueprint"); $("tabKeys").classList.toggle("on", o === "keys");
    show("bp-only", o === "blueprint"); show("keys-only", o === "keys");
    rebuild();
  };
  $("tabBp").onclick = () => setOutput("blueprint");
  $("tabKeys").onclick = () => setOutput("keys");

  // upload: button or drag & drop anywhere
  async function upload(file) {
    if (!file) return;
    status(`lade ${file.name} hoch…`, "busy");
    try {
      const r = await (await fetch("/api/upload?name=" + encodeURIComponent(file.name), { method: "POST", body: file })).json();
      if (r.error) return status(r.error, "warn");
      fillModels(r.models, r.id); rebuild();
    } catch (e) { status("Hochladen fehlgeschlagen", "warn"); }
  }
  $("uploadBtn").onclick = () => $("file").click();
  $("file").onchange = e => upload(e.target.files[0]);
  addEventListener("dragover", e => { e.preventDefault(); $("drop").style.display = "grid"; });
  addEventListener("dragleave", e => { if (e.target === $("drop")) $("drop").style.display = "none"; });
  addEventListener("drop", e => { e.preventDefault(); $("drop").style.display = "none"; upload(e.dataTransfer.files[0]); });

  async function exportNow() {
    status("schreibe Dateien…", "busy");
    try {
      const r = await (await fetch("/api/export?" + params(), { method: "POST" })).json();
      if (r.error) return status(r.error, "warn");
      status(`Fertig: <a href="${r.download}">Zip herunterladen</a>\n${r.folder}` + (r.guide ? "\n\n" + r.guide : ""), "ok");
    } catch (e) { status("Programm nicht erreichbar – läuft es noch?", "warn"); }
  }
  $("exportBp").onclick = exportNow;
  $("exportKeys").onclick = exportNow;
  $("designer").onchange = () => { if (output === "blueprint") rebuild(); };
  $("cost").onchange = () => { if (output === "blueprint") rebuild(); };

  // ---- keyboard build: poll the server and show placed beams
  let polling = null;
  const RUNNING = ["ready", "countdown", "running", "paused"];
  const lockable = ["model", "uploadBtn", "baseOn", "base", "size", "tolerance", "spacing", "finish", "tabBp", "tabKeys",
    "kps", "progress", "startAt", "countdown", "simulate", "runStart", "exportKeys"];
  const setLocked = on => { for (const id of lockable) $(id).disabled = on; $("runPause").disabled = $("runStop").disabled = !on; if (!on) labels(); };
  function showRun(st) {
    const el = $("runstatus");
    if (!st || st.status === "idle") { el.textContent = ""; setLocked(false); return; }
    const active = RUNNING.includes(st.status);
    setLocked(active);
    $("progress").value = st.placed; refresh();
    $("runbar").firstElementChild.style.width = (100 * st.placed / Math.max(st.total, 1)) + "%";
    $("runPause").textContent = st.status === "paused" ? "▶ Weiter" : "❚❚ Pause";
    const left = fmtTime(st.eta_s);
    el.textContent = ({
      ready: "startet…",
      countdown: `Zum Spiel wechseln! Start in ${st.countdown} s`,
      running: `setze Träger ${st.placed + 1} / ${st.total} · noch ${left}`,
      paused: `pausiert bei ${st.placed} / ${st.total} · noch ${left}`,
      stopped: `gestoppt bei ${st.placed} / ${st.total}`,
      aborted: `abgebrochen bei ${st.placed} / ${st.total}`,
      done: `fertig – alle ${st.total} Träger gesetzt`,
      error: "Fehler",
    }[st.status] || st.status) + (st.message && !active ? ` (${st.message})` : "") + (st.backend && active ? ` · ${st.backend}` : "");
    el.className = "msg " + (st.status === "running" || st.status === "done" ? "ok" : "warn");
    if (!active) { $("startAt").value = st.placed < st.total ? st.placed : 0; clearInterval(polling); polling = null; }
  }
  const poll = () => { if (!polling) polling = setInterval(async () => {
    try { showRun(await (await fetch("/api/run/status")).json()); } catch (e) { clearInterval(polling); polling = null; setLocked(false); }
  }, 250); };
  $("runStart").onclick = async () => {
    const qs = params({ start_at: $("startAt").value, countdown: $("countdown").value, simulate: $("simulate").checked });
    const st = await (await fetch("/api/run/start?" + qs, { method: "POST" })).json();
    if (st.error) { $("runstatus").textContent = st.error; $("runstatus").className = "msg warn"; return; }
    showRun(st); poll();
  };
  $("runPause").onclick = () => fetch("/api/run/" + ($("runPause").textContent.includes("Weiter") ? "resume" : "pause"), { method: "POST" });
  $("runStop").onclick = () => fetch("/api/run/stop", { method: "POST" });

  resize(); requestAnimationFrame(loop);
  if (APP.run && RUNNING.includes(APP.run.status) && APP.run.query) {
    // a keyboard build is running: show its settings and progress
    const q = new URLSearchParams(APP.run.query);
    for (const [id, key] of [["size", "size"], ["tolerance", "tolerance"], ["spacing", "spacing"], ["finish", "finish"], ["kps", "kps"]])
      if (q.get(key) !== null) $(id).value = q.get(key);
    if (q.get("model")) $("model").value = q.get("model");
    if (+q.get("drop_base")) { $("baseOn").checked = true; $("base").value = q.get("drop_base"); }
    labels(); output = "keys"; $("tabKeys").classList.add("on"); $("tabBp").classList.remove("on");
    show("bp-only", false); show("keys-only", true);
    rebuild().then(() => { showRun(APP.run); poll(); });
  } else {
    show("keys-only", false); show("bp-only", true);
    rebuild();
  }
} else {
  resize(); render(DATA); requestAnimationFrame(loop);
}
</script>
</body>
</html>
"""
