"""Beams and the helpers to make them: straighten polylines, split long beams, merge.

Coordinates are model coordinates in metres. A beam runs along its centre line from
`start` to `end`. For the keyboard plan every endpoint is snapped to the 1 m nudge grid
(whole nudges from the anchor); for blueprints (set_precise) endpoints stay exact.
"""

from __future__ import annotations

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


# Precise mode (blueprint export): endpoints are NOT snapped to the 1 m nudge grid. A blueprint
# stores exact positions, so beams can sit exactly on the surface.
PRECISE = False


def set_precise(on: bool) -> None:
    global PRECISE
    PRECISE = bool(on)


def _snap(p) -> tuple:
    """Grid point for keystroke plans; exact point (rounded to 1 mm) in precise mode."""
    if PRECISE:
        return tuple(round(float(c), 3) for c in p)
    return tuple(int(math.floor(float(c) + 0.5)) for c in p)


def _ivec(p) -> Vec:
    return (int(p[0]), int(p[1]), int(p[2]))


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
    pts = np.asarray(points, dtype=float)
    pts = np.round(pts, 3) if PRECISE else np.floor(pts + 0.5).astype(int)
    if len(pts) == 0:
        return []
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.any(pts[1:] != pts[:-1], axis=1)
    pts = pts[keep]
    if closed and len(pts) > 1 and (pts[0] == pts[-1]).all():
        pts = pts[:-1]
    if len(pts) < 2:
        return []
    simple = simplify_polyline(pts, tol, closed=closed)
    if not PRECISE:
        simple = simple.astype(int)
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
        pts = [_snap(start + (end - start) * k / pieces) for k in range(pieces + 1)]
        beams = [Beam(p, q, kind) for p, q in zip(pts, pts[1:]) if p != q]
        if max_len <= 0 or all(b.length <= max_len + 1e-9 for b in beams) or pieces > 1000:
            return beams
        pieces += 1


# --------------------------------------------------------------------------- clean-up


def merge_collinear(beams: list[Beam], max_len: float = 40) -> list[Beam]:
    """Remove duplicates and fuse beams that lie on the same line and overlap or touch."""
    if any(isinstance(c, float) and not float(c).is_integer() for b in beams[:200] for c in b.start + b.end):
        # precise (non-grid) beams: only drop exact duplicates
        seen, out = set(), []
        for b in beams:
            k = b.canonical()
            if k not in seen and b.length > 1e-6:
                seen.add(k)
                out.append(b)
        return out
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
