"""Surface-following beam generators.

* planes   - find flat regions (any orientation, incl. sloped/vertical diagonal planes) and
             fill them with parallel long beams lying *in* the plane, plus the region outline.
* contour  - slice the surface with parallel planes at every metre (rings / arcs), optionally
             plus radial half-planes (ribs).
* features - one beam chain along every sharp edge / open border of the mesh.
* hybrid   - planes for flat regions, contours for the curved rest, features for the edges.

All generators work on the mesh in model coordinates (metres, min corner at the origin) and
return beams with integer endpoints.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import beams as beams_mod
from . import parallel
from .beams import Beam, beams_from_segments_lattice, polyline_to_beams, split_segment


# --------------------------------------------------------------------------- topology


@dataclass
class Topology:
    tris: np.ndarray  # (N, 3, 3)
    normals: np.ndarray  # (N, 3) unit normals (zero for degenerate faces)
    areas: np.ndarray  # (N,)
    vidx: np.ndarray  # (N, 3) welded vertex indices
    verts: np.ndarray  # (V, 3)
    edge_faces: dict[tuple[int, int], list[int]]


def build_topology(tris: np.ndarray) -> Topology:
    flat = tris.reshape(-1, 3)
    keys = np.round(flat, 5)
    verts, inverse = np.unique(keys, axis=0, return_inverse=True)
    vidx = inverse.reshape(-1, 3)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    norm = np.linalg.norm(n, axis=1)
    areas = norm / 2
    normals = np.divide(n, norm[:, None], out=np.zeros_like(n), where=norm[:, None] > 1e-12)
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for f, (a, b, c) in enumerate(vidx):
        for u, v in ((a, b), (b, c), (c, a)):
            if u != v:
                edge_faces.setdefault((min(u, v), max(u, v)), []).append(f)
    return Topology(tris, normals, areas, vidx, verts, edge_faces)


def sharp_edge_set(topo: Topology, angle: float = 30.0) -> set[tuple[int, int]]:
    """Mesh edges where faces meet at more than `angle` degrees, plus open borders."""
    cos_a = math.cos(math.radians(angle))
    sharp = set()
    for key, faces in topo.edge_faces.items():
        if len(faces) == 1:
            sharp.add(key)
        else:
            # >2 faces = messy (non-manifold) mesh: sharp if any two of them disagree
            ns = topo.normals[faces]
            if float((ns @ ns.T).min()) < cos_a:
                sharp.add(key)
    return sharp


def _face_neighbours(topo: Topology) -> list[list[int]]:
    nb: list[list[int]] = [[] for _ in range(len(topo.tris))]
    for faces in topo.edge_faces.values():
        for f in faces:
            nb[f].extend(g for g in faces if g != f)
    return nb


# --------------------------------------------------------------------------- planar regions


@dataclass
class Region:
    faces: np.ndarray
    normal: np.ndarray
    offset: float  # plane: normal . x = offset
    area: float


def find_planar_regions(
    topo: Topology, angle_tol: float = 8.0, dist_tol: float = 0.35, min_area: float = 6.0
) -> tuple[list[Region], np.ndarray]:
    """Region-grow connected faces that lie (nearly) in one plane.

    Returns the regions with area >= min_area and a bool mask of faces that are NOT in any
    region (the curved rest).
    """
    cos_tol = math.cos(math.radians(angle_tol))
    nb = _face_neighbours(topo)
    assigned = np.full(len(topo.tris), -1)
    order = np.argsort(-topo.areas)
    regions: list[Region] = []
    for seed in order:
        if assigned[seed] != -1 or topo.areas[seed] < 1e-9:
            continue
        rid = len(regions)
        normal = topo.normals[seed].copy()
        offset = float(normal @ topo.tris[seed, 0])
        faces = [seed]
        assigned[seed] = rid
        stack = [seed]
        area_n = normal * topo.areas[seed]
        while stack:
            f = stack.pop()
            for g in nb[f]:
                if assigned[g] != -1 or topo.areas[g] < 1e-9:
                    continue
                if float(topo.normals[g] @ normal) < cos_tol:
                    continue
                if np.abs(topo.tris[g] @ normal - offset).max() > dist_tol:
                    continue
                assigned[g] = rid
                faces.append(g)
                stack.append(g)
                area_n += topo.normals[g] * topo.areas[g]
        faces_arr = np.array(faces)
        regions.append(Region(faces_arr, normal, offset, float(topo.areas[faces_arr].sum())))

    keep = [r for r in regions if r.area >= min_area]
    curved = np.ones(len(topo.tris), dtype=bool)
    for r in keep:
        curved[r.faces] = False
    curved &= topo.areas > 1e-9
    return keep, curved


def _fewest_lines_direction(normal: np.ndarray, pts: np.ndarray, candidates: list[np.ndarray]) -> np.ndarray:
    """In-plane direction whose perpendicular width is smallest -> fewest, longest beams.

    Earlier candidates win ties (within 0.5 m), so level/slope beat arbitrary edge directions.
    """
    best, best_w = candidates[0], math.inf
    for d in candidates:
        d = d - (d @ normal) * normal
        n = np.linalg.norm(d)
        if n < 1e-9:
            continue
        d = d / n
        e = np.cross(normal, d)
        t = pts @ e
        w = float(t.max() - t.min())
        if w < best_w - 0.5:
            best, best_w = d, w
    return best


def _plane_frame(
    normal: np.ndarray, pts: np.ndarray, direction: str, edge_dirs: tuple = ()
) -> tuple[np.ndarray, np.ndarray]:
    """Pick the hatch direction d (beams run along d) and the spacing direction e, both in-plane."""
    up = np.array([0.0, 0.0, 1.0])
    level = np.cross(normal, up)  # horizontal line inside the plane
    if np.linalg.norm(level) < 1e-6:  # horizontal plane
        level = np.array([1.0, 0.0, 0.0])
    level /= np.linalg.norm(level)
    slope = np.cross(normal, level)  # steepest line inside the plane
    slope /= np.linalg.norm(slope)

    if direction == "level":
        d = level
    elif direction == "slope":
        d = slope
    elif 0.05 < abs(float(normal[2])) < 0.95:
        # tilted plane (roof, ramp): beams run down the slope, so its profile is one smooth
        # diagonal instead of a staircase of stacked horizontal beams
        d = slope
    else:  # vertical or horizontal plane: fewest, longest beams
        d = _fewest_lines_direction(normal, pts, [level, slope] + list(edge_dirs))
    e = np.cross(normal, d)
    e /= np.linalg.norm(e)
    return d, e


def hatch_region(
    topo: Topology, region: Region, spacing: float, max_len: float, direction: str = "auto",
    sharp: set[tuple[int, int]] | None = None,
) -> list[Beam]:
    """Fill a planar region with parallel beams (1 m apart = closed surface).

    Beam ends are pulled in by half a beam only where the region meets a sharp edge (so no
    staircase pokes out past a crisp border). At soft seams the beams run right up to the
    neighbouring face, so there is no crack between the two.
    """
    tris = topo.tris[region.faces]
    border = _region_border(topo, region)
    if sharp is None:
        sharp = set(border)
    seg_a = topo.verts[[u for u, _ in border]] if border else np.zeros((0, 3))
    seg_b = topo.verts[[v for _, v in border]] if border else np.zeros((0, 3))
    seg_sharp = np.array([k in sharp for k in border], dtype=bool)

    def near_sharp(p: np.ndarray) -> bool:
        if not len(seg_a):
            return True
        ab = seg_b - seg_a
        tt = np.clip(np.einsum("ij,ij->i", p - seg_a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-12), 0, 1)
        d = np.linalg.norm(seg_a + ab * tt[:, None] - p, axis=1)
        return bool(seg_sharp[int(np.argmin(d))])

    pts = tris.reshape(-1, 3)
    d, e = _plane_frame(region.normal, pts, direction, _border_directions(topo, region))
    origin = pts.mean(axis=0)
    # 2D coordinates: s along the beams, t across them
    s = (tris - origin) @ d  # (F, 3)
    t = (tris - origin) @ e
    t_min, t_max = float(t.min()), float(t.max())
    width = t_max - t_min
    n_lines = max(int(math.ceil(width / spacing - 1e-6)), 1)
    beams: list[Beam] = []
    for k in range(n_lines):
        tl = t_min + (k + 0.5) * width / n_lines
        intervals = _line_triangle_intervals(s, t, tl)
        for lo, hi in _union(intervals, gap=0.5):
            if hi - lo < 0.5:
                continue
            a = origin + lo * d + tl * e
            b = origin + hi * d + tl * e
            if hi - lo > 1.5:
                if near_sharp(a):
                    a = a + 0.5 * d
                if near_sharp(b):
                    b = b - 0.5 * d
            beams.extend(split_segment(a, b, max_len, "plane"))
    return beams


def _line_triangle_intervals(s: np.ndarray, t: np.ndarray, tl: float) -> list[tuple[float, float]]:
    """Intervals along s where the line t = tl crosses each triangle (vectorised)."""
    out = []
    lo_t, hi_t = t.min(axis=1), t.max(axis=1)
    hit = (lo_t <= tl) & (hi_t >= tl)
    for sf, tf in zip(s[hit], t[hit]):
        xs = []
        for i in range(3):
            j = (i + 1) % 3
            t0, t1 = tf[i], tf[j]
            if (t0 - tl) * (t1 - tl) <= 0 and t0 != t1:
                u = (tl - t0) / (t1 - t0)
                xs.append(sf[i] + u * (sf[j] - sf[i]))
            elif t0 == t1 == tl:
                xs += [sf[i], sf[j]]
        if xs:
            out.append((min(xs), max(xs)))
    return out


def _union(intervals: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for lo, hi in sorted(intervals):
        if out and lo <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(a, b) for a, b in out]


def _chain_edges(edges: list[tuple[int, int]]) -> list[tuple[list[int], bool]]:
    """Chain undirected vertex-index edges into polylines. Returns (vertex list, closed)."""
    adj: dict[int, list[int]] = {}
    for u, v in edges:
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)
    used: set[tuple[int, int]] = set()
    chains: list[tuple[list[int], bool]] = []

    def walk(start: int, nxt: int) -> list[int]:
        path = [start, nxt]
        used.add((min(start, nxt), max(start, nxt)))
        while len(adj[path[-1]]) == 2:
            cand = [w for w in adj[path[-1]] if (min(path[-1], w), max(path[-1], w)) not in used]
            if not cand:
                break
            used.add((min(path[-1], cand[0]), max(path[-1], cand[0])))
            path.append(cand[0])
        return path

    # open chains start at vertices that are not of degree 2
    for v, ns in adj.items():
        if len(ns) != 2:
            for w in ns:
                if (min(v, w), max(v, w)) not in used:
                    chains.append((walk(v, w), False))
    # remaining edges form closed loops
    for v, ns in adj.items():
        for w in ns:
            if (min(v, w), max(v, w)) not in used:
                path = walk(v, w)
                chains.append((path, path[0] == path[-1]))
    return chains


def _region_border(topo: Topology, region: Region) -> list[tuple[int, int]]:
    count: dict[tuple[int, int], int] = {}
    for f in region.faces:
        a, b, c = topo.vidx[f]
        for u, v in ((a, b), (b, c), (c, a)):
            if u != v:
                key = (min(u, v), max(u, v))
                count[key] = count.get(key, 0) + 1
    return [k for k, n in count.items() if n == 1]


def _border_directions(topo: Topology, region: Region, limit: int = 8) -> list[np.ndarray]:
    """Directions of the longest border edges (hatching parallel to an edge looks clean)."""
    edges = _region_border(topo, region)
    vecs = [topo.verts[v] - topo.verts[u] for u, v in edges]
    vecs.sort(key=lambda v: -float(np.linalg.norm(v)))
    return vecs[:limit]


def region_outline(topo: Topology, region: Region, tol: float, max_len: float) -> list[Beam]:
    """Beams along the border of a planar region (crisp edges for hatched faces)."""
    beams: list[Beam] = []
    for chain, closed in _chain_edges(_region_border(topo, region)):
        pts = topo.verts[chain[:-1] if closed else chain]
        beams.extend(_dense_polyline_to_beams(pts, tol, max_len, "outline", closed))
    return beams


def _dense_polyline_to_beams(pts: np.ndarray, tol: float, max_len: float, kind: str, closed: bool) -> list[Beam]:
    """Resample at <= 0.5 m so grid snapping follows the true shape, then straighten."""
    if len(pts) < 2:
        return []
    seq = np.vstack([pts, pts[:1]]) if closed else pts
    dense = [seq[0]]
    for a, b in zip(seq, seq[1:]):
        n = max(int(math.ceil(np.linalg.norm(b - a) / 0.5)), 1)
        dense.extend(a + (b - a) * (k / n) for k in range(1, n + 1))
    dense_arr = np.array(dense[:-1] if closed else dense)
    return polyline_to_beams(dense_arr, tol, max_len, kind, closed=closed)


def sharp_border_fraction(topo: Topology, region: Region, sharp: set[tuple[int, int]]) -> float:
    border = _region_border(topo, region)
    if not border:
        return 1.0
    lengths = np.array([np.linalg.norm(topo.verts[u] - topo.verts[v]) for u, v in border])
    flags = np.array([k in sharp for k in border])
    return float(lengths[flags].sum() / max(lengths.sum(), 1e-9))


def beams_from_planes(
    topo: Topology,
    spacing: float = 1.0,
    max_len: float = 40,
    tol: float = 0.5,
    direction: str = "auto",
    min_area: float = 6.0,
    outline: bool = True,
) -> tuple[list[Beam], np.ndarray]:
    """Planes mode. Returns (beams, mask of curved faces that weren't covered)."""
    regions, curved = find_planar_regions(topo, min_area=min_area)
    sharp = sharp_edge_set(topo)
    beams: list[Beam] = []
    for r in regions:
        beams += hatch_region(topo, r, spacing, max_len, direction, sharp)
        if outline:
            beams += region_outline(topo, r, tol, max_len)
    return beams, curved


# --------------------------------------------------------------------------- feature edges


def feature_edges(topo: Topology, angle: float = 30.0, tol: float = 0.5, max_len: float = 40,
                  lattice: bool = False) -> list[Beam]:
    """Beams along sharp edges (dihedral angle > `angle`) and open borders of the mesh."""
    sharp = sorted(sharp_edge_set(topo, angle))
    if lattice:
        segs = [(topo.verts[u], topo.verts[v]) for u, v in sharp]
        return beams_from_segments_lattice(segs, max_len, "edge")
    beams: list[Beam] = []
    for chain, closed in _chain_edges(sharp):
        pts = topo.verts[chain[:-1] if closed else chain]
        beams.extend(_dense_polyline_to_beams(pts, tol, max_len, "edge", closed))
    return beams


# --------------------------------------------------------------------------- contours


def slice_plane(tris: np.ndarray, normal: np.ndarray, offset: float) -> list[np.ndarray]:
    """Intersect triangles with the plane normal . x = offset; returns chained polylines."""
    dist = tris @ normal - offset  # (F, 3)
    dist = np.where(np.abs(dist) < 1e-9, 1e-9, dist)  # avoid vertices exactly on the plane
    sign = dist > 0
    crossing = sign.any(axis=1) & ~sign.all(axis=1)
    segs = []
    for tri, dd, sg in zip(tris[crossing], dist[crossing], sign[crossing]):
        pts = []
        for i in range(3):
            j = (i + 1) % 3
            if sg[i] != sg[j]:
                u = dd[i] / (dd[i] - dd[j])
                pts.append(tri[i] + u * (tri[j] - tri[i]))
        if len(pts) == 2:
            segs.append(pts)
    return _chain_segments(segs)


def _chain_segments(segs: list[list[np.ndarray]]) -> list[np.ndarray]:
    if not segs:
        return []
    key = lambda p: tuple(np.round(p, 5))  # noqa: E731
    adj: dict[tuple, list[int]] = {}
    for i, (a, b) in enumerate(segs):
        adj.setdefault(key(a), []).append(i)
        adj.setdefault(key(b), []).append(i)
    used = np.zeros(len(segs), dtype=bool)
    polylines = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        line = [segs[i][0], segs[i][1]]
        for forward in (True, False):
            while True:
                end = line[-1] if forward else line[0]
                nxt = [j for j in adj[key(end)] if not used[j]]
                if not nxt:
                    break
                j = nxt[0]
                used[j] = True
                a, b = segs[j]
                p = b if key(a) == key(end) else a
                if forward:
                    line.append(p)
                else:
                    line.insert(0, p)
        polylines.append(np.array(line))
    return polylines


def _polylines_to_beams(polys: list[np.ndarray], tol: float, max_len: float, kind: str) -> list[Beam]:
    beams: list[Beam] = []
    for poly in polys:
        closed = len(poly) > 2 and np.allclose(poly[0], poly[-1], atol=1e-4)
        pts = poly[:-1] if closed else poly
        beams += _dense_polyline_to_beams(pts, tol, max_len, kind, closed)
    return beams


# slicing plane families u . x = k (k integer). Diagonal families have slices only
# 1/|u| m apart, which keeps 45° surfaces closed and gives staves more directions to follow.
SLICE_FAMILIES: list[tuple[int, int, int]] = [
    (0, 0, 1), (1, 0, 0), (0, 1, 0),
    (1, 1, 0), (1, -1, 0), (1, 0, 1), (1, 0, -1), (0, 1, 1), (0, 1, -1),
    (2, 1, 0), (1, 2, 0), (2, -1, 0), (1, -2, 0),
]


def local_flow_directions(tris: np.ndarray, radius: float | None = None) -> np.ndarray:
    """Per face: the direction its part of the model runs along (unit vectors).

    Principal axis of the surface around each face within `radius`. On a long part (a tube,
    a streak, a rocket body) that is its length direction. Where there is no clear long
    direction the result is vertical.
    """
    pts = tris.reshape(-1, 3)
    size = float((pts.max(axis=0) - pts.min(axis=0)).max())
    if radius is None:
        radius = min(max(0.08 * size, 4.0), 15.0)
    cent = tris.mean(axis=1)
    area = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1) / 2 + 1e-9
    # sample each face with its centroid and vertices, weighted by area
    samp = np.concatenate([cent, tris[:, 0], tris[:, 1], tris[:, 2]])
    w = np.concatenate([area, area / 3, area / 3, area / 3])
    jobs = [(samp, w, cent, radius, sl.start, sl.stop) for sl in parallel.chunks(len(tris), min_size=500)]
    return np.concatenate(parallel.pmap(_flow_range, jobs))


def _flow_range(samp, w, cent, radius, start, stop):
    """local_flow_directions for faces [start, stop) (runs in a worker process)."""
    cells: dict[tuple, list[int]] = {}
    keys = np.floor(samp / radius).astype(int)
    for i, k in enumerate(map(tuple, keys)):
        cells.setdefault(k, []).append(i)
    cells_np = {k: np.array(v) for k, v in cells.items()}
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    out = np.tile(np.array([0.0, 0.0, 1.0]), (stop - start, 1))
    ckeys = np.floor(cent / radius).astype(int)
    r2 = radius * radius
    for f in range(start, stop):
        k = ckeys[f]
        idx = [cells_np[c] for o in offs if (c := (k[0] + o[0], k[1] + o[1], k[2] + o[2])) in cells_np]
        idx = np.concatenate(idx)
        q = samp[idx]
        d2 = ((q - cent[f]) ** 2).sum(axis=1)
        m = d2 <= r2
        if m.sum() < 4:
            continue
        q, ww = q[m], w[idx[m]]
        mu = (q * ww[:, None]).sum(axis=0) / ww.sum()
        c = q - mu
        cov = (c * ww[:, None]).T @ c / ww.sum()
        vals, vecs = np.linalg.eigh(cov)
        if vals[2] > 1.6 * max(vals[1], 1e-9):
            out[f - start] = vecs[:, 2]
    return out


def _choose_families(n: np.ndarray, spacing: float, pattern: str, flow: np.ndarray | None):
    """Pick a slicing family per face. Returns (families, steps, choice index per face)."""
    fams = SLICE_FAMILIES
    steps, gaps, aligns = [], [], []
    for u in fams:
        ua = np.array(u, dtype=float)
        un = float(np.linalg.norm(ua))
        st = max(int(round(spacing * un)), 1)
        steps.append(st)
        uh = ua / un
        # distance between neighbouring slices measured ON each face
        gaps.append((st / un) / np.sqrt(np.clip(1 - (n @ uh) ** 2, 1e-6, 1)))
        if flow is not None:
            t = np.cross(n, uh)  # direction the slice runs across the face
            tn = np.linalg.norm(t, axis=1)
            aligns.append(np.abs((t * flow).sum(axis=1)) / np.maximum(tn, 1e-9) * (tn > 1e-6))
    gaps = np.stack(gaps, axis=1)
    rows = np.arange(len(n))
    if pattern == "flow" and flow is not None:
        aligns = np.stack(aligns, axis=1)
        # simpler families win near-ties so neighbouring faces agree (long continuous staves)
        simple = np.array([0 if sum(map(abs, u)) == 1 else 0.02 if max(map(abs, u)) == 1 else 0.04 for u in fams])
        score = np.where(gaps <= spacing * 1.05, aligns - simple, -np.inf)
        choice = np.where(np.isfinite(score.max(axis=1)), score.argmax(axis=1), gaps.argmin(axis=1))
    else:
        # rings: plain axis slices wherever they're already <= ~1 m apart on the face;
        # only steep-diagonal faces fall back to the tighter diagonal families
        axis_best = gaps[:, :3].argmin(axis=1)
        choice = np.where(gaps[rows, axis_best] <= spacing * 1.05, axis_best, gaps[:, :9].argmin(axis=1))
    return fams, steps, choice


def beams_from_contours(
    tris: np.ndarray,
    ring_spacing: float = 1.0,
    rib_spacing: float = 0.0,
    axes: str = "auto",
    tol: float = 0.5,
    max_len: float = 40,
    pattern: str = "flow",
    flow: np.ndarray | None = None,
) -> list[Beam]:
    """Slice the surface with parallel planes at integer levels about `ring_spacing` apart.

    pattern="flow" (staves): every face is sliced so the beams run along the direction its
    part of the model runs - up a rocket body, along a twisted streak - side by side like
    barrel staves. `flow` can pass precomputed directions (see local_flow_directions).
    pattern="rings": steep walls get horizontal rings, flatter tops vertical arcs.
    axes="x"/"z"/"xyz"...: plain slices perpendicular to those axes instead.
    Ribs: optional vertical half-planes around the centre axis, ~`rib_spacing` apart at the
    widest point (0 = none).
    """
    beams: list[Beam] = []
    if len(tris) == 0:
        return beams
    pts = tris.reshape(-1, 3)
    lo, hi = pts.min(axis=0), pts.max(axis=0)

    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    if axes == "auto":
        if pattern == "flow" and flow is None:
            flow = local_flow_directions(tris)
        fams, steps, choice = _choose_families(n, ring_spacing, pattern, flow)
        # (seams between groups are closed by the gap-filling pass afterwards)
        groups = [(u, steps[k], choice == k) for k, u in enumerate(fams)]
    else:
        step = max(int(round(ring_spacing)), 1)
        groups = [(tuple(int(a == c) for c in "xyz"), step, np.ones(len(tris), dtype=bool)) for a in axes]

    jobs = []
    for u, step, mask in groups:
        if not mask.any():
            continue
        sub = tris[mask]
        u_arr = np.array(u, dtype=float)
        kind = "ring" if u == (0, 0, 1) else ("stave" if pattern == "flow" else "arc")
        lv = tris @ u_arr  # level of every vertex (integer points have integer levels)
        s_lv = sub @ u_arr
        levels = list(range(int(math.floor(s_lv.min())), int(math.ceil(s_lv.max())) + 1, step))
        # independent slices: spread them over the CPU cores in batches
        for k in range(0, len(levels), 16):
            jobs.append((sub, u, levels[k:k + 16], float(lv.min()), float(lv.max()), tol, max_len, kind,
                         beams_mod.PRECISE))
    for part in parallel.pmap(_slice_levels, jobs):
        beams += part

    if rib_spacing > 0:
        centre = (lo + hi) / 2
        radius = float(np.linalg.norm(pts[:, :2] - centre[:2], axis=1).max())
        count = max(int(round(2 * math.pi * radius / rib_spacing)), 4)
        for k in range(count):
            ang = 2 * math.pi * k / count
            out_dir = np.array([math.cos(ang), math.sin(ang), 0.0])
            normal = np.array([-math.sin(ang), math.cos(ang), 0.0])
            polys = slice_plane(tris, normal, float(normal @ centre))
            half = []
            for p in polys:
                # keep only the part on the outward side of the centre axis
                side = (p - centre) @ out_dir > 0
                for run in _true_runs(side):
                    if len(run) >= 2:
                        half.append(p[run])
            beams += _polylines_to_beams(half, tol, max_len, "rib")
    return beams


def _slice_levels(sub, u, levels, lo_l, hi_l, tol, max_len, kind, precise=False) -> list[Beam]:
    """Cut `sub` with the planes u . x = level for each level and straighten into beams."""
    from . import beams as beams_mod

    beams_mod.set_precise(precise)  # runs in a worker process: carry the mode over
    u_arr = np.array(u, dtype=float)
    norm = float(np.linalg.norm(u_arr))
    normal = u_arr / norm
    beams: list[Beam] = []
    for level in levels:
        # nudge the cutting plane inside so faces lying exactly on a level still get cut
        h = min(max(level, lo_l + 0.05), hi_l - 0.05)
        polys = slice_plane(sub, normal, h / norm)
        for p in polys:
            p += np.outer(level - p @ u_arr, u_arr) / (norm * norm)  # exactly onto the level
        beams += _polylines_to_beams(polys, tol, max_len, kind)
    return beams


def _true_runs(mask: np.ndarray) -> list[np.ndarray]:
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    return np.split(idx, breaks + 1)


# --------------------------------------------------------------------------- wireframe


def beams_from_mesh_edges(
    topo: Topology, max_len: float = 40, feature_angle: float = 1.0, lattice: bool = True
) -> list[Beam]:
    """One beam per mesh edge (edges between nearly coplanar faces skipped)."""
    cos_limit = math.cos(math.radians(feature_angle))
    segs = []
    for (u, v), faces in topo.edge_faces.items():
        if len(faces) == 2 and abs(float(topo.normals[faces[0]] @ topo.normals[faces[1]])) >= cos_limit:
            continue
        segs.append((topo.verts[u], topo.verts[v]))
    if lattice:
        return beams_from_segments_lattice(segs, max_len, "edge")
    from .beams import beams_from_segments

    return beams_from_segments(segs, max_len, "edge")


# --------------------------------------------------------------------------- hybrid


def beams_hybrid(
    topo: Topology,
    spacing: float = 1.0,
    ring_spacing: float = 1.0,
    rib_spacing: float = 0.0,
    tol: float = 0.5,
    max_len: float = 40,
    direction: str = "auto",
    min_area: float = 6.0,
    edge_angle: float = 30.0,
    flat_border: float = 0.75,
    pattern: str = "flow",
) -> list[Beam]:
    """Planes where the model is flat, contours where it's curved, beams on sharp edges.

    A flat region only counts as a real flat face if most of its border is a sharp edge.
    Facets of a low-poly curved surface (cylinders, twisted shapes) have soft seams, so they
    are sliced as one continuous curved surface instead of being hatched facet by facet.
    """
    sharp = sharp_edge_set(topo, edge_angle)
    regions, curved = find_planar_regions(topo, min_area=min_area)
    beams: list[Beam] = []
    for r in regions:
        if sharp_border_fraction(topo, r, sharp) >= flat_border:
            beams += hatch_region(topo, r, spacing, max_len, direction, sharp)
        else:
            curved[r.faces] = True
    if curved.any():
        flow = local_flow_directions(topo.tris)[curved] if pattern == "flow" else None
        beams += beams_from_contours(topo.tris[curved], ring_spacing, rib_spacing, "auto", tol, max_len,
                                     pattern, flow)
    beams += feature_edges(topo, edge_angle, tol, max_len)
    return beams
