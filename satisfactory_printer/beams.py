"""Beam primitives and the generic helpers every generation mode shares.

Coordinates are model coordinates in metres; beam endpoints are integers because every
endpoint has to be reachable with whole 1 m nudges from the anchor. A beam's endpoints are
the centres of its end faces (centreline), so a voxel at integer position p is covered by a
beam whose centreline passes through p.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

Vec = tuple[int, int, int]


@dataclass(frozen=True)
class Beam:
    """A beam from `start` to `end` in integer grid coordinates (1 unit = 1 m = 1 nudge)."""

    start: Vec
    end: Vec
    kind: str = field(default="", compare=False)  # which generator made it (for previews)

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
            self.kind,
        )

    def canonical(self) -> "Beam":
        """Same beam with endpoints in a fixed order (for de-duplication)."""
        return self if self.start <= self.end else Beam(self.end, self.start, self.kind)


def _ivec(p) -> Vec:
    return (int(p[0]), int(p[1]), int(p[2]))


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
AXIS_DIRECTIONS: list[Vec] = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]


def lattice_walk(start: np.ndarray, end: np.ndarray) -> list[Vec]:
    """Trace a segment through the 1 m grid (3D DDA); each step is one of the 26 directions."""
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    n = max(int(math.ceil(float(np.abs(end - start).max()))), 1)
    pts: list[Vec] = []
    for k in range(n + 1):
        # floor(x + 0.5) instead of round(): monotone, so steps never jump by 2
        p = _ivec([math.floor(c + 0.5) for c in start + (end - start) * k / n])
        if not pts or p != pts[-1]:
            pts.append(p)
    return pts


def beams_from_segments_lattice(
    segments: list[tuple[np.ndarray, np.ndarray]], max_len: float = 40, kind: str = "edge"
) -> list[Beam]:
    """Restrict segments to the 26 cube directions and merge them into maximal straight beams."""
    canon = set(LATTICE_DIRECTIONS)
    edges: dict[Vec, set[Vec]] = {d: set() for d in LATTICE_DIRECTIONS}
    for a, b in segments:
        pts = lattice_walk(a, b)
        for p, q in zip(pts, pts[1:]):
            step = _ivec([qc - pc for pc, qc in zip(p, q)])
            if step in canon:
                edges[step].add(p)
            else:
                edges[_ivec([-c for c in step])].add(q)

    beams: list[Beam] = []
    for d, starts in edges.items():
        max_steps = max(int(max_len / math.sqrt(sum(c * c for c in d)) + 1e-9), 1)
        for p in starts:
            if _ivec([pc - dc for pc, dc in zip(p, d)]) in starts:
                continue  # not the beginning of a chain
            length, q = 0, p
            while q in starts:
                q = _ivec([qc + dc for qc, dc in zip(q, d)])
                length += 1
            for pos in range(0, length, max_steps):
                n = min(max_steps, length - pos)
                s = _ivec([pc + dc * pos for pc, dc in zip(p, d)])
                e = _ivec([pc + dc * (pos + n) for pc, dc in zip(p, d)])
                beams.append(Beam(s, e, kind))
    return beams


# --------------------------------------------------------------------------- polylines


def simplify_polyline(points: np.ndarray, tol: float, closed: bool = False) -> np.ndarray:
    """Douglas-Peucker. Keeps a subset of the input vertices (so integer input stays integer)."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3:
        return pts
    if closed:
        # split the loop at the vertex farthest from the first one
        far = int(np.argmax(np.linalg.norm(pts - pts[0], axis=1)))
        if far == 0:
            return pts[:1]
        a = simplify_polyline(pts[: far + 1], tol)
        b = simplify_polyline(np.vstack([pts[far:], pts[:1]]), tol)
        return np.vstack([a[:-1], b[:-1]])

    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = pts[j] - pts[i]
        seg_len = np.linalg.norm(seg)
        rel = pts[i + 1 : j] - pts[i]
        if seg_len < 1e-12:
            d = np.linalg.norm(rel, axis=1)
        else:
            d = np.linalg.norm(np.cross(rel, seg / seg_len), axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return pts[keep]


def polyline_to_beams(
    points: np.ndarray, tol: float, max_len: float, kind: str, closed: bool = False
) -> list[Beam]:
    """Snap a dense polyline to the grid, straighten it within `tol` and cut it into beams."""
    pts = np.floor(np.asarray(points, dtype=float) + 0.5).astype(int)
    if len(pts) == 0:
        return []
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.any(pts[1:] != pts[:-1], axis=1)
    pts = pts[keep]
    if closed and len(pts) > 1 and (pts[0] == pts[-1]).all():
        pts = pts[:-1]
    if len(pts) < 2:
        return []
    simple = simplify_polyline(pts, tol, closed=closed).astype(int)
    if closed and len(simple) > 2:
        simple = np.vstack([simple, simple[:1]])
    beams: list[Beam] = []
    for a, b in zip(simple, simple[1:]):
        beams.extend(split_segment(a, b, max_len, kind))
    return beams


def split_segment(start: np.ndarray, end: np.ndarray, max_len: float, kind: str = "") -> list[Beam]:
    """Snap a segment to the integer grid and split it into pieces no longer than max_len."""
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    length = float(np.linalg.norm(end - start))
    pieces = max(int(math.ceil(length / max_len)), 1) if max_len > 0 else 1
    # Rounding can push a piece slightly over max_len; add pieces until it fits.
    while True:
        pts = [_ivec(np.floor(start + (end - start) * k / pieces + 0.5)) for k in range(pieces + 1)]
        beams = [Beam(p, q, kind) for p, q in zip(pts, pts[1:]) if p != q]
        if max_len <= 0 or all(b.length <= max_len + 1e-9 for b in beams) or pieces > 1000:
            return beams
        pieces += 1


def beams_from_segments(
    segments: list[tuple[np.ndarray, np.ndarray]], max_len: float = 40, kind: str = "edge"
) -> list[Beam]:
    """Free directions: snap each segment's endpoints to the grid (any integer vector)."""
    seen: set[Beam] = set()
    out: list[Beam] = []
    for s, e in segments:
        for beam in split_segment(s, e, max_len, kind):
            key = beam.canonical()
            if key not in seen:
                seen.add(key)
                out.append(beam)
    return out


# --------------------------------------------------------------------------- voxels


def beams_from_voxels(
    grid: np.ndarray, max_len: float = 40, directions: str = "26", axis: str | None = None
) -> list[Beam]:
    """Cover every filled voxel with as few beams as possible (greedy set cover).

    Voxel (i, j, k) has its centre at integer position (i, j, k). Candidate beams are straight
    runs of filled voxels along the allowed directions (3 axes, or all 13 cube directions incl.
    diagonals). Beams may overlap - clipping is free in-game - so the greedy picks whichever
    beam covers the most still-uncovered voxels, preferring axis directions on ties.
    """
    if axis:
        dirs = [AXIS_DIRECTIONS["xyz".index(axis)]]
    else:
        dirs = AXIS_DIRECTIONS if directions == "6" else LATTICE_DIRECTIONS
    filled = {_ivec(p) for p in np.argwhere(grid)}
    if not filled:
        return []

    # candidate runs: maximal lines through filled voxels, cut into windows <= max length
    candidates: list[tuple[Vec, list[Vec]]] = []
    for d in dirs:
        step_len = math.sqrt(sum(c * c for c in d))
        max_steps = max(int(max_len / step_len + 1e-9), 1)
        for p in filled:
            if _ivec([a - b for a, b in zip(p, d)]) in filled:
                continue
            line = [p]
            while (q := _ivec([a + b for a, b in zip(line[-1], d)])) in filled:
                line.append(q)
            if len(line) == 1:
                continue
            # overlapping windows so the greedy can choose where long runs get cut
            stride = max(max_steps // 2, 1)
            s = 0
            while True:
                window = line[s : s + max_steps + 1]
                candidates.append((d, window))
                if s + max_steps >= len(line) - 1:
                    break
                s += stride

    covered: set[Vec] = set()
    beams: list[Beam] = []
    # lazy greedy: priority = (-new voxels, diagonal penalty, index)
    heap = [(-len(c[1]), 0 if c[0] in AXIS_DIRECTIONS else 1, i) for i, c in enumerate(candidates)]
    heapq.heapify(heap)
    while heap:
        neg, pen, i = heapq.heappop(heap)
        voxels = candidates[i][1]
        gain = sum(1 for v in voxels if v not in covered)
        if gain == 0:
            continue
        if gain < -neg:
            heapq.heappush(heap, (-gain, pen, i))
            continue
        # trim already-covered voxels off both ends (overlap only where it saves a beam)
        lo = next(k for k, v in enumerate(voxels) if v not in covered)
        hi = len(voxels) - 1 - next(k for k, v in enumerate(reversed(voxels)) if v not in covered)
        if hi == lo:  # single new voxel: 1 m stub towards a neighbour
            lo, hi = (lo, lo + 1) if lo + 1 < len(voxels) else (lo - 1, lo)
        voxels = voxels[lo : hi + 1]
        beams.append(Beam(voxels[0], voxels[-1], "voxel"))
        covered.update(voxels)

    # isolated voxels with no filled neighbour in any direction: 1 m vertical stub
    for p in filled - covered:
        beams.append(Beam(p, (p[0], p[1], p[2] + 1), "voxel"))
    return beams


# --------------------------------------------------------------------------- clean-up


def merge_collinear(beams: list[Beam], max_len: float = 40) -> list[Beam]:
    """Remove duplicates and fuse beams that lie on the same line and overlap or touch."""
    lines: dict[tuple, list[tuple[float, float, Beam]]] = {}
    for b in beams:
        v = np.array(b.vector, dtype=int)
        if not v.any():
            continue
        g = math.gcd(math.gcd(abs(int(v[0])), abs(int(v[1]))), abs(int(v[2])))
        d = v // g
        if tuple(d) < (0, 0, 0):
            d = -d
        p = np.array(b.start)
        # a line is identified exactly by its direction and p x d (same for all points on it)
        key = (_ivec(d), _ivec(np.cross(p, d)))
        dd = float(d @ d)
        t0 = float(p @ d) / dd
        t1 = float(np.array(b.end) @ d) / dd
        lines.setdefault(key, []).append((min(t0, t1), max(t0, t1), b))

    out: list[Beam] = []
    for (d, _), items in lines.items():
        d_arr = np.array(d)
        unit = math.sqrt(float(d_arr @ d_arr))
        items.sort(key=lambda it: it[0])
        cur_lo, cur_hi, first = items[0]
        group = [first]
        for lo, hi, b in items[1:]:
            if lo <= cur_hi + 1e-9 and (max(hi, cur_hi) - cur_lo) * unit <= max_len + 1e-9:
                cur_hi = max(cur_hi, hi)
                group.append(b)
            elif hi <= cur_hi + 1e-9:
                group.append(b)  # fully contained in the current span: drop it
            else:
                out.append(_span(group, cur_lo, cur_hi, d_arr))
                cur_lo, cur_hi, group = lo, hi, [b]
        out.append(_span(group, cur_lo, cur_hi, d_arr))
    return out


def _span(group: list[Beam], lo: float, hi: float, d: np.ndarray) -> Beam:
    ends = [p for b in group for p in (b.start, b.end)]
    dd = float(d @ d)
    start = min(ends, key=lambda p: abs(float(np.array(p) @ d) / dd - lo))
    end = min(ends, key=lambda p: abs(float(np.array(p) @ d) / dd - hi))
    kind = max(group, key=lambda b: b.length).kind
    return Beam(start, end, kind)


def sort_for_building(beams: list[Beam]) -> list[Beam]:
    """Build bottom-up, then back-to-front, then left-to-right."""
    return sorted(
        beams,
        key=lambda b: (min(b.start[2], b.end[2]), min(b.start[1], b.end[1]), min(b.start[0], b.end[0])),
    )
