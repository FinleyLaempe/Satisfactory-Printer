"""Export beams as Satisfactory blueprints (.sbp + .sbpcfg): place with one click, exact.

A blueprint stores every beam's exact position (cm), rotation (quaternion) and length, so
nothing is snapped to the 1 m nudge grid and every beam is turned with a face flat on the
surface. Limits:
* the blueprint designer is small (Mk1 4x4x4, Mk2 5x5x5, Mk3 6x6x6 foundations = 32/40/48 m),
  so large models are cut into tiles that you place next to / on top of each other;
* placing a blueprint needs all of its material at once.

Coordinates: our model space is x right, y forward, z up, metres. Unreal is left-handed with
X forward, Y right, Z up, centimetres -> (x, y, z) m becomes (Y=x, X=y, Z=z) * 100 cm, which
keeps the model un-mirrored. A beam actor sits at its start point and extends along its local
+X by its length (checked against beams in real save files); local Z is turned to the surface.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .beams import Beam
from .orient import face_normals

WRITER_DIR = Path(__file__).parent / "blueprint_writer"
DEFAULT_TEMPLATE = WRITER_DIR / "template" / "template.sbp"
FOUNDATION_M = 8.0
IRON_PLATE = "/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C"


@dataclass
class Tile:
    index: tuple[int, int, int]
    origin: np.ndarray  # model-space position of the blueprint origin (centre of the floor)
    beams: list[tuple[np.ndarray, np.ndarray, np.ndarray]]  # (start, end, face normal), model space


def to_unreal(v: np.ndarray) -> np.ndarray:
    """Model metres (x right, y forward, z up) -> Unreal centimetres (X fwd, Y right, Z up)."""
    v = np.asarray(v, dtype=float)
    return np.array([v[1], v[0], v[2]]) * 100.0


def quat_from_axes(x_axis: np.ndarray, z_hint: np.ndarray) -> tuple[float, float, float, float]:
    """Quaternion (x, y, z, w) of the rotation taking local X to x_axis and local Z towards z_hint."""
    x = x_axis / np.linalg.norm(x_axis)
    z = z_hint - (z_hint @ x) * x
    if np.linalg.norm(z) < 1e-6:  # no surface direction: keep a face up (or forward if vertical)
        for ref in (np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])):
            z = ref - (ref @ x) * x
            if np.linalg.norm(z) > 0.2:
                break
    z /= np.linalg.norm(z)
    y = np.cross(z, x)
    m = np.stack([x, y, z], axis=1)  # columns = local axes in world coordinates
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, qx, qy, qz = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, qx, qy, qz = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, qx, qy, qz = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, qx, qy, qz = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    return (float(qx), float(qy), float(qz), float(w))


def _clip(s: np.ndarray, e: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[float, float] | None:
    """Liang-Barsky: parameter range [t0, t1] of segment s->e inside the box [lo, hi]."""
    d = e - s
    t0, t1 = 0.0, 1.0
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if s[i] < lo[i] or s[i] >= hi[i]:
                return None
            continue
        a, b = (lo[i] - s[i]) / d[i], (hi[i] - s[i]) / d[i]
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if t0 >= t1:
            return None
    return t0, t1


def make_tiles(beams: list[Beam], normals: np.ndarray, designer: int, inset: float = 0.5,
               min_piece: float = 0.25) -> list[Tile]:
    """Cut the beams into designer-sized tiles (beams crossing a border are split there).

    inset: move every beam this far into the surface, so its outer face (not its centre line)
    lies on the model surface.
    """
    size = designer * FOUNDATION_M
    segs = []
    for b, n in zip(beams, normals):
        s = np.array(b.start, float) - inset * n
        e = np.array(b.end, float) - inset * n
        segs.append((s, e, n))
    pts = np.array([p for s, e, _ in segs for p in (s, e)])
    # tile grid centred on the model in x/y (fewest tiles), starting at its bottom in z
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    n = np.maximum(np.ceil((hi - lo) / size - 1e-9), 1)
    lo_all = (lo + hi) / 2 - n * size / 2
    lo_all[2] = lo[2]
    tiles: dict[tuple[int, int, int], Tile] = {}
    for s, e, n in segs:
        a = np.floor((np.minimum(s, e) - lo_all) / size).astype(int)
        b = np.floor((np.maximum(s, e) - lo_all) / size).astype(int)
        for i in range(a[0], b[0] + 1):
            for j in range(a[1], b[1] + 1):
                for k in range(a[2], b[2] + 1):
                    box_lo = lo_all + np.array([i, j, k]) * size
                    r = _clip(s, e, box_lo, box_lo + size)
                    if r is None:
                        continue
                    ps, pe = s + (e - s) * r[0], s + (e - s) * r[1]
                    if np.linalg.norm(pe - ps) < min_piece:
                        continue
                    key = (i, j, k)
                    if key not in tiles:
                        origin = box_lo + np.array([size / 2, size / 2, 0.0])
                        tiles[key] = Tile(key, origin, [])
                    tiles[key].beams.append((ps, pe, n))
    return [tiles[k] for k in sorted(tiles)]


def check_node() -> str | None:
    """Problem description if the Node.js writer can't run, else None."""
    if not shutil.which("node"):
        return "Node.js is not installed (https://nodejs.org) - it is needed to write blueprint files."
    if not (WRITER_DIR / "node_modules" / "@etothepii" / "satisfactory-file-parser").exists():
        return f"run once:  npm install --prefix \"{WRITER_DIR}\""
    return None


def write_tiles(tiles: list[Tile], out: Path, name: str, designer: int, template: Path | None = None,
                cost_per_m: float = 0.25, max_len: float = 40.0) -> list[dict]:
    """Write one blueprint per tile. Returns a list of {file, tile, beams, origin_m}."""
    problem = check_node()
    if problem:
        raise RuntimeError(problem)
    template = Path(template or DEFAULT_TEMPLATE)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    multi = len(tiles) > 1
    for t in tiles:
        rows = []
        for s, e, n in t.beams:
            # long pieces: split at max beam length
            length = float(np.linalg.norm(e - s))
            pieces = max(int(math.ceil(length / max_len - 1e-9)), 1)
            for p in range(pieces):
                ps = s + (e - s) * (p / pieces)
                pe = s + (e - s) * ((p + 1) / pieces)
                d_ue = to_unreal(pe - ps)
                q = quat_from_axes(d_ue, to_unreal(n))
                start_ue = to_unreal(ps - t.origin)
                rows.append([*map(float, start_ue), *q, float(np.linalg.norm(d_ue))])
        tag = f"_{t.index[0]}_{t.index[1]}_{t.index[2]}" if multi else ""
        bp_name = f"{name}{tag}"
        spec = {
            "template": {"sbp": str(template), "sbpcfg": str(template.with_suffix(".sbpcfg"))},
            "out": {"sbp": str(out / f"{bp_name}.sbp"), "sbpcfg": str(out / f"{bp_name}.sbpcfg")},
            "name": bp_name,
            "description": f"Satisfactory Printer: {name} tile {t.index}" if multi else f"Satisfactory Printer: {name}",
            "designer": designer,
            "cost_per_m": [[IRON_PLATE, cost_per_m]] if cost_per_m > 0 else [],
            "beams": rows,
        }
        spec_path = out / f".{bp_name}.spec.json"
        spec_path.write_text(json.dumps(spec))
        proc = subprocess.run(["node", str(WRITER_DIR / "write_blueprint.mjs"), str(spec_path)],
                              capture_output=True, text=True, cwd=WRITER_DIR)
        spec_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RuntimeError(f"blueprint writer failed for {bp_name}:\n{proc.stderr.strip()[-2000:]}")
        results.append({"file": f"{bp_name}.sbp", "tile": list(t.index), "beams": len(rows),
                        "origin_m": [round(float(c), 2) for c in t.origin]})
    return results


def placement_guide(results: list[dict], designer: int) -> str:
    size = designer * FOUNDATION_M
    lines = [
        f"Blueprint tiles ({len(results)}), designer {designer}x{designer}x{designer} = {size:g} m:",
        "Place tile 0_0_0 first. Every other tile goes exactly next to / above it:",
        "tile (i, j, k) = i tiles to the right (x), j tiles forward (y), k tiles up (z),",
        f"i.e. {size:g} m = {int(size // FOUNDATION_M)} foundations per tile step.",
        "",
    ]
    for r in results:
        i, j, k = r["tile"]
        lines.append(f"  {r['file']:40s} right {i * size:6g} m, forward {j * size:6g} m, up {k * size:6g} m"
                     f"   ({r['beams']} beams)")
    return "\n".join(lines) + "\n"
