"""How realistic is a beam set? Compares the beams against the original surface.

* coverage  - share of the model surface that is actually hidden under a beam (<= 0.6 m
              from a beam centreline: inside the 1x1 m beam body). The rest are visible holes.
* deviation - mean / 95th percentile distance from the surface to the nearest beam
              (capped at 1.5 m)
* stray     - share of beam length that sits more than 1 m away from the surface
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .beams import Beam

CAP = 3.0  # distances are capped (search radius) - anything further counts as CAP
COVERED = 0.6  # a surface point closer than this to a beam centreline is under the beam


def sample_surface(tris: np.ndarray, density: float = 8.0, max_points: int = 150_000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    total = float(areas.sum())
    if total <= 0:
        return np.zeros((0, 3))
    n = int(min(max(total * density, 1000), max_points))
    idx = rng.choice(len(tris), size=n, p=areas / total)
    r1, r2 = rng.random(n), rng.random(n)
    flip = r1 + r2 > 1
    r1[flip], r2[flip] = 1 - r1[flip], 1 - r2[flip]
    return a[idx] + r1[:, None] * (b[idx] - a[idx]) + r2[:, None] * (c[idx] - a[idx])


def sample_beams(beams: list[Beam], step: float = 0.25) -> np.ndarray:
    pts = []
    for bm in beams:
        s, e = np.array(bm.start, float), np.array(bm.end, float)
        n = max(int(np.ceil(bm.length / step)), 1)
        t = np.linspace(0, 1, n + 1)[:, None]
        pts.append(s + t * (e - s))
    return np.vstack(pts) if pts else np.zeros((0, 3))


def nearest_distance(query: np.ndarray, targets: np.ndarray, cell: float = CAP) -> np.ndarray:
    """Distance from every query point to the closest target point (capped at `cell`).

    Spatial hash with buckets of size `cell`: only the 27 neighbouring buckets are searched,
    so a small cell (e.g. when only "closer than 0.6 m?" matters) is much faster.
    """
    out = np.full(len(query), cell)
    if len(targets) == 0 or len(query) == 0:
        return out
    tk = np.floor(targets / cell).astype(np.int64)
    order = np.lexsort(tk.T[::-1])
    tk_sorted = tk[order]
    t_sorted = targets[order]
    uniq, start, counts = np.unique(tk_sorted, axis=0, return_index=True, return_counts=True)
    bucket = {tuple(k): (s, s + c) for k, s, c in zip(uniq.tolist(), start.tolist(), counts.tolist())}

    qk = np.floor(query / cell).astype(np.int64)
    q_uniq, q_inv = np.unique(qk, axis=0, return_inverse=True)
    q_inv = q_inv.reshape(-1)
    q_order = np.argsort(q_inv, kind="stable")
    q_bounds = np.searchsorted(q_inv[q_order], np.arange(len(q_uniq) + 1))
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for g, k in enumerate(q_uniq.tolist()):
        spans = [bucket[c] for o in offsets if (c := (k[0] + o[0], k[1] + o[1], k[2] + o[2])) in bucket]
        if not spans:
            continue
        t = np.concatenate([t_sorted[a:b] for a, b in spans])
        qi = q_order[q_bounds[g]:q_bounds[g + 1]]
        q = query[qi]
        d2 = ((q[:, None, :] - t[None, :, :]) ** 2).sum(axis=2)
        out[qi] = np.minimum(np.sqrt(d2.min(axis=1)), cell)
    return out


def evaluate(beams: list[Beam], tris: np.ndarray) -> dict[str, float]:
    surface = sample_surface(tris)
    beam_pts = sample_beams(beams)
    d_surface = nearest_distance(surface, beam_pts, cell=1.5)
    d_beam = nearest_distance(beam_pts, surface, cell=1.5)
    return {
        "coverage_pct": round(100 * float((d_surface <= COVERED).mean()), 1) if len(surface) else 0.0,
        "mean_dev_m": round(float(d_surface.mean()), 2) if len(surface) else 0.0,
        "p95_dev_m": round(float(np.percentile(d_surface, 95)), 2) if len(surface) else 0.0,
        "stray_pct": round(100 * float((d_beam > 1.0).mean()), 1) if len(beam_pts) else 0.0,
    }


def _point_segment_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distances of points p (P,3) to segments a->b (S,3) -> (S, P)."""
    ab = b - a
    ap = p[None, :, :] - a[:, None, :]
    denom = np.maximum((ab * ab).sum(axis=1), 1e-12)[:, None]
    t = np.clip((ap * ab[:, None, :]).sum(axis=2) / denom, 0, 1)
    closest = a[:, None, :] + t[..., None] * ab[:, None, :]
    return np.linalg.norm(p[None, :, :] - closest, axis=2)


_CORNERS = np.array([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)])


def _patch_beam(pts: np.ndarray, max_len: float, normal: np.ndarray | None = None) -> list[Beam]:
    """One straight beam (integer endpoints) over a small elongated cluster of hole points.

    Of all grid-corner choices that hide the whole cluster, the one lying flattest on the
    surface (least sticking out along `normal`) wins, so patches don't look like spikes.
    """
    c = pts.mean(axis=0)
    if len(pts) >= 2:
        _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
        axis = vt[0]
    else:
        axis = np.array([1.0, 0.0, 0.0])
    t = (pts - c) @ axis
    lo, hi = float(t.min()), float(t.max())
    if hi - lo < 1.0:  # beams are at least 1 m long
        mid = (lo + hi) / 2
        lo, hi = mid - 0.5, mid + 0.5
    a, b = c + lo * axis, c + hi * axis
    # try every grid corner around both ends, keep the beam that hides the cluster best
    ca = np.floor(a) + _CORNERS
    cb = np.floor(b) + _CORNERS
    A = np.repeat(ca, 8, axis=0)
    B = np.tile(cb, (8, 1))
    ok = np.any(A != B, axis=1)
    A, B = A[ok], B[ok]
    worst = _point_segment_dist(pts, A, B).max(axis=1)
    length_err = np.abs(np.linalg.norm(B - A, axis=1) - (hi - lo))
    if normal is not None:
        # how far the beam leaves the surface plane through the cluster (at ends and middle)
        off = np.stack([(A - c) @ normal, (B - c) @ normal, ((A + B) / 2 - c) @ normal], axis=1)
        protrude = np.abs(off).max(axis=1)
        covers = worst <= COVERED
        if covers.any():
            k = int(np.lexsort((length_err, np.round(protrude, 2), ~covers))[0])
        else:
            k = int(np.lexsort((protrude, np.round(worst, 2)))[0])
    else:
        k = int(np.lexsort((length_err, np.round(worst, 2)))[0])
    from .beams import split_segment

    return split_segment(A[k], B[k], max_len, "fill")


def _clusters(points: np.ndarray, cell: float = 0.5) -> list[np.ndarray]:
    """Connected groups of points (26-neighbourhood on a `cell` grid)."""
    keys = np.floor(points / cell).astype(np.int64)
    members: dict[tuple, list[int]] = {}
    for i, k in enumerate(map(tuple, keys.tolist())):
        members.setdefault(k, []).append(i)
    seen: set[tuple] = set()
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    out = []
    for start in members:
        if start in seen:
            continue
        seen.add(start)
        stack, idx = [start], []
        while stack:
            k = stack.pop()
            idx += members[k]
            for o in offs:
                n = (k[0] + o[0], k[1] + o[1], k[2] + o[2])
                if n in members and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(points[idx])
    return out


def _split_cluster(pts: np.ndarray, width: float = 0.9) -> list[np.ndarray]:
    """Cut wide clusters into bands thin enough for one beam each (along their long axis)."""
    if len(pts) < 3:
        return [pts]
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    across = (pts - c) @ vt[1]
    lo, hi = across.min(), across.max()
    if hi - lo <= width:
        return [pts]
    n = int(np.ceil((hi - lo) / width))
    band = np.minimum(((across - lo) / (hi - lo) * n).astype(int), n - 1)
    return [pts[band == k] for k in range(n) if (band == k).any()]


def fill_gaps(beams: list[Beam], tris: np.ndarray, max_len: float = 40, rounds: int = 4) -> list[Beam]:
    """Find every visible hole in the beam skin and patch it.

    Holes are surface points not hidden under any beam. Neighbouring hole points are grouped
    into cracks; each crack gets one straight beam along its length, with its integer
    endpoints chosen so the beam actually passes over the whole crack.
    """
    tris = np.asarray(tris, dtype=float)
    cent = tris.mean(axis=1)
    nrm = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    face_cells: dict[tuple, list[int]] = {}
    for i, k in enumerate(map(tuple, np.floor(cent / 3.0).astype(int).tolist())):
        face_cells.setdefault(k, []).append(i)

    def normal_at(p: np.ndarray) -> np.ndarray | None:
        k = np.floor(p / 3.0).astype(int)
        cand = [i for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                for i in face_cells.get((k[0] + dx, k[1] + dy, k[2] + dz), ())]
        if not cand:
            return None
        j = cand[int(np.argmin(np.linalg.norm(cent[cand] - p, axis=1)))]
        return nrm[j]

    out = list(beams)
    for r in range(rounds):
        # fresh sample points every round, so small holes can't hide between samples
        surface = sample_surface(tris, density=16, max_points=2_000_000, seed=100 + r)
        d = nearest_distance(surface, sample_beams(out), cell=COVERED + 0.01)
        holes = surface[d > COVERED]
        if not len(holes):
            break
        for cluster in _clusters(holes):
            for part in _split_cluster(cluster):
                # long curved cracks: chop into <= 6 m pieces so a straight beam can follow
                c = part.mean(axis=0)
                if len(part) >= 2:
                    _, _, vt = np.linalg.svd(part - c, full_matrices=False)
                    t = (part - c) @ vt[0]
                    pieces = max(int(np.ceil((t.max() - t.min()) / 6.0)), 1)
                    edges = np.linspace(t.min(), t.max() + 1e-9, pieces + 1)
                    groups = [part[(t >= edges[i]) & (t < edges[i + 1])] for i in range(pieces)]
                else:
                    groups = [part]
                for g in groups:
                    if len(g):
                        out += _patch_beam(g, max_len, normal_at(g.mean(axis=0)))
    return out
