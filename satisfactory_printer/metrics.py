"""How realistic is a beam set? Compares the beams against the original surface.

* coverage  - share of the model surface that lies within a beam's reach (<= 0.75 m from a
              beam centreline, i.e. touched by the 1x1 m beam body)
* deviation - mean / 95th percentile distance from the surface to the nearest beam
* stray     - share of beam length that sits more than 1 m away from the surface
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .beams import Beam

CAP = 3.0  # distances are capped (search radius) - anything further counts as CAP


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
    """Distance from every query point to the closest target point (capped at `cell`)."""
    out = np.full(len(query), cell)
    if len(targets) == 0 or len(query) == 0:
        return out
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, k in enumerate(map(tuple, np.floor(targets / cell).astype(int))):
        buckets[k].append(i)
    qcells = np.floor(query / cell).astype(int)
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, k in enumerate(map(tuple, qcells)):
        groups[k].append(i)
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for k, qi in groups.items():
        cand = [j for o in offsets for j in buckets.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]), ())]
        if not cand:
            continue
        q = query[qi]
        t = targets[cand]
        d2 = ((q[:, None, :] - t[None, :, :]) ** 2).sum(axis=2)
        out[qi] = np.minimum(np.sqrt(d2.min(axis=1)), cell)
    return out


def evaluate(beams: list[Beam], tris: np.ndarray) -> dict[str, float]:
    surface = sample_surface(tris)
    beam_pts = sample_beams(beams)
    d_surface = nearest_distance(surface, beam_pts)
    d_beam = nearest_distance(beam_pts, surface)
    return {
        "coverage_pct": round(100 * float((d_surface <= 0.75).mean()), 1) if len(surface) else 0.0,
        "mean_dev_m": round(float(d_surface.mean()), 2) if len(surface) else 0.0,
        "p95_dev_m": round(float(np.percentile(d_surface, 95)), 2) if len(surface) else 0.0,
        "stray_pct": round(100 * float((d_beam > 1.0).mean()), 1) if len(beam_pts) else 0.0,
    }
