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
    topo: Topology, region: Region, spacing: float, max_len: float, direction: str = "auto"
) -> list[Beam]:
    """Fill a planar region with parallel beams (1 m apart = closed surface)."""
    tris = topo.tris[region.faces]
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
            # pull the ends in by half a beam so the 1 m beam body stays inside the face
            # instead of poking out as a staircase along slanted borders
            if hi - lo > 1.5:
                lo, hi = lo + 0.5, hi - 0.5
            a = origin + lo * d + tl * e
            b = origin + hi * d + tl * e
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
    beams: list[Beam] = []
    for r in regions:
        beams += hatch_region(topo, r, spacing, max_len, direction)
        if outline:
            beams += region_outline(topo, r, tol, max_len)
    return beams, curved


# --------------------------------------------------------------------------- feature edges


def feature_edges(topo: Topology, angle: float = 30.0, tol: float = 0.5, max_len: float = 40,
                  lattice: bool = False) -> list[Beam]:
    """Beams along sharp edges (dihedral angle > `angle`) and open borders of the mesh."""
    cos_a = math.cos(math.radians(angle))
    sharp = []
    for key, faces in topo.edge_faces.items():
        if len(faces) == 1:
            sharp.append(key)
        elif len(faces) >= 2:
            # >2 faces = messy (non-manifold) mesh: sharp if any two of them disagree
            ns = topo.normals[faces]
            if float((ns @ ns.T).min()) < cos_a:
                sharp.append(key)
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


def beams_from_contours(
    tris: np.ndarray,
    ring_spacing: float = 1.0,
    rib_spacing: float = 0.0,
    axes: str = "auto",
    tol: float = 0.5,
    max_len: float = 40,
) -> list[Beam]:
    """Slice the surface with parallel planes at integer levels, `ring_spacing` apart.

    axes="auto": every face is sliced along the axis its normal is most perpendicular to, so
    steep walls get horizontal rings and flat-ish tops/bottoms get vertical slices that follow
    the curve as smooth arcs (instead of rings that drift far apart on gentle slopes).
    Otherwise slice perpendicular to each listed axis, e.g. "z" or "xyz".
    Ribs: optional vertical half-planes around the centre axis, ~`rib_spacing` apart at the
    widest point (0 = none).
    """
    beams: list[Beam] = []
    if len(tris) == 0:
        return beams
    pts = tris.reshape(-1, 3)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    step = max(int(round(ring_spacing)), 1)

    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n = np.abs(n) / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    groups: list[tuple[int, np.ndarray]] = []
    if axes == "auto":
        best = n.min(axis=1, keepdims=True)
        for i in (2, 0, 1):
            # small overlap between groups so their borders don't leave gaps
            mask = n[:, i] <= best[:, 0] + 0.25
            if mask.any():
                groups.append((i, mask))
    else:
        groups = [("xyz".index(a), np.ones(len(tris), dtype=bool)) for a in axes]

    for i, mask in groups:
        sub = tris[mask]
        normal = np.zeros(3)
        normal[i] = 1
        kind = "ring" if i == 2 else "arc"
        s_lo, s_hi = sub[..., i].min(), sub[..., i].max()
        for level in range(int(math.floor(lo[i])), int(math.ceil(hi[i])) + 1, step):
            if level < s_lo - 0.5 or level > s_hi + 0.5:
                continue
            # nudge the cutting plane inside so faces lying exactly on a level still get cut
            h = min(max(level, lo[i] + 0.05), hi[i] - 0.05)
            polys = slice_plane(sub, normal, h)
            for p in polys:
                p[:, i] = level  # keep the contour exactly on its grid level
            beams += _polylines_to_beams(polys, tol, max_len, kind)

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
) -> list[Beam]:
    """Planes where the model is flat, contours where it's curved, beams on sharp edges."""
    beams, curved = beams_from_planes(topo, spacing, max_len, tol, direction, min_area, outline=False)
    if curved.any():
        beams += beams_from_contours(topo.tris[curved], ring_spacing, rib_spacing, "auto", tol, max_len)
    beams += feature_edges(topo, edge_angle, tol, max_len)
    return beams
