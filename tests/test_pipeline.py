import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from make_examples import cube  # noqa: E402
from satisfactory_printer.beams import Beam, beams_from_mesh_edges, beams_from_voxels, split_segment  # noqa: E402
from satisfactory_printer.cli import main  # noqa: E402
from satisfactory_printer.plan import DEFAULT_CONFIG, build_plan  # noqa: E402
from satisfactory_printer.voxelize import hollow, scale_to_grid, voxelize_solid  # noqa: E402


def test_cube_voxelizes_solid():
    grid = voxelize_solid(scale_to_grid(cube(), size=4))
    assert grid.shape == (4, 4, 4) and grid.all()


def test_hollow_removes_interior():
    grid = np.ones((5, 5, 5), dtype=bool)
    assert hollow(grid).sum() == 5**3 - 3**3


def test_voxel_beams_cover_every_voxel_exactly_once():
    rng = np.random.default_rng(0)
    grid = rng.random((7, 6, 5)) > 0.4
    beams = beams_from_voxels(grid, max_len=3)
    covered = np.zeros_like(grid, dtype=int)
    for b in beams:
        assert 1 <= b.length <= 3
        lo = np.minimum(b.start, b.end)
        hi = np.maximum(b.start, b.end)
        hi = np.where(hi == lo, hi + 1, hi)
        covered[lo[0] : hi[0], lo[1] : hi[1], lo[2] : hi[2]] += 1
    assert (covered == grid.astype(int)).all()


def test_wireframe_cube_has_12_edges():
    beams = beams_from_mesh_edges(scale_to_grid(cube(), size=5))
    assert len(beams) == 12 and all(b.length == 5 for b in beams)


def test_split_respects_max_length():
    pieces = split_segment(np.array([0, 0, 0]), np.array([30, 7, 3]), 12)
    assert all(p.length <= 12 for p in pieces)
    assert pieces[0].start == (0, 0, 0) and pieces[-1].end == (30, 7, 3)


def test_plan_nudges_reproduce_beam():
    """Simulate the technique: shape nudge relative to anchor, then place nudge."""
    cfg = dict(DEFAULT_CONFIG, model_offset=[0, 0, 0])
    beam = Beam((2, 3, 1), (5, -1, 4))
    (step,), _, _ = build_plan([beam], cfg)
    inverse = {v: k for k, v in cfg["keys"].items()}
    pos, phase, vectors = np.zeros(3, int), 0, []
    for a in step.actions:
        if a.kind == "key" and a.key in inverse and inverse[a.key] != "lock":
            sign = 1 if inverse[a.key][0] == "+" else -1
            pos["xyz".index(inverse[a.key][1])] += sign * a.times
        elif a.kind == "click":
            vectors.append(pos.copy())
            pos[:] = 0
    shape, place = vectors
    # beam drawn from (anchor + shape) to anchor, then moved by place
    assert tuple(shape + place) == beam.start and tuple(place) == beam.end


def test_cli_build(tmp_path):
    from make_examples import save_stl_ascii

    stl = tmp_path / "c.stl"
    save_stl_ascii(stl, cube())
    assert main(["build", str(stl), "--size", "3", "-o", str(tmp_path / "out")]) == 0
    plan = json.loads((tmp_path / "out" / "plan.json").read_text())
    assert plan["beams"]


def test_lattice_walk_is_26_connected():
    from satisfactory_printer.beams import lattice_walk

    pts = lattice_walk(np.array([0.5, 0.5, 0.5]), np.array([17.3, -6.5, 9.5]))
    for p, q in zip(pts, pts[1:]):
        assert max(abs(a - b) for a, b in zip(p, q)) == 1


def test_lattice_beams_only_use_cube_directions():
    from satisfactory_printer.beams import beams_from_segments_lattice

    segs = [
        (np.array([0, 0, 0]), np.array([20, 0, 0])),  # straight, longer than max_len
        (np.array([0, 0, 0]), np.array([5, 5, 5])),  # space diagonal
        (np.array([0, 0, 0]), np.array([10, 0, 0])),  # overlaps the first one
        (np.array([0, 0, 0]), np.array([7, 3, 1])),  # arbitrary -> staircase of allowed steps
    ]
    beams = beams_from_segments_lattice(segs, max_len=12)
    for b in beams:
        v = np.array(b.vector)
        steps = np.abs(v).max()
        assert set(np.unique(np.abs(v))) <= {0, steps}  # direction components are 0 or ±1 * steps
        assert b.length <= 12 + 1e-9
    assert Beam((0, 0, 0), (5, 5, 5)) in beams
    straight = [b for b in beams if b.vector[1] == 0 and b.vector[2] == 0 and b.start[1:] == (0, 0)]
    assert sum(abs(b.vector[0]) for b in straight) >= 20  # x axis covered once, no duplicates beyond overlap

