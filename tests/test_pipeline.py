import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from make_examples import cube, house, ramp, save_stl_ascii, sphere  # noqa: E402
from satisfactory_printer import surfaces  # noqa: E402
from satisfactory_printer.beams import (  # noqa: E402
    Beam,
    beams_from_segments_lattice,
    beams_from_voxels,
    lattice_walk,
    merge_collinear,
    simplify_polyline,
    split_segment,
)
from satisfactory_printer.cli import main  # noqa: E402
from satisfactory_printer.metrics import evaluate  # noqa: E402
from satisfactory_printer.plan import DEFAULT_CONFIG, build_plan  # noqa: E402
from satisfactory_printer.voxelize import hollow, scale_to_grid, voxelize_solid  # noqa: E402


def _lattice_points(b: Beam):
    v = np.array(b.vector)
    n = int(np.abs(v).max())
    return {tuple(np.array(b.start) + v // n * k) for k in range(n + 1)}


# --------------------------------------------------------------------------- voxels


def test_cube_voxelizes_solid():
    grid = voxelize_solid(scale_to_grid(cube(), size=4))
    assert grid.shape == (4, 4, 4) and grid.all()


def test_hollow_removes_interior():
    grid = np.ones((5, 5, 5), dtype=bool)
    assert hollow(grid).sum() == 5**3 - 3**3


def test_voxel_beams_cover_every_voxel_and_stay_inside():
    rng = np.random.default_rng(0)
    grid = rng.random((9, 8, 7)) > 0.4
    filled = {tuple(p) for p in np.argwhere(grid)}
    beams = beams_from_voxels(grid, max_len=5)
    covered = set()
    for b in beams:
        assert 1 <= b.length <= 5 + 1e-9
        assert max(abs(c) for c in b.vector) == min(abs(c) for c in b.vector if c)  # cube direction
        pts = _lattice_points(b)
        covered |= pts
        if len(pts & filled) > 1:  # (isolated voxels get a 1 m stub into empty space)
            assert pts <= filled
    assert filled <= covered


def test_voxel_mixes_axes_and_uses_long_beams():
    grid = np.zeros((30, 30, 30), dtype=bool)
    grid[:, 0, 0] = True  # 30 m along x
    grid[0, :, 5] = True  # 30 m along y
    grid[5, 5, :] = True  # 30 m along z
    beams = beams_from_voxels(grid, max_len=40, directions="6")
    assert len(beams) == 3
    assert sorted(b.length for b in beams) == [29, 29, 29]


def test_voxel_diagonal_staircase_becomes_one_beam():
    grid = np.zeros((10, 3, 10), dtype=bool)
    for i in range(10):
        grid[i, 1, i] = True  # a 45° slope, one voxel thick
    beams = beams_from_voxels(grid, max_len=40)
    assert len(beams) == 1 and beams[0].vector in ((9, 0, 9), (-9, 0, -9))


# --------------------------------------------------------------------------- helpers


def test_lattice_walk_is_26_connected():
    pts = lattice_walk(np.array([0.5, 0.5, 0.5]), np.array([17.3, -6.5, 9.5]))
    for p, q in zip(pts, pts[1:]):
        assert max(abs(a - b) for a, b in zip(p, q)) == 1


def test_lattice_beams_only_use_cube_directions():
    segs = [
        (np.array([0, 0, 0]), np.array([50, 0, 0])),  # longer than max_len
        (np.array([0, 0, 0]), np.array([5, 5, 5])),
        (np.array([0, 0, 0]), np.array([7, 3, 1])),
    ]
    beams = beams_from_segments_lattice(segs, max_len=40)
    for b in beams:
        v = np.abs(np.array(b.vector))
        assert set(np.unique(v)) <= {0, v.max()} and b.length <= 40 + 1e-9
    assert Beam((0, 0, 0), (5, 5, 5)) in beams


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
    merged = merge_collinear(beams, max_len=40)
    vecs = sorted(tuple(abs(c) for c in b.vector) for b in merged)
    assert vecs == [(5, 0, 0), (6, 6, 0), (20, 0, 0)]
    long = merge_collinear([Beam((0, 0, 0), (30, 0, 0)), Beam((30, 0, 0), (60, 0, 0))], max_len=40)
    assert len(long) == 2


# --------------------------------------------------------------------------- surfaces


def test_planar_regions_of_house():
    topo = surfaces.build_topology(scale_to_grid(house(), size=20))
    regions, curved = surfaces.find_planar_regions(topo)
    assert len(regions) == 7  # 4 walls + floor + 2 roof planes (gables merge with end walls)
    assert not curved.any()


def test_sloped_plane_gets_diagonal_beams():
    """The ramp's 30° slope must be built from beams running down the slope, not a staircase."""
    tris = scale_to_grid(ramp(), size=20)
    topo = surfaces.build_topology(tris)
    regions, _ = surfaces.find_planar_regions(topo)
    slope = next(r for r in regions if 0.1 < abs(r.normal[2]) < 0.95)
    beams = surfaces.hatch_region(topo, slope, 1.0, 40)
    assert beams and all(b.vector[0] != 0 and b.vector[2] != 0 for b in beams)  # rise along x and z
    assert max(b.length for b in beams) > 15


def test_hybrid_house_is_accurate():
    tris = scale_to_grid(house(), size=20)
    beams = merge_collinear(surfaces.beams_hybrid(surfaces.build_topology(tris)))
    m = evaluate(beams, tris)
    assert m["coverage_pct"] > 97 and m["mean_dev_m"] < 0.4 and m["stray_pct"] < 1
    assert any(b.kind == "edge" for b in beams)


def test_contour_sphere_is_closed_surface():
    from satisfactory_printer.metrics import fill_gaps

    tris = scale_to_grid(sphere(), size=16)
    rings = surfaces.beams_from_contours(tris, pattern="rings")
    assert {b.kind for b in rings} <= {"ring", "arc"}
    staves = surfaces.beams_from_contours(tris, pattern="flow")
    assert any(b.kind == "stave" for b in staves)
    closed = fill_gaps(staves, tris)
    m = evaluate(closed, tris)
    assert m["coverage_pct"] >= 99.5 and m["stray_pct"] < 2


def test_rocket_body_gets_vertical_staves():
    """A tall revolved body: staves should run up the body, not around it."""
    from make_examples import rocket

    tris = scale_to_grid(rocket(), size=30)
    beams = surfaces.beams_from_contours(tris, pattern="flow")
    long = [b for b in beams if b.length >= 3]
    vertical = [b for b in long if abs(b.vector[2]) / b.length > 0.7]
    assert len(vertical) > 0.6 * len(long)


def test_roll_puts_a_face_on_the_surface():
    from satisfactory_printer.orient import roll_steps

    # horizontal beam along x lying on a 30°-tilted roof (normal rotated about x)
    ang = np.radians(30)
    n = np.array([0, -np.sin(ang), np.cos(ang)])
    tri = np.array([[[-50, 0, 0], [50, 0, 0], [0, 10 * np.cos(ang), 10 * np.sin(ang)]]])
    assert np.allclose(np.cross(tri[0, 1] - tri[0, 0], tri[0, 2] - tri[0, 0]) / np.linalg.norm(
        np.cross(tri[0, 1] - tri[0, 0], tri[0, 2] - tri[0, 0])), n)
    (steps,) = roll_steps([Beam((0, 2, 1), (10, 2, 1))], tri)
    assert abs(steps) == 2  # 30° = 2 notches of 15°
    (flat,) = roll_steps([Beam((0, 0, 0), (10, 0, 0))], np.array([[[-50, -50, 0], [50, -50, 0], [0, 50, 0]]]))
    assert flat == 0


def test_wireframe_cube_has_12_edges():
    topo = surfaces.build_topology(scale_to_grid(cube(), size=5))
    beams = surfaces.beams_from_mesh_edges(topo)
    assert len(beams) == 12 and all(b.length == 5 for b in beams)


# --------------------------------------------------------------------------- plan / cli


def test_plan_nudges_reproduce_beam():
    """Simulate the technique: shape nudge relative to anchor, then place nudge."""
    cfg = dict(DEFAULT_CONFIG, model_offset=[0, 0, 0])
    beam = Beam((2, 3, 1), (5, -1, 4))
    (step,), _, _ = build_plan([beam], cfg)
    inverse = {v: k for k, v in cfg["keys"].items()}
    pos, vectors = np.zeros(3, int), []
    for a in step.actions:
        if a.kind == "key" and a.key in inverse and inverse[a.key] != "lock":
            sign = 1 if inverse[a.key][0] == "+" else -1
            pos["xyz".index(inverse[a.key][1])] += sign * a.times
        elif a.kind == "click":
            vectors.append(pos.copy())
            pos[:] = 0
    shape, place = vectors
    # beam drawn from (anchor + shape) to anchor, then moved by place. The plan may draw it
    # from either end (the one nearer the anchor is placed last) - same beam either way.
    assert {tuple(shape + place), tuple(place)} == {beam.start, beam.end}
    assert sum(map(abs, place)) <= sum(map(abs, shape + place))


def test_cli_all_modes(tmp_path):
    stl = tmp_path / "h.stl"
    save_stl_ascii(stl, house())
    for mode in ("hybrid", "planes", "contour", "voxel", "wireframe"):
        out = tmp_path / mode
        assert main(["build", str(stl), "--size", "12", "--mode", mode, "-o", str(out)]) == 0
        plan = json.loads((out / "plan.json").read_text())
        assert plan["beams"] and all(b["length"] <= 40 for b in plan["beams"])
        html = (out / "preview.html").read_text()
        assert "let DATA = {" in html and "__DATA__" not in html


# --------------------------------------------------------------------------- live preview / runner


def test_live_session_rebuilds_and_runs(tmp_path):
    import time

    from satisfactory_printer.cli import _build_parser
    from satisfactory_printer.server import LiveSession

    stl = tmp_path / "h.stl"
    save_stl_ascii(stl, house())
    cfg = tmp_path / "fast.json"
    cfg.write_text(json.dumps({"click_delay": 0, "beam_delay": 0}))
    args = _build_parser().parse_args(["serve", str(stl), "--size", "10", "-o", str(tmp_path / "live"), "-c", str(cfg)])
    session = LiveSession(args)
    small = session.preview(session.params({"size": ["10"]}))
    big = session.preview(session.params({"size": ["30"]}))
    assert len(big["beams"]) > len(small["beams"]) and big["timing"]["presses"] > 0
    assert "surface coverage" in session.metrics(session.params({"size": ["10"]}))

    st = session.run_start(session.params({"size": ["10"], "kps": ["100"], "countdown": ["0"],
                                           "simulate": ["1"], "start_at": ["2"]}))
    assert st["status"] in ("ready", "countdown", "running") and st["total"] == len(small["beams"])
    assert session.run_start(session.params({}))["error"]  # only one run at a time
    deadline = time.time() + 30
    while session.run_status()["status"] not in ("done", "error") and time.time() < deadline:
        time.sleep(0.05)
    final = session.run_status()
    assert final["status"] == "done" and final["placed"] == final["total"]
    assert (tmp_path / "live" / "h_hybrid_10m" / "plan.json").exists()


def test_runner_pause_and_stop_between_beams():
    import time

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
