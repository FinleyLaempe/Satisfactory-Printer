"""Beam generation: voxel grid / mesh edges / line segments -> list of integer beams."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Vec = tuple[int, int, int]


@dataclass(frozen=True)
class Beam:
    """A beam from `start` to `end` in integer grid coordinates (1 unit = 1 m = 1 nudge)."""

    start: Vec
    end: Vec

    @property
    def vector(self) -> Vec:
        return tuple(e - s for s, e in zip(self.start, self.end))  # type: ignore[return-value]

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)

    def translated(self, offset: Vec) -> "Beam":
        return Beam(
            tuple(a + o for a, o in zip(self.start, offset)),  # type: ignore[arg-type]
            tuple(a + o for a, o in zip(self.end, offset)),  # type: ignore[arg-type]
        )

    def canonical(self) -> "Beam":
        """Same beam with endpoints in a fixed order (for de-duplication)."""
        return self if self.start <= self.end else Beam(self.end, self.start)


# --------------------------------------------------------------------------- voxels


def _runs_along_axis(grid: np.ndarray, axis: int, max_len: int) -> list[Beam]:
    moved = np.moveaxis(grid, axis, -1)  # [a, b, run]
    beams: list[Beam] = []
    for a in range(moved.shape[0]):
        for b in range(moved.shape[1]):
            line = moved[a, b]
            if not line.any():
                continue
            # find run boundaries
            padded = np.concatenate(([False], line, [False])).astype(np.int8)
            diff = np.diff(padded)
            starts = np.flatnonzero(diff == 1)
            ends = np.flatnonzero(diff == -1)
            for s, e in zip(starts, ends):
                pos = int(s)
                while pos < e:
                    length = min(max_len, int(e) - pos)
                    beams.append(_axis_beam(axis, a, b, pos, length))
                    pos += length
    return beams


def _axis_beam(axis: int, a: int, b: int, pos: int, length: int) -> Beam:
    other = [i for i in range(3) if i != axis]
    start = [0, 0, 0]
    start[other[0]], start[other[1]], start[axis] = a, b, pos
    end = list(start)
    end[axis] += length
    return Beam(tuple(start), tuple(end))  # type: ignore[arg-type]


def beams_from_voxels(grid: np.ndarray, max_len: int = 40, axis: str = "auto") -> list[Beam]:
    """Merge filled voxels into straight 1x1xL beams.

    axis: "x", "y", "z" or "auto" (picks the direction that needs the fewest beams).
    """
    if axis != "auto":
        return _runs_along_axis(grid, "xyz".index(axis), max_len)
    candidates = [_runs_along_axis(grid, i, max_len) for i in range(3)]
    return min(candidates, key=len)


# --------------------------------------------------------------------------- segments


def split_segment(start: np.ndarray, end: np.ndarray, max_len: float) -> list[Beam]:
    """Snap a segment to the integer grid and split it into pieces no longer than max_len."""
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    length = float(np.linalg.norm(end - start))
    pieces = max(int(math.ceil(length / max_len)), 1) if max_len > 0 else 1
    # Rounding can push a piece slightly over max_len; add pieces until it fits.
    while True:
        pts = [tuple(int(round(c)) for c in start + (end - start) * k / pieces) for k in range(pieces + 1)]
        beams = [Beam(p, q) for p, q in zip(pts, pts[1:]) if p != q]
        if max_len <= 0 or all(b.length <= max_len + 1e-9 for b in beams) or pieces > 1000:
            return beams
        pieces += 1


def beams_from_segments(segments: list[tuple[np.ndarray, np.ndarray]], max_len: float = 40) -> list[Beam]:
    seen: set[Beam] = set()
    out: list[Beam] = []
    for s, e in segments:
        for beam in split_segment(s, e, max_len):
            key = beam.canonical()
            if key not in seen:
                seen.add(key)
                out.append(beam)
    return out


def beams_from_mesh_edges(
    tris: np.ndarray, max_len: float = 40, feature_angle: float = 1.0, lattice: bool = False
) -> list[Beam]:
    """Wireframe mode: one beam per mesh edge.

    Edges between nearly coplanar faces (dihedral angle below `feature_angle` degrees) are
    skipped, so flat quads made of two triangles don't get a diagonal beam.
    """
    edge_faces: dict[tuple[tuple[float, ...], tuple[float, ...]], list[np.ndarray]] = {}
    for tri in tris:
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        norm = np.linalg.norm(n)
        if norm < 1e-12:
            continue
        n = n / norm
        for i in range(3):
            p = tuple(np.round(tri[i], 6))
            q = tuple(np.round(tri[(i + 1) % 3], 6))
            key = (p, q) if p <= q else (q, p)
            edge_faces.setdefault(key, []).append(n)

    cos_limit = math.cos(math.radians(feature_angle))
    segments = []
    for (p, q), normals in edge_faces.items():
        if len(normals) == 2 and abs(float(np.dot(normals[0], normals[1]))) >= cos_limit:
            continue
        segments.append((np.array(p), np.array(q)))
    if lattice:
        return beams_from_segments_lattice(segments, max_len)
    return beams_from_segments(segments, max_len)


# --------------------------------------------------------------------------- 1 m lattice

# The 13 canonical directions of a 1x1x1 cube (edges, face diagonals, space diagonals);
# together with their negatives they are the 26 neighbour directions.
LATTICE_DIRECTIONS: list[Vec] = [
    (dx, dy, dz)
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    for dz in (-1, 0, 1)
    if (dx, dy, dz) > (0, 0, 0)
]


def _canonical_step(step: Vec) -> tuple[Vec, bool]:
    """Return (canonical direction, flipped?) for a unit lattice step."""
    if step in _CANON:
        return step, False
    return tuple(-c for c in step), True  # type: ignore[return-value]


_CANON = set(LATTICE_DIRECTIONS)


def lattice_walk(start: np.ndarray, end: np.ndarray) -> list[Vec]:
    """Trace a segment through the 1 m grid (3D DDA).

    Consecutive points differ by at most 1 on every axis, i.e. each step is one of the 26
    directions of a 1x1x1 cube.
    """
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    n = max(int(math.ceil(float(np.abs(end - start).max()))), 1)
    pts: list[Vec] = []
    for k in range(n + 1):
        # floor(x + 0.5) instead of round(): monotone, so steps never jump by 2
        p = tuple(int(math.floor(c + 0.5)) for c in start + (end - start) * k / n)
        if not pts or p != pts[-1]:
            pts.append(p)  # type: ignore[arg-type]
    return pts


def beams_from_segments_lattice(
    segments: list[tuple[np.ndarray, np.ndarray]], max_len: float = 40
) -> list[Beam]:
    """Restrict segments to the 1 m lattice and merge them into maximal straight beams.

    Every beam starts and ends on a grid point and points along a cube edge, face diagonal
    or space diagonal. Overlapping paths are de-duplicated at the 1-step level.
    """
    edges: dict[Vec, set[Vec]] = {d: set() for d in LATTICE_DIRECTIONS}
    for a, b in segments:
        pts = lattice_walk(a, b)
        for p, q in zip(pts, pts[1:]):
            step = tuple(qc - pc for pc, qc in zip(p, q))
            d, flipped = _canonical_step(step)  # type: ignore[arg-type]
            edges[d].add(q if flipped else p)

    beams: list[Beam] = []
    for d, starts in edges.items():
        step_len = math.sqrt(sum(c * c for c in d))
        max_steps = max(int(max_len / step_len + 1e-9), 1) if max_len > 0 else 10**9
        for p in starts:
            prev = tuple(pc - dc for pc, dc in zip(p, d))
            if prev in starts:
                continue  # not the beginning of a chain
            length = 0
            q = p
            while q in starts:
                q = tuple(qc + dc for qc, dc in zip(q, d))
                length += 1
            pos = 0
            while pos < length:
                n = min(max_steps, length - pos)
                s = tuple(pc + dc * pos for pc, dc in zip(p, d))
                e = tuple(pc + dc * (pos + n) for pc, dc in zip(p, d))
                beams.append(Beam(s, e))  # type: ignore[arg-type]
                pos += n
    return beams


# --------------------------------------------------------------------------- ordering


def sort_for_building(beams: list[Beam]) -> list[Beam]:
    """Build bottom-up, then back-to-front, then left-to-right."""
    return sorted(
        beams,
        key=lambda b: (min(b.start[2], b.end[2]), min(b.start[1], b.end[1]), min(b.start[0], b.end[0])),
    )
