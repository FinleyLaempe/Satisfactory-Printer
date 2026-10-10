"""Command line.

  python -m satisfactory_printer                              # the app (browser UI)
  python -m satisfactory_printer blueprint model.stl --size 70 --finish caterium
  python -m satisfactory_printer keys model.stl --size 40     # keyboard plan (fallback)
  python -m satisfactory_printer run out/plan.json             # type a keyboard plan into the game
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import parallel
from .pipeline import Settings, accuracy, build, export_blueprints, export_keys, stats

FINISH_CHOICES = ["caterium", "copper", "chrome", "carbonsteel"]


def _settings(args: argparse.Namespace, output: str) -> Settings:
    s = Settings(size=args.size, drop_base=args.drop_base, tolerance=args.tolerance,
                 spacing=args.spacing, finish=args.finish or "", output=output)
    if output == "blueprint":
        s.designer, s.inset, s.cost_per_m = args.designer, args.inset, args.cost_per_m
        s.template = str(args.template) if args.template else None
    else:
        s.kps, s.roll, s.config = args.kps, not args.no_roll, args.config
    return s


def _print_stats(res) -> None:
    for k, v in {**stats(res), **accuracy(res)}.items():
        print(f"{k + ':':22s}{v}")


def _cmd_blueprint(args: argparse.Namespace) -> int:
    from .blueprint import check_node

    problem = check_node()
    if problem:
        print("Blueprints können noch nicht geschrieben werden:", problem, file=sys.stderr)
        return 1
    src = Path(args.model)
    res = build(src, _settings(args, "blueprint"), args.name or src.stem)
    if not res.beams:
        print("Das Modell ergibt keine Träger (zu klein?).", file=sys.stderr)
        return 1
    out = Path(args.output)
    info = export_blueprints(res, out)
    _print_stats(res)
    print(info["guide"])
    print(f"geschrieben nach {out}/ : {len(info['files'])} × (.sbp + .sbpcfg), PLACEMENT.txt, preview.html")
    if args.open:
        webbrowser.open((out / "preview.html").resolve().as_uri())
    return 0


def _cmd_keys(args: argparse.Namespace) -> int:
    src = Path(args.model)
    res = build(src, _settings(args, "keys"), src.stem)
    if not res.beams:
        print("Das Modell ergibt keine Träger (zu klein?).", file=sys.stderr)
        return 1
    out = Path(args.output)
    export_keys(res, out)
    _print_stats(res)
    print(f"Anker-Versatz: {res.offset}")
    print(f"geschrieben nach {out}/ : plan.json, keys.txt, preview.html")
    if args.open:
        webbrowser.open((out / "preview.html").resolve().as_uri())
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .executor import run_plan

    run_plan(args.plan, start_at=args.start_at, countdown=args.countdown, dry_run=args.dry_run,
             simulate=args.simulate)
    return 0


def _cmd_app(args: argparse.Namespace) -> int:
    from .server import serve

    return serve(args)


def _shape_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("model", help=".stl or .obj model (Z up)")
    p.add_argument("-o", "--output", default="out", help="output folder")
    p.add_argument("--size", type=float, default=40.0, help="largest side of the model in metres (default 40)")
    p.add_argument("--drop-base", type=float, default=0.0, metavar="FRACTION",
                   help="remove separate parts in the bottom FRACTION of the height, e.g. 0.16 = pedestal")
    p.add_argument("--tolerance", type=float, default=1.0,
                   help="how far (m) a straightened beam may stray from a curve; lower = more detail")
    p.add_argument("--spacing", type=float, default=1.0, help="distance between beams (1 = closed surface)")
    p.add_argument("--finish", type=str.lower, choices=FINISH_CHOICES, help="metal paint finish, e.g. caterium")
    p.add_argument("--open", action="store_true", help="open the preview in the browser")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="satisfactory_printer", description="3D model -> Satisfactory beams")
    p.add_argument("-j", "--workers", type=int, help="CPU cores to use (default: all)")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("app", help="browser app (default when no command is given)")
    a.add_argument("--models", default="models", help="folder with your models (uploads go here too)")
    a.add_argument("-o", "--output", default="out", help="where exported blueprints / plans go")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--no-browser", action="store_true", help="don't open the browser")
    a.set_defaults(func=_cmd_app)

    b = sub.add_parser("blueprint", help="write blueprint files (exact, placed with one click)")
    _shape_options(b)
    b.add_argument("--name", help="blueprint name (default: model file name)")
    b.add_argument("--designer", default="auto",
                   help="blueprint size in foundations: auto = one blueprint; 6 = Mk3 tiles (48 m); AxBxC")
    b.add_argument("--inset", type=float, default=0.5, help="move beams this far into the surface (m)")
    b.add_argument("--cost-per-m", type=float, default=0.25, help="steel beams per metre for the cost list")
    b.add_argument("--template", type=Path, help="own blueprint (.sbp) with one painted beam to copy")
    b.set_defaults(func=_cmd_blueprint)

    k = sub.add_parser("keys", help="keyboard plan: type the beams into the game (fallback)")
    _shape_options(k)
    k.add_argument("--kps", type=float, default=10.0, help="keystrokes per second")
    k.add_argument("--no-roll", action="store_true", help="don't roll beams with the mouse wheel")
    k.add_argument("-c", "--config", help="JSON config: keys, macro, timings (see config.example.json)")
    k.set_defaults(func=_cmd_keys)

    r = sub.add_parser("run", help="type a keyboard plan into the game (run on the gaming PC)")
    r.add_argument("plan")
    r.add_argument("--start-at", type=int, default=0, help="resume from this beam")
    r.add_argument("--countdown", type=int, default=5)
    r.add_argument("--dry-run", action="store_true", help="only list the beams")
    r.add_argument("--simulate", action="store_true", help="real timing, but press no keys")
    r.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("app", "blueprint", "keys", "run", "-h", "--help", "-j", "--workers"):
        argv = ["app", *argv]
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        args = build_parser().parse_args([*argv, "app"])
    parallel.set_workers(args.workers)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
