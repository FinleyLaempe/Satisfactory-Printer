"""Interactive 3D browser preview (single HTML file, three.js from a CDN)."""

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


def export_html(
    path: str | Path,
    beams: list[Beam],
    tris: np.ndarray,
    offset: tuple[int, int, int],
    stats: dict,
    keys: list[str],
    title: str = "Satisfactory Printer preview",
    max_tris: int = 120_000,
) -> None:
    kinds = sorted({b.kind for b in beams})
    if len(tris) > max_tris:
        tris = tris[np.linspace(0, len(tris) - 1, max_tris).astype(int)]
    data = {
        "beams": [[*b.start, *b.end, kinds.index(b.kind)] for b in beams],
        "kinds": kinds,
        "colors": [KIND_COLORS.get(k, "#cccccc") for k in kinds],
        "keys": keys,
        "mesh": np.round(tris.reshape(-1), 2).tolist(),
        "anchor": [-o for o in offset],
        "stats": stats,
        "title": title,
    }
    html = _TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    html = html.replace("__TITLE__", title.replace("<", "&lt;"))
    Path(path).write_text(html, encoding="utf-8")


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
  #panel { position:fixed; top:12px; left:12px; width:300px; max-width:calc(100vw - 24px);
    max-height:calc(100vh - 24px); overflow:auto; background:var(--panel); border:1px solid var(--line);
    border-radius:10px; padding:12px 14px; }
  h1 { font-size:15px; margin:0 0 8px; }
  .stats { display:grid; grid-template-columns:auto auto; gap:2px 12px; font-variant-numeric:tabular-nums; }
  .stats div:nth-child(odd) { color:var(--muted); }
  .legend { display:flex; flex-wrap:wrap; gap:6px 12px; margin:10px 0; }
  .legend span { display:inline-flex; align-items:center; gap:5px; cursor:pointer; user-select:none; }
  .legend i { width:12px; height:12px; border-radius:3px; display:inline-block; }
  .legend span.off { opacity:.35; }
  label { display:flex; align-items:center; gap:6px; margin:4px 0; color:var(--muted); }
  input[type=range] { width:100%; accent-color:var(--accent); }
  .row { display:flex; gap:6px; align-items:center; margin:6px 0; }
  button, select { background:#2a2e36; color:var(--text); border:1px solid var(--line); border-radius:6px;
    padding:4px 10px; font:inherit; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  #cur { font-family: ui-monospace, Consolas, monospace; font-size:12px; color:var(--muted);
    white-space:pre-wrap; word-break:break-all; margin-top:6px; }
  #hint { position:fixed; right:12px; bottom:10px; color:var(--muted); font-size:12px; }
  #err { position:fixed; inset:0; display:none; place-items:center; color:var(--muted); }
  @media (max-width: 600px) { #panel { width:auto; right:12px; max-height:45vh; } #hint { display:none; } }
</style>
</head>
<body>
<div id="view"></div>
<div id="err">Couldn't load three.js from the CDN. An internet connection is needed for the preview.</div>
<div id="panel">
  <h1 id="title"></h1>
  <div class="stats" id="stats"></div>
  <div class="legend" id="legend"></div>
  <label>Build progress <span id="count" style="margin-left:auto"></span></label>
  <input type="range" id="progress" min="0" value="0">
  <div class="row">
    <button id="play">▶ Play</button>
    <select id="speed"><option value="1">1×</option><option value="5" selected>5×</option><option value="25">25×</option><option value="100">100×</option></select>
    <button id="step">+1</button>
  </div>
  <label><input type="checkbox" id="ghost" checked> Show original model</label>
  <label><input type="checkbox" id="anchor" checked> Show anchor &amp; grid</label>
  <label>Colour by
    <select id="colorby" style="margin-left:auto"><option value="kind">method</option><option value="length">length</option><option value="order">build order</option></select>
  </label>
  <div id="cur"></div>
</div>
<div id="hint">drag: rotate · right-drag / shift-drag: pan · wheel: zoom</div>
<script src="https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js"></script>
<script>
const DATA = __DATA__;
if (typeof THREE === "undefined") { document.getElementById("err").style.display = "grid"; throw new Error("no three"); }

// game coords (x right, y forward, z up) -> three.js (x, z, -y)
const g2t = (x, y, z) => new THREE.Vector3(x, z, -y);

const view = document.getElementById("view");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
view.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14161a);
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 5000);
scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 0.9));
const sun = new THREE.DirectionalLight(0xffffff, 0.8); sun.position.set(50, 120, 80); scene.add(sun);

// ---- beams (instanced boxes)
const B = DATA.beams, N = B.length;
const geo = new THREE.BoxGeometry(1, 1, 1);
const mat = new THREE.MeshLambertMaterial({ color: 0xffffff });
const inst = new THREE.InstancedMesh(geo, mat, Math.max(N, 1));
const q = new THREE.Quaternion(), up = new THREE.Vector3(0, 0, 1);
const lengths = [], mats = [];
const lo = new THREE.Vector3(Infinity, Infinity, Infinity), hi = lo.clone().negate();
B.forEach((b, i) => {
  const s = g2t(b[0], b[1], b[2]), e = g2t(b[3], b[4], b[5]);
  const dir = e.clone().sub(s), len = dir.length(); lengths.push(len);
  q.setFromUnitVectors(up, dir.clone().normalize());
  const m = new THREE.Matrix4().compose(s.clone().add(e).multiplyScalar(0.5), q, new THREE.Vector3(1, 1, Math.max(len, 0.05)));
  mats.push(m); inst.setMatrixAt(i, m);
  lo.min(s); lo.min(e); hi.max(s); hi.max(e);
});
scene.add(inst);
const maxLen = Math.max(1, ...lengths);
const hidden = new Set();

function colorFor(i) {
  const mode = document.getElementById("colorby").value;
  if (mode === "length") return new THREE.Color().setHSL(0.66 - 0.66 * lengths[i] / maxLen, 0.7, 0.55);
  if (mode === "order") return new THREE.Color().setHSL(0.75 * i / Math.max(N - 1, 1), 0.65, 0.55);
  return new THREE.Color(DATA.colors[B[i][6]]);
}
const highlight = new THREE.Color(0xff3b30);
function refresh() {
  const n = +progress.value;
  const zero = new THREE.Matrix4().makeScale(0, 0, 0);
  B.forEach((b, i) => {
    inst.setMatrixAt(i, hidden.has(b[6]) ? zero : mats[i]);
    inst.setColorAt(i, i === n - 1 && n < N ? highlight : colorFor(i));
  });
  inst.count = n;
  inst.instanceMatrix.needsUpdate = true;
  if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
  document.getElementById("count").textContent = n + " / " + N;
  const c = document.getElementById("cur");
  if (n > 0) {
    const b = B[n - 1];
    c.textContent = `#${n - 1}  (${b[0]},${b[1]},${b[2]}) → (${b[3]},${b[4]},${b[5]})  ${lengths[n - 1].toFixed(2)} m  [${DATA.kinds[b[6]] || "beam"}]\n${DATA.keys[n - 1] || ""}`;
  } else c.textContent = "";
}

// ---- ghost of the original model
let ghost = null;
if (DATA.mesh.length) {
  const M = DATA.mesh, pos = new Float32Array(M.length);
  for (let i = 0; i < M.length; i += 3) { pos[i] = M[i]; pos[i + 1] = M[i + 2]; pos[i + 2] = -M[i + 1]; }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.computeVertexNormals();
  ghost = new THREE.Mesh(g, new THREE.MeshLambertMaterial({ color: 0x8899aa, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }));
  scene.add(ghost);
}

// ---- anchor + grid
const helpers = new THREE.Group();
const a = DATA.anchor, ap = g2t(a[0], a[1], a[2]);
const anchorDot = new THREE.Mesh(new THREE.SphereGeometry(0.6, 16, 12), new THREE.MeshBasicMaterial({ color: 0xff3b30 }));
anchorDot.position.copy(ap); helpers.add(anchorDot);
const span = Math.ceil(Math.max(hi.x - lo.x, hi.z - lo.z, Math.abs(ap.x - lo.x), Math.abs(ap.z - lo.z)) + 10);
const grid = new THREE.GridHelper(span * 2, span * 2, 0x444a55, 0x262a31);
grid.position.set((lo.x + hi.x) / 2, lo.y - 0.5, (lo.z + hi.z) / 2); helpers.add(grid);
// player: stands behind the anchor looking forward (+y in game)
const player = new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), g2t(a[0], a[1] - 6, a[2]), 4, 0xf0a030, 1.2, 0.8);
helpers.add(player);
scene.add(helpers);

// ---- tiny orbit controls
const target = lo.clone().add(hi).multiplyScalar(0.5);
let radius = Math.max(hi.clone().sub(lo).length() * 1.4, 10), theta = -0.7, phi = 1.0;
function placeCamera() {
  camera.position.set(target.x + radius * Math.sin(phi) * Math.sin(theta), target.y + radius * Math.cos(phi),
    target.z + radius * Math.sin(phi) * Math.cos(theta));
  camera.lookAt(target);
}
let drag = null;
renderer.domElement.addEventListener("contextmenu", e => e.preventDefault());
renderer.domElement.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey }; renderer.domElement.setPointerCapture(e.pointerId); });
renderer.domElement.addEventListener("pointerup", () => drag = null);
renderer.domElement.addEventListener("pointermove", e => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
  if (drag.pan) {
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0), upv = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
    target.add(right.multiplyScalar(-dx * radius / 800)).add(upv.multiplyScalar(dy * radius / 800));
  } else { theta -= dx * 0.008; phi = Math.min(Math.max(phi - dy * 0.008, 0.05), Math.PI - 0.05); }
  placeCamera();
});
renderer.domElement.addEventListener("wheel", e => { e.preventDefault(); radius *= Math.exp(e.deltaY * 0.001); placeCamera(); }, { passive: false });

function resize() { renderer.setSize(innerWidth, innerHeight); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); }
addEventListener("resize", resize);

// ---- UI
document.getElementById("title").textContent = DATA.title;
const st = document.getElementById("stats");
for (const [k, v] of Object.entries(DATA.stats)) { const a = document.createElement("div"); a.textContent = k; const b = document.createElement("div"); b.textContent = v; st.append(a, b); }
const legend = document.getElementById("legend");
DATA.kinds.forEach((k, i) => {
  const n = B.filter(b => b[6] === i).length;
  const s = document.createElement("span"); s.innerHTML = `<i style="background:${DATA.colors[i]}"></i>${k || "beam"} (${n})`;
  s.onclick = () => { hidden.has(i) ? hidden.delete(i) : hidden.add(i); s.classList.toggle("off"); refresh(); };
  legend.append(s);
});
const progress = document.getElementById("progress");
progress.max = N; progress.value = N;
progress.oninput = () => { stop(); refresh(); };
document.getElementById("ghost").onchange = e => { if (ghost) ghost.visible = e.target.checked; };
document.getElementById("anchor").onchange = e => { helpers.visible = e.target.checked; };
document.getElementById("colorby").onchange = refresh;
document.getElementById("step").onclick = () => { stop(); progress.value = Math.min(+progress.value + 1, N); refresh(); };
let playing = false, acc = 0, last = 0;
const playBtn = document.getElementById("play");
function stop() { playing = false; playBtn.textContent = "▶ Play"; }
playBtn.onclick = () => {
  if (playing) return stop();
  if (+progress.value >= N) progress.value = 0;
  playing = true; playBtn.textContent = "❚❚ Pause"; last = performance.now();
};

function loop(t) {
  if (playing) {
    acc += (t - last) / 1000 * +document.getElementById("speed").value; last = t;
    if (acc >= 1) { progress.value = Math.min(+progress.value + Math.floor(acc), N); acc %= 1; refresh(); if (+progress.value >= N) stop(); }
  } else last = t;
  renderer.render(scene, camera);
  requestAnimationFrame(loop);
}
resize(); placeCamera(); refresh(); requestAnimationFrame(loop);
</script>
</body>
</html>
"""
