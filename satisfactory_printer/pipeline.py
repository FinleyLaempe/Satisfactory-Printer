"""One pipeline for everything: model -> beams -> blueprint files or a keyboard plan.

Used by the app (server.py) and the command line (cli.py) alike.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import beams as beam_mod
from . import blueprint as bpmod
from . import surfaces
from .beams import Beam
from .mesh import fit_to_size, load_mesh
from .metrics import evaluate, fill_gaps
from .orient import face_normals, roll_steps
from .plan import apply_speed, build_plan, keystrokes_per_second, load_config, plan_to_json, plan_to_text
from .preview_html import export_html, make_preview_data

MAX_BEAM = 40.0  # longest beam the game allows (m)
FINISH_COLORS = {"caterium": "#d9a833", "copper": "#c8743f", "chrome": "#c9ced6", "carbonsteel": "#4a4d52"}


@dataclass
class Settings:
    # shape
    size: float = 40.0  # largest side of the model in metres
    tolerance: float = 1.0  # how far (m) a straightened beam may stray from a curve
    spacing: float = 1.0  # distance between neighbouring beams (1 = closed surface)
    finish: str = ""  # caterium / copper / chrome / carbonsteel / "" (none)
    # output
    output: str = "blueprint"  # "blueprint" (exact, one click) or "keys" (typed, 1 m grid)
    designer: str = "auto"  # blueprint size: auto, 6 (Mk3 tiles), AxBxC (foundations)
    inset: float = 0.5  # blueprint: beams half a beam inwards so their outer face is the surface
    cost_per_m: float = 0.25  # blueprint cost list: steel beams per metre
    template: str | None = None  # blueprint: own .sbp with a painted beam to copy
    kps: float = 10.0  # keys: keystrokes per second
    roll: bool = True  # keys: mouse-wheel roll so a face lies on the surface
    config: str | None = None  # keys: JSON config (keys, macro, timings)

    def shape_key(self) -> tuple:
        """Settings that change the beams (everything else only changes the export)."""
        return (round(self.size, 2), round(self.tolerance, 3),
                round(self.spacing, 3), self.output)


@dataclass
class Result:
    name: str
    settings: Settings
    mesh: np.ndarray  # model surface in beam coordinates (metres)
    beams: list[Beam]
    normals: np.ndarray | None = None  # blueprint: outward face direction per beam
    steps: list = field(default_factory=list)  # keys: plan steps
    offset: tuple[int, int, int] = (0, 0, 0)  # keys: model position relative to the anchor
    cfg: dict = field(default_factory=dict)  # keys: plan config


def load_model(path: str | Path, s: Settings) -> np.ndarray:
    return fit_to_size(load_mesh(path), s.size)


def build(path: str | Path, s: Settings, name: str | None = None, tris: np.ndarray | None = None) -> Result:
    """Model -> beams covering its surface (exact for blueprints, 1 m grid for keys)."""
    mesh = tris if tris is not None else load_model(path, s)
    precise = s.output == "blueprint"
    beam_mod.set_precise(precise)
    try:
        topo = surfaces.build_topology(mesh)
        beams = surfaces.beams_from_surface(topo, s.spacing, s.tolerance, MAX_BEAM)
        beams = fill_gaps(beams, mesh, MAX_BEAM)
        beams = beam_mod.merge_collinear(beams, MAX_BEAM)
    finally:
        beam_mod.set_precise(False)
    beams = beam_mod.sort_for_building(beams)
    res = Result(name or Path(path).stem, s, mesh, beams)
    if not beams:
        return res
    if precise:
        res.normals = face_normals(beams, mesh)
    else:
        res.cfg = apply_speed(load_config(s.config), s.kps)
        rolls = roll_steps(beams, mesh, float(res.cfg.get("roll_step_deg", 15))) if s.roll else None
        if not s.roll:
            res.cfg["roll"] = False
        res.steps, res.offset = build_plan(beams, res.cfg, rolls)
    return res


# --------------------------------------------------------------------------- numbers


def _keys_timing(res: Result) -> dict[str, float]:
    return {
        "presses": sum(st.key_presses for st in res.steps),
        "wait_s": round(sum(a.seconds for st in res.steps for a in st.actions if a.kind == "wait"), 2),
        "kps": round(keystrokes_per_second(res.cfg), 2) if res.cfg else 10.0,
    }


def _designer(res: Result) -> tuple[int, int, int]:
    d = bpmod.parse_designer(res.settings.designer)
    return bpmod.auto_designer(res.beams, res.normals, res.settings.inset) if d == "auto" else d


def stats(res: Result) -> dict[str, str]:
    pts = res.mesh.reshape(-1, 3)
    size = pts.max(axis=0) - pts.min(axis=0)
    total = sum(b.length for b in res.beams)
    out = {
        "Modellgröße": f"{size[0]:.0f} × {size[1]:.0f} × {size[2]:.0f} m",
        "Träger": f"{len(res.beams)}",
        "Trägerlänge gesamt": f"{total:.0f} m",
        "längster Träger": f"{max((b.length for b in res.beams), default=0):.1f} m",
    }
    if res.settings.output == "blueprint":
        if res.beams:
            d = _designer(res)
            out["Blueprint-Größe"] = f"{d[0]}×{d[1]}×{d[2]} Fundamente"
        out["Kosten (geschätzt)"] = f"{int(np.ceil(total * res.settings.cost_per_m))} Stahlträger"
    else:
        t = _keys_timing(res)
        out["Tastendrücke"] = f"{t['presses']}"
        out["Bauzeit (geschätzt)"] = f"{(t['presses'] / t['kps'] + t['wait_s']) / 60:.0f} min"
    return out


def accuracy(res: Result) -> dict[str, str]:
    m = evaluate(res.beams, res.mesh)
    return {
        "Oberfläche bedeckt": f"{m['coverage_pct']} %",
        "Abweichung": f"{m['mean_dev_m']} m (95 %: {m['p95_dev_m']} m)",
    }


# --------------------------------------------------------------------------- preview


def _shown_beams(res: Result) -> list[Beam]:
    """What gets built: blueprint beams are moved half a beam into the surface."""
    if res.settings.output != "blueprint" or res.normals is None:
        return res.beams
    d = res.settings.inset
    return [Beam(tuple(np.array(b.start) - d * n), tuple(np.array(b.end) - d * n), b.kind)
            for b, n in zip(res.beams, res.normals)]


def preview_data(res: Result, stat: dict[str, str]) -> dict:
    keys = [line.split(":  ", 1)[-1] for line in plan_to_text(res.steps).splitlines()] if res.steps else []
    data = make_preview_data(_shown_beams(res), res.mesh, res.offset, stat, keys, title=res.name)
    data["output"] = res.settings.output
    if res.settings.output == "blueprint" and res.normals is not None:
        data["faces"] = res.normals.round(4).tolist()
    elif res.steps:
        data["timing"] = _keys_timing(res)
        step_deg = float(res.cfg.get("roll_step_deg", 15))
        # the plan may draw a beam from its other end (nearer end last): flips the roll sign
        data["rolls"] = [
            (st.roll if st.beam.start == b.translated(res.offset).start else -st.roll) * step_deg
            for st, b in zip(res.steps, res.beams)
        ]
    if res.settings.finish in FINISH_COLORS:
        data["colors"] = [FINISH_COLORS[res.settings.finish]] * len(data["kinds"])
    return data


# --------------------------------------------------------------------------- export


def export_blueprints(res: Result, out: Path, name: str | None = None) -> dict:
    """Write blueprint file(s) + PLACEMENT.txt + preview.html into `out`."""
    s = res.settings
    if s.output != "blueprint" or res.normals is None:
        raise ValueError("build with output='blueprint' first")
    designer = _designer(res)
    tiles = bpmod.make_tiles(res.beams, res.normals, designer, inset=s.inset)
    name = name or res.name
    files = bpmod.write_tiles(tiles, out, name, designer, s.template, s.cost_per_m, MAX_BEAM,
                              finish=s.finish or None)
    guide = bpmod.placement_guide(files, designer)
    (out / "PLACEMENT.txt").write_text(guide)
    export_html(out / "preview.html", preview_data(res, {**stats(res), **accuracy(res)}))
    return {"files": files, "guide": guide, "designer": list(designer)}


def export_keys(res: Result, out: Path, kps: float | None = None) -> dict:
    """Write plan.json + keys.txt + preview.html into `out` (typing speed `kps`)."""
    if res.settings.output != "keys":
        raise ValueError("build with output='keys' first")
    cfg = apply_speed(res.cfg, kps) if kps else res.cfg
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan_to_json(res.steps, res.offset, cfg), indent=1))
    (out / "keys.txt").write_text(plan_to_text(res.steps))
    export_html(out / "preview.html", preview_data(res, {**stats(res), **accuracy(res)}))
    return {"plan": str(out / "plan.json")}

