import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from make_examples import cube, house, ramp, rocket, save_stl_ascii, sphere  # noqa: E402
from satisfactory_printer import blueprint as bpmod  # noqa: E402
from satisfactory_printer import surfaces  # noqa: E402
from satisfactory_printer.beams import Beam, merge_collinear, set_precise, simplify_polyline, split_segment  # noqa: E402
from satisfactory_printer.cli import main  # noqa: E402
from satisfactory_printer.mesh import fit_to_size  # noqa: E402
from satisfactory_printer.metrics import evaluate, fill_gaps  # noqa: E402
from satisfactory_printer.pipeline import Settings, build, export_blueprints, preview_data, stats  # noqa: E402
from satisfactory_printer.plan import DEFAULT_CONFIG, build_plan  # noqa: E402

needs_node = pytest.mark.skipif(bool(bpmod.check_node()), reason="node / blueprint writer not installed")


def _rot(q, v):
    x, y, z, w = q
    u = np.array([x, y, z])
    return v + 2 * w * np.cross(u, v) + 2 * np.cross(u, np.cross(u, v))


# --------------------------------------------------------------------------- model


def test_fit_to_size():
    t = fit_to_size(cube(1.0) * np.array([2, 4, 1]) + 5, 20)
    pts = t.reshape(-1, 3)
    assert np.allclose(pts.min(axis=0), 0) and np.isclose(pts.max(), 20)


# --------------------------------------------------------------------------- beam helpers


def test_split_respects_max_length():
    pieces = split_segment(np.array([0, 0, 0]), np.array([90, 17, 3]), 40)
    assert all(p.length <= 40 for p in pieces)
    assert pieces[0].start == (0, 0, 0) and pieces[-1].end == (90, 17, 3)


def test_simplify_keeps_corners_and_drops_noise():
    xs = np.arange(0, 21)
    line = np.stack([xs, (xs > 10) * (xs - 10), np.zeros_like(xs)], axis=1).astype(float)
    line[3, 1] += 0.2  # noise below tolerance
    simple = simplify_polyline(line, tol=0.5)
    assert [tuple(p) for p in simple] == [(0, 0, 0), (10, 0, 0), (20, 10, 0)]


def test_merge_collinear_fuses_overlaps_and_respects_max_len():
    beams = [Beam((0, 0, 0), (10, 0, 0)), Beam((5, 0, 0), (20, 0, 0)), Beam((20, 0, 0), (0, 0, 0)),
             Beam((0, 0, 0), (2, 2, 0)), Beam((2, 2, 0), (6, 6, 0)), Beam((0, 1, 0), (5, 1, 0))]
    vecs = sorted(tuple(abs(c) for c in b.vector) for b in merge_collinear(beams, max_len=40))
    assert vecs == [(5, 0, 0), (6, 6, 0), (20, 0, 0)]
    assert len(merge_collinear([Beam((0, 0, 0), (30, 0, 0)), Beam((30, 0, 0), (60, 0, 0))], 40)) == 2


def test_precise_mode_keeps_exact_points():
    set_precise(True)
    try:
        (b,) = split_segment(np.array([0.25, 0, 0]), np.array([10.6, 0.3, 0]), 40)
    finally:
        set_precise(False)
    assert b.start == (0.25, 0.0, 0.0) and b.end == (10.6, 0.3, 0.0)


# --------------------------------------------------------------------------- surfaces


def test_planar_regions_of_house():
    topo = surfaces.build_topology(fit_to_size(house(), 20))
    regions, curved = surfaces.find_planar_regions(topo)
    assert len(regions) == 7 and not curved.any()  # 4 walls + floor + 2 roof planes


def test_sloped_plane_gets_diagonal_beams():
    """The ramp's 30° slope is built from beams running down the slope, not a staircase."""
    topo = surfaces.build_topology(fit_to_size(ramp(), 20))
    regions, _ = surfaces.find_planar_regions(topo)
    slope = next(r for r in regions if 0.1 < abs(r.normal[2]) < 0.95)
    beams = surfaces.hatch_region(topo, slope, 1.0, 40)
    assert beams and all(b.vector[0] != 0 and b.vector[2] != 0 for b in beams)
    assert max(b.length for b in beams) > 15


def test_house_is_accurate_with_edges():
    tris = fit_to_size(house(), 20)
    beams = merge_collinear(surfaces.beams_from_surface(surfaces.build_topology(tris)))
    m = evaluate(beams, tris)
    assert m["coverage_pct"] > 97 and m["mean_dev_m"] < 0.4 and m["stray_pct"] < 1
    assert any(b.kind == "edge" for b in beams)


def test_rocket_body_gets_vertical_staves():
    tris = fit_to_size(rocket(), 30)
    beams = surfaces.beams_from_contours(tris)
    long = [b for b in beams if b.length >= 3]
    assert long and sum(abs(b.vector[2]) / b.length > 0.7 for b in long) > 0.6 * len(long)


def test_sphere_is_closed_after_gap_filling():
    tris = fit_to_size(sphere(), 16)
    beams = fill_gaps(surfaces.beams_from_contours(tris), tris)
    m = evaluate(beams, tris)
    assert m["coverage_pct"] >= 99.5 and m["stray_pct"] < 2


# --------------------------------------------------------------------------- keyboard plan + roll


def test_plan_nudges_reproduce_beam():
    cfg = dict(DEFAULT_CONFIG, model_offset=[0, 0, 0])
    beam = Beam((2, 3, 1), (5, -1, 4))
    (step,), _ = build_plan([beam], cfg)
    inverse = {v: k for k, v in cfg["keys"].items()}
    pos, vectors = np.zeros(3, int), []
    for a in step.actions:
        if a.kind == "key" and a.key in inverse and inverse[a.key] != "lock":
            pos["xyz".index(inverse[a.key][1])] += (1 if inverse[a.key][0] == "+" else -1) * a.times
        elif a.kind == "click":
            vectors.append(pos.copy())
            pos[:] = 0
    shape, place = vectors
    # drawn from (anchor + shape) to the anchor, then moved by place; either end may go last
    assert {tuple(shape + place), tuple(place)} == {beam.start, beam.end}


def test_planned_wheel_notches_turn_a_face_onto_the_surface():
    """Wheel up = counter-clockwise from the tip about start->end, 15° per notch."""
    from satisfactory_printer.orient import reference_face, roll_steps

    ang = np.radians(30)
    normal = np.array([0, -np.sin(ang), np.cos(ang)])
    tri = np.array([[[-80, 0, 0], [80, 0, 0], [0, 10 * np.cos(ang), 10 * np.sin(ang)]]], float)
    beams = [Beam((0, 2, 1), (10, 2, 1)), Beam((10, 2, 1), (0, 2, 1))]
    steps, _ = build_plan(beams, dict(DEFAULT_CONFIG, model_offset=[30, 5, 0]), roll_steps(beams, tri))
    for st in steps:
        notches = sum(a.times for a in st.actions if a.kind == "scroll")
        axis = np.subtract(st.beam.end, st.beam.start).astype(float)
        axis /= np.linalg.norm(axis)
        r, a = reference_face(axis), np.radians(15 * notches)
        face = r * np.cos(a) + np.cross(axis, r) * np.sin(a) + axis * (axis @ r) * (1 - np.cos(a))
        c = abs(face @ normal)
        assert c > 0.99 or c < 0.01


def test_runner_pause_and_stop_between_beams():
    from satisfactory_printer.executor import PlanRunner

    beam = {"actions": [{"key": "h", "times": 1}, {"wait": 0.05}]}
    plan = {"timing": {"key_delay": 0.001, "key_interval": 0.001},
            "beams": [dict(beam, index=i, start=[0, 0, 0], end=[1, 0, 0]) for i in range(200)]}
    r = PlanRunner(plan, countdown=0, simulate=True, log=lambda *_: None)
    r.start_thread()
    time.sleep(0.3)
    r.pause()
    time.sleep(0.2)
    placed = r.index
    time.sleep(0.2)
    assert r.status == "paused" and r.index == placed
    r.stop()
    time.sleep(0.2)
    assert r.status == "stopped" and 0 < r.index < 200


# --------------------------------------------------------------------------- blueprint


def test_quaternion_puts_local_x_on_the_beam_and_z_on_the_surface():
    rng = np.random.default_rng(5)
    for _ in range(50):
        d, n = rng.normal(size=3), rng.normal(size=3)
        q = bpmod.quat_from_axes(d, n)
        assert abs(np.linalg.norm(q) - 1) < 1e-9
        assert np.allclose(_rot(q, np.array([1.0, 0, 0])), d / np.linalg.norm(d), atol=1e-9)
        n_perp = n - (n @ d) * d / (d @ d)
        assert np.allclose(_rot(q, np.array([0, 0, 1.0])), n_perp / np.linalg.norm(n_perp), atol=1e-9)


def test_real_save_beam_convention():
    """A vertical beam from a real save maps local X to +Z."""
    q = bpmod.quat_from_axes(np.array([0, 0, 1.0]), np.array([1.0, 0, 0]))
    assert np.allclose(_rot(q, np.array([1.0, 0, 0])), [0, 0, 1])


def test_tiles_split_beams_at_borders_and_auto_size():
    beams = [Beam((0.0, 0.0, 1.0), (100.0, 0.0, 1.0)), Beam((1.0, 1.0, 0.0), (1.0, 1.0, 10.0))]
    normals = np.array([[0, 0, 1.0], [1.0, 0, 0]])
    total = 0.0
    for t in bpmod.make_tiles(beams, normals, designer=(6, 6, 6), inset=0):
        for s, e, _ in t.beams:
            for p in (s - t.origin, e - t.origin):  # inside the designer box (origin: floor centre)
                assert -24 - 1e-6 <= p[0] <= 24 + 1e-6 and -24 - 1e-6 <= p[1] <= 24 + 1e-6
                assert -1e-6 <= p[2] <= 48 + 1e-6
            total += np.linalg.norm(e - s)
    assert abs(total - 110) < 1e-6
    assert bpmod.parse_designer("11x11x15") == (11, 11, 15)
    d = bpmod.auto_designer(beams, normals, inset=0)
    assert d == (13, 1, 2) and len(bpmod.make_tiles(beams, normals, d, inset=0)) == 1


# --------------------------------------------------------------------------- pipeline / cli / app


def test_pipeline_outputs(tmp_path):
    stl = tmp_path / "rocket.stl"
    save_stl_ascii(stl, rocket())
    bp = build(stl, Settings(size=20, finish="caterium"))
    keys = build(stl, Settings(size=20, output="keys"))
    assert bp.beams and bp.normals is not None and keys.steps
    assert any(not float(c).is_integer() for b in bp.beams for c in b.start)  # exact
    assert all(float(c).is_integer() for b in keys.beams for c in b.start)  # 1 m grid
    data = preview_data(bp, stats(bp))
    assert data["faces"] and data["colors"][0] == "#d9a833"
    assert "Blueprint-Größe" in stats(bp) and "Tastendrücke" in stats(keys)


@needs_node
def test_blueprint_files_roundtrip(tmp_path):
    stl = tmp_path / "rocket.stl"
    save_stl_ascii(stl, rocket())
    out = tmp_path / "bp"
    assert main(["blueprint", str(stl), "--size", "20", "--finish", "caterium", "-o", str(out)]) == 0
    sbp = list(out.glob("*.sbp"))
    assert sbp and all(p.with_suffix(".sbpcfg").exists() for p in sbp)
    assert (out / "PLACEMENT.txt").exists() and (out / "preview.html").exists()


def test_cli_keys(tmp_path):
    stl = tmp_path / "h.stl"
    save_stl_ascii(stl, house())
    out = tmp_path / "k"
    assert main(["keys", str(stl), "--size", "12", "-o", str(out)]) == 0
    plan = json.loads((out / "plan.json").read_text())
    assert plan["beams"] and all(b["length"] <= 40 for b in plan["beams"])
    assert "const APP = null" in (out / "preview.html").read_text()


def test_app_builds_exports_and_types(tmp_path):
    from satisfactory_printer.server import App

    app = App(tmp_path / "models", tmp_path / "out")
    up = app.upload("my rocket.stl", Path(ROOT / "examples" / "rocket.stl").read_bytes())
    q = lambda **kw: {k: [str(v)] for k, v in kw.items()}  # noqa: E731
    model, s, run = app.params(q(model=up["id"], size=15, finish="caterium"))
    data = app.preview(model, s)
    assert data["beams"] and data["output"] == "blueprint"
    assert "Oberfläche bedeckt" in app.accuracy(model, s)
    if not bpmod.check_node():
        info = app.export(model, s, run)
        from urllib.parse import unquote

        names = zipfile.ZipFile(app.download(unquote(info["download"].split("=", 1)[1]))).namelist()
        assert any(n.endswith(".sbp") for n in names)

    model, s, run = app.params(q(model=up["id"], size=8, output="keys", kps=100, countdown=0, simulate=1))
    st = app.run_start(model, s, run, "")
    assert st["status"] in ("ready", "countdown", "running")
    app.run_control("stop")
    deadline = time.time() + 20
    while app.run_status()["status"] not in ("stopped", "done") and time.time() < deadline:
        time.sleep(0.05)
    assert app.run_status()["status"] in ("stopped", "done")


def test_parallel_matches_serial():
    from satisfactory_printer import metrics, parallel

    rng = np.random.default_rng(3)
    q, t = rng.random((60_000, 3)) * 50, rng.random((50_000, 3)) * 50
    parallel.set_workers(1)
    serial = metrics.nearest_distance(q, t, 0.61)
    parallel.set_workers(4)
    try:
        assert np.array_equal(serial, metrics.nearest_distance(q, t, 0.61))
    finally:
        parallel.set_workers(None)
