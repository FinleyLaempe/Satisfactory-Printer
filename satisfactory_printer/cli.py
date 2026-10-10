"""Command line interface.

  python -m satisfactory_printer build  model.stl --size 20 -o out/      # 3D model -> keystroke plan
  python -m satisfactory_printer build  print.gcode --scale 0.1 -o out/  # slicer output -> plan
  python -m satisfactory_printer run    out/plan.json                    # type it into the game
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from . import beams as beam_mod
from .gcode import read_gcode_segments
from .mesh import load_mesh
from .plan import build_plan, load_config, plan_to_json, plan_to_text
from .preview import ascii_layers, export_obj
from .voxelize import hollow, scale_to_grid, voxelize_solid, voxelize_surface


def _cmd_build(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.max_beam_length:
        cfg["max_beam_length"] = args.max_beam_length
    max_len = cfg["max_beam_length"]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(args.input)
    grid = None

    if src.suffix.lower() in (".gcode", ".gco", ".g"):
        segs = read_gcode_segments(src)
        if not segs:
            print("No extrusion moves found in G-code.", file=sys.stderr)
            return 1
        pts = np.array([p for seg in segs for p in seg])
        lo = pts.min(axis=0)
        factor = args.scale if args.scale else (args.size / float((pts.max(axis=0) - lo).max()) if args.size else 1.0)
        segs = [((a - lo) * factor, (b - lo) * factor) for a, b in segs]
        if args.directions == "26":
            beams = beam_mod.beams_from_segments_lattice(segs, max_len)
        else:
            beams = beam_mod.beams_from_segments(segs, max_len)
            beams = [b for b in beams if b.length >= args.min_length]
    else:
        tris = scale_to_grid(load_mesh(src), size=args.size, scale=args.scale)
        if args.mode == "wireframe":
            beams = beam_mod.beams_from_mesh_edges(
                tris, max_len, args.feature_angle, lattice=args.directions == "26"
            )
        else:
            if args.mode == "surface":
                grid = voxelize_surface(tris)
            else:
                grid = voxelize_solid(tris)
                if not grid.any():
                    print("Solid voxelization found nothing (mesh not watertight?) - falling back to surface mode.")
                    grid = voxelize_surface(tris)
                if not args.solid:
                    grid = hollow(grid)
            beams = beam_mod.beams_from_voxels(grid, int(max_len), args.axis)

    beams = beam_mod.sort_for_building(beams)
    if args.limit:
        beams = beams[: args.limit]
    if not beams:
        print("Model produced no beams (too small? try a bigger --size).", file=sys.stderr)
        return 1

    steps, offset, warnings = build_plan(beams, cfg)
    import json

    (out / "plan.json").write_text(json.dumps(plan_to_json(steps, offset, cfg), indent=1))
    (out / "keys.txt").write_text(plan_to_text(steps))
    export_obj(beams, out / "preview.obj")
    if grid is not None:
        (out / "layers.txt").write_text(ascii_layers(grid))

    presses = sum(s.key_presses for s in steps)
    per_press = cfg["key_delay"] + cfg["key_interval"]
    est = presses * per_press + sum(
        a.seconds for s in steps for a in s.actions if a.kind == "wait"
    )
    total_len = sum(b.length for b in beams)
    print(f"beams:            {len(beams)}")
    print(f"total beam metres:{total_len:8.1f}")
    print(f"key presses:      {presses}")
    print(f"est. build time:  {est / 60:.1f} min")
    print(f"model offset:     {offset} (relative to anchor)")
    if grid is not None:
        print(f"voxel grid:       {grid.shape[0]} x {grid.shape[1]} x {grid.shape[2]}  ({int(grid.sum())} voxels)")
    for w in warnings[:10]:
        print("WARNING:", w)
    if len(warnings) > 10:
        print(f"... {len(warnings) - 10} more warnings")
    print(f"written to {out}/ : plan.json, keys.txt, preview.obj" + (", layers.txt" if grid is not None else ""))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .executor import run_plan

    run_plan(args.plan, start_at=args.start_at, countdown=args.countdown, dry_run=args.dry_run)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="satisfactory_printer", description="3D model -> Satisfactory beam keystrokes")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="convert a model (.stl/.obj) or slicer G-code into a keystroke plan")
    b.add_argument("input")
    b.add_argument("-o", "--output", default="out")
    size = b.add_mutually_exclusive_group()
    size.add_argument("--size", type=float, help="largest model dimension in metres (= beams)")
    size.add_argument("--scale", type=float, help="metres per model unit (G-code: per mm)")
    b.add_argument("--mode", choices=["voxel", "surface", "wireframe"], default="voxel",
                   help="voxel: filled 1x1 beams (hollow shell by default); surface: shell from non-watertight "
                        "meshes; wireframe: one beam per mesh edge (diagonals allowed)")
    b.add_argument("--solid", action="store_true", help="voxel mode: keep interior (default hollows the model)")
    b.add_argument("--axis", choices=["auto", "x", "y", "z"], default="auto", help="beam direction in voxel mode")
    b.add_argument("--feature-angle", type=float, default=1.0, help="wireframe: skip edges flatter than this")
    b.add_argument("--directions", choices=["26", "free"], default="26",
                   help="G-code/wireframe: '26' snaps every path to the 1 m grid and only allows the 26 "
                        "directions of a 1x1x1 cube (edges, face and space diagonals); 'free' allows any "
                        "integer vector")
    b.add_argument("--min-length", type=float, default=1.0, help="G-code with --directions free: "
                   "drop beams shorter than this")
    b.add_argument("--max-beam-length", type=float, help="override config max_beam_length")
    b.add_argument("--limit", type=int, help="only emit the first N beams (for testing)")
    b.add_argument("-c", "--config", help="JSON config overriding the defaults (see config.example.json)")
    b.set_defaults(func=_cmd_build)

    r = sub.add_parser("run", help="send a plan's keystrokes to the game (run on the gaming PC)")
    r.add_argument("plan")
    r.add_argument("--start-at", type=int, default=0, help="resume from this beam index")
    r.add_argument("--countdown", type=int, default=5)
    r.add_argument("--dry-run", action="store_true", help="print beams without pressing keys")
    r.set_defaults(func=_cmd_run)

    args = p.parse_args(argv)
    if args.cmd == "build" and args.size is None and args.scale is None and not args.input.lower().endswith(
        (".gcode", ".gco", ".g")
    ):
        args.size = 10.0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
