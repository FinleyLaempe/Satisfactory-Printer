"""Command line interface.

  python -m satisfactory_printer build   model.stl --size 30 -o out   # 3D model -> keystroke plan
  python -m satisfactory_printer compare model.stl --size 30 -o cmp   # try every mode
  python -m satisfactory_printer serve   model.stl                    # live preview with sliders
  python -m satisfactory_printer run     out/plan.json                # type it into the game
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from . import beams as beam_mod
from . import surfaces
from .mesh import load_mesh
from .plan import apply_speed, build_plan, keystrokes_per_second, load_config, plan_to_json, plan_to_text
from .preview import ascii_layers, export_obj
from .preview_html import export_html, make_preview_data
from .voxelize import hollow, scale_to_grid, voxelize_solid, voxelize_surface

MODES = ["hybrid", "planes", "contour", "voxel", "wireframe"]


def generate(tris, args, max_len: float):
    """Run the selected generator. Returns (beams, mesh in beam coordinates, voxel grid or None)."""
    mode = args.mode
    if mode == "voxel":
        grid = None
        if args.voxelize in ("auto", "solid"):
            grid = voxelize_solid(tris)
            if not grid.any():
                if args.voxelize == "solid":
                    raise SystemExit("Solid voxelization found nothing - is the mesh watertight?")
                print("Solid voxelization found nothing (mesh not watertight?) - using the surface instead.")
                grid = None
            elif not args.solid:
                grid = hollow(grid)
        if grid is None:
            grid = voxelize_surface(tris)
        axis = None if args.axis == "auto" else args.axis
        beams = beam_mod.beams_from_voxels(grid, max_len, args.directions, axis)
        # voxel (i,j,k) spans [i, i+1) in mesh space, its centre (= beam line) is i+0.5
        return beams, tris - 0.5, grid

    topo = surfaces.build_topology(tris)
    if mode == "wireframe":
        beams = surfaces.beams_from_mesh_edges(topo, max_len, args.feature_angle, args.directions == "26")
    elif mode == "planes":
        beams, curved = surfaces.beams_from_planes(
            topo, args.spacing, max_len, args.tolerance, args.hatch, args.min_plane_area
        )
        if curved.any():
            print(f"note: {int(curved.sum())} faces aren't part of a flat region - try --mode hybrid for those")
    elif mode == "contour":
        beams = surfaces.beams_from_contours(
            tris, args.ring_spacing, args.rib_spacing, args.contour_axes, args.tolerance, max_len, args.pattern
        )
    else:
        beams = surfaces.beams_hybrid(
            topo, args.spacing, args.ring_spacing, args.rib_spacing, args.tolerance, max_len,
            args.hatch, args.min_plane_area, args.edge_angle, pattern=args.pattern,
        )
    return beams, tris, None


@dataclass
class BuildResult:
    name: str
    mode: str
    beams: list
    mesh: np.ndarray  # original surface in beam coordinates
    grid: np.ndarray | None
    raw_count: int
    steps: list
    offset: tuple[int, int, int]
    warnings: list[str]
    cfg: dict
    key_text: str


def run_build(raw_tris: np.ndarray, args: argparse.Namespace, name: str = "model") -> BuildResult:
    """Scale the model, generate + merge + order beams and turn them into a keystroke plan."""
    cfg = apply_speed(load_config(args.config), getattr(args, "kps", None))
    if args.max_beam_length:
        cfg["max_beam_length"] = args.max_beam_length
    max_len = float(cfg["max_beam_length"])
    tris = scale_to_grid(raw_tris, size=args.size, scale=args.scale)
    beams, mesh, grid = generate(tris, args, max_len)
    if args.mode in ("hybrid", "planes", "contour") and not getattr(args, "no_fill", False):
        from .metrics import fill_gaps

        beams = fill_gaps(beams, mesh, max_len)
    raw_count = len(beams)
    if not args.no_merge:
        beams = beam_mod.merge_collinear(beams, max_len)
    beams = beam_mod.sort_for_building(beams)
    if args.limit:
        beams = beams[: args.limit]
    rolls = None
    if beams and cfg.get("roll", True) and not getattr(args, "no_roll", False):
        from .orient import roll_steps

        rolls = roll_steps(beams, mesh, float(cfg.get("roll_step_deg", 15)))
    else:
        cfg = {**cfg, "roll": False}
    steps, offset, warnings = build_plan(beams, cfg, rolls) if beams else ([], (0, 0, 0), [])
    return BuildResult(name, args.mode, beams, mesh, grid, raw_count, steps, offset, warnings, cfg,
                       plan_to_text(steps) if steps else "")


def with_speed(res: BuildResult, kps: float | None) -> BuildResult:
    return replace(res, cfg=apply_speed(res.cfg, kps))


def _timing(res: BuildResult) -> dict[str, float]:
    return {
        "presses": sum(s.key_presses for s in res.steps),
        "wait_s": round(sum(a.seconds for s in res.steps for a in s.actions if a.kind == "wait"), 2),
        "kps": round(keystrokes_per_second(res.cfg), 2),
    }


def summary_stats(res: BuildResult) -> dict[str, str]:
    t = _timing(res)
    presses = t["presses"]
    est = presses / t["kps"] + t["wait_s"]
    pts = res.mesh.reshape(-1, 3)
    size = pts.max(axis=0) - pts.min(axis=0)
    beams = res.beams
    return {
        "mode": res.mode,
        "model size": f"{size[0]:.0f} × {size[1]:.0f} × {size[2]:.0f} m",
        "beams": f"{len(beams)}" + (f" (merged from {res.raw_count})" if res.raw_count != len(beams) else ""),
        "diagonal beams": f"{sum(1 for b in beams if sum(1 for c in b.vector if c) > 1)}",
        "longest beam": f"{max((b.length for b in beams), default=0):.1f} m",
        "total beam length": f"{sum(b.length for b in beams):.0f} m",
        "key presses": f"{presses}",
        "est. build time": f"{est / 60:.1f} min",
    }


def metric_stats(res: BuildResult) -> dict[str, str]:
    from .metrics import evaluate

    m = evaluate(res.beams, res.mesh)
    return {
        "surface coverage": f"{m['coverage_pct']}%",
        "mean deviation": f"{m['mean_dev_m']} m (95%: {m['p95_dev_m']} m)",
        "stray beam length": f"{m['stray_pct']}%",
    }


def write_outputs(res: BuildResult, out: Path, stats: dict[str, str]) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan_to_json(res.steps, res.offset, res.cfg), indent=1))
    (out / "keys.txt").write_text(res.key_text)
    export_obj(res.beams, out / "preview.obj")
    files = ["plan.json", "keys.txt", "preview.html", "preview.obj"]
    if res.grid is not None:
        (out / "layers.txt").write_text(ascii_layers(res.grid))
        files.append("layers.txt")
    export_html(out / "preview.html", preview_data(res, stats))
    return files


def preview_data(res: BuildResult, stats: dict[str, str]) -> dict:
    data = make_preview_data(
        res.beams, res.mesh, res.offset, stats,
        [line.split(":  ", 1)[-1] for line in res.key_text.splitlines()],
        title=f"{res.name} – {res.mode}",
    )
    data["timing"] = _timing(res)
    step_deg = float(res.cfg.get("roll_step_deg", 15))
    # preview draws the beams start->end as generated; the plan may have drawn a beam the
    # other way round (nearer end last), which flips the sign of its roll
    data["rolls"] = [
        (s.roll if s.beam.start == b.translated(res.offset).start else -s.roll) * step_deg
        for s, b in zip(res.steps, res.beams)
    ]
    return data


def _cmd_build(args: argparse.Namespace) -> int:
    src = Path(args.input)
    res = run_build(load_mesh(src), args, src.name)
    if not res.beams:
        print("Model produced no beams (too small? try a bigger --size).", file=sys.stderr)
        return 1
    stats = summary_stats(res)
    if not args.no_metrics:
        stats.update(metric_stats(res))
    out = Path(args.output)
    files = write_outputs(res, out, stats)

    for k, v in stats.items():
        print(f"{k + ':':20s}{v}")
    print(f"{'model offset:':20s}{res.offset} (relative to anchor)")
    for w in res.warnings[:10]:
        print("WARNING:", w)
    if len(res.warnings) > 10:
        print(f"... {len(res.warnings) - 10} more warnings")
    print(f"written to {out}/ : {', '.join(files)}")
    if args.open:
        webbrowser.open((out / "preview.html").resolve().as_uri())
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    """Build the model with every mode, one output folder per mode."""
    base = Path(args.output)
    for mode in MODES:
        argv = ["build", args.input, "-o", str(base / mode), "--mode", mode, "--size", str(args.size)]
        if args.scale:
            argv = argv[:-2] + ["--scale", str(args.scale)]
        if args.config:
            argv += ["-c", args.config]
        print(f"\n=== {mode} ===")
        _cmd_build(_build_parser().parse_args(argv))
    print("\nPreviews:\n" + "\n".join(f"  {base / m / 'preview.html'}" for m in MODES))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    return serve(args)


def _cmd_run(args: argparse.Namespace) -> int:
    from .executor import run_plan

    run_plan(args.plan, start_at=args.start_at, countdown=args.countdown, dry_run=args.dry_run,
             simulate=args.simulate)
    return 0


def _add_model_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", help=".stl or .obj model (Z up)")
    p.add_argument("-j", "--workers", type=int,
                   help="CPU cores to use for the calculations (default: all; 1 = no parallelism)")
    p.add_argument("-o", "--output", default="out")
    size = p.add_mutually_exclusive_group()
    size.add_argument("--size", type=float, help="largest model dimension in metres (default 20)")
    size.add_argument("--scale", type=float, help="metres per model unit")
    p.add_argument("-c", "--config", help="JSON config overriding the defaults (see config.example.json)")


def _add_build_options(b: argparse.ArgumentParser) -> None:
    b.add_argument("--mode", choices=MODES, default="hybrid",
                   help="hybrid (default): flat areas filled with in-plane beams, curved areas with "
                        "rings+ribs, sharp edges traced. planes / contour: only that part. "
                        "voxel: blocky, filled 1 m cubes. wireframe: one beam per mesh edge")
    g = b.add_argument_group("surface modes (hybrid / planes / contour)")
    g.add_argument("--tolerance", type=float, default=0.5,
                   help="how far (m) a straightened beam may deviate from a curve; higher = fewer, longer beams")
    g.add_argument("--spacing", type=float, default=1.0,
                   help="distance between parallel beams on flat areas (1 = closed surface, 2+ = open lattice)")
    g.add_argument("--hatch", choices=["auto", "level", "slope"], default="auto",
                   help="beam direction on flat areas. auto: down the slope on tilted planes (smooth diagonals), "
                        "fewest beams on vertical/horizontal ones; level: always horizontal; slope: always down the slope")
    g.add_argument("--min-plane-area", type=float, default=6.0, help="smallest flat area (m²) treated as a plane")
    g.add_argument("--ring-spacing", type=float, default=1.0,
                   help="distance between contour slices (1 = closed surface, 2+ = open lattice)")
    g.add_argument("--rib-spacing", type=float, default=0.0,
                   help="add ribs: radial beams up the surface, this far apart at the widest point (0 = none)")
    g.add_argument("--pattern", choices=["flow", "rings"], default="flow",
                   help="curved surfaces: flow = staves running along each part (up a rocket body, along "
                        "a twisted streak); rings = horizontal rings / arcs")
    g.add_argument("--contour-axes", default="auto",
                   help="contour mode: 'auto' picks the best slicing axis per face; or e.g. z (rings), xyz")
    g.add_argument("--edge-angle", type=float, default=30.0,
                   help="hybrid: faces meeting at more than this angle form a sharp edge")
    v = b.add_argument_group("voxel / wireframe modes")
    v.add_argument("--directions", choices=["26", "6"], default="26",
                   help="26 = straight + diagonal beams, 6 = axis-aligned only")
    v.add_argument("--voxelize", choices=["auto", "solid", "surface"], default="auto",
                   help="voxel: solid needs a watertight mesh; surface works with any mesh")
    v.add_argument("--solid", action="store_true", help="voxel: keep the interior (default hollows the model)")
    v.add_argument("--axis", choices=["auto", "x", "y", "z"], default="auto", help="voxel: force one beam direction")
    v.add_argument("--feature-angle", type=float, default=1.0, help="wireframe: skip edges flatter than this")
    o = b.add_argument_group("output")
    o.add_argument("--max-beam-length", type=float, help="override config max_beam_length (default 40)")
    o.add_argument("--kps", type=float, help="typing speed in keystrokes per second (default from config: 10)")
    o.add_argument("--no-merge", action="store_true", help="don't fuse collinear overlapping beams")
    o.add_argument("--no-roll", action="store_true",
                   help="don't roll beams with the mouse wheel to lie flat on the surface")
    o.add_argument("--no-fill", action="store_true",
                   help="surface modes: skip the final pass that patches every visible hole")
    o.add_argument("--no-metrics", action="store_true", help="skip the coverage/deviation measurement")
    o.add_argument("--limit", type=int, help="only emit the first N beams (for testing)")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="satisfactory_printer", description="3D model -> Satisfactory beam keystrokes")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="convert a model into a keystroke plan + browser preview")
    _add_model_args(b)
    _add_build_options(b)
    b.add_argument("--open", action="store_true", help="open preview.html in the browser")
    b.set_defaults(func=_cmd_build)

    sv = sub.add_parser("serve", help="live browser preview with sliders for size, mode, tolerance, spacing")
    _add_model_args(sv)
    _add_build_options(sv)
    sv.add_argument("--port", type=int, default=8765, help="local port (0 = any free port)")
    sv.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    sv.set_defaults(func=_cmd_serve)

    c = sub.add_parser("compare", help="build with every mode and compare beams / time / accuracy")
    _add_model_args(c)
    c.set_defaults(func=_cmd_compare)

    r = sub.add_parser("run", help="send a plan's keystrokes to the game (run on the gaming PC)")
    r.add_argument("plan")
    r.add_argument("--start-at", type=int, default=0, help="resume from this beam index")
    r.add_argument("--countdown", type=int, default=5)
    r.add_argument("--dry-run", action="store_true", help="print beams without pressing keys")
    r.add_argument("--simulate", action="store_true", help="run with real timing but press no keys")
    r.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if getattr(args, "workers", None) is not None or args.cmd != "run":
        from . import parallel

        parallel.set_workers(getattr(args, "workers", None))
    if args.cmd in ("build", "compare", "serve") and args.size is None and args.scale is None:
        args.size = 20.0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
