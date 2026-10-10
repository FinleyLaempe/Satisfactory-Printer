"""Minimal mesh loading (STL ascii/binary, OBJ) into an (N, 3, 3) triangle array."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def load_mesh(path: str | Path) -> np.ndarray:
    """Return triangles as float64 array of shape (N, 3, 3)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".stl":
        return _load_stl(path)
    if suffix == ".obj":
        return _load_obj(path)
    raise ValueError(f"Unsupported mesh format: {suffix} (use .stl or .obj)")


def _load_stl(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if 84 + count * 50 == len(data):
            return _parse_binary_stl(data, count)
    return _parse_ascii_stl(data.decode("utf-8", errors="replace"))


def _parse_binary_stl(data: bytes, count: int) -> np.ndarray:
    dtype = np.dtype([("normal", "<f4", 3), ("verts", "<f4", (3, 3)), ("attr", "<u2")])
    records = np.frombuffer(data, dtype=dtype, count=count, offset=84)
    return records["verts"].astype(np.float64)


def _parse_ascii_stl(text: str) -> np.ndarray:
    verts = [
        [float(v) for v in line.split()[1:4]]
        for line in text.splitlines()
        if line.strip().startswith("vertex")
    ]
    if len(verts) % 3:
        raise ValueError("Malformed ASCII STL: vertex count is not a multiple of 3")
    return np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)


def _load_obj(path: Path) -> np.ndarray:
    vertices: list[list[float]] = []
    tris: list[list[int]] = []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            vertices.append([float(p) for p in parts[1:4]])
        elif parts[0] == "f":
            idx = []
            for p in parts[1:]:
                i = int(p.split("/")[0])
                idx.append(i - 1 if i > 0 else len(vertices) + i)
            # fan triangulation for polygons
            for k in range(1, len(idx) - 1):
                tris.append([idx[0], idx[k], idx[k + 1]])
    if not tris:
        raise ValueError("OBJ file contains no faces")
    v = np.asarray(vertices, dtype=np.float64)
    return v[np.asarray(tris)]


def save_stl_ascii(path: str | Path, triangles: np.ndarray, name: str = "model") -> None:
    lines = [f"solid {name}"]
    for tri in triangles:
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        norm = np.linalg.norm(n)
        n = n / norm if norm else n
        lines.append(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}")
        lines.append("    outer loop")
        for v in tri:
            lines.append(f"      vertex {v[0]:.6e} {v[1]:.6e} {v[2]:.6e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    Path(path).write_text("\n".join(lines) + "\n")


def connected_parts(triangles: np.ndarray) -> np.ndarray:
    """Part id per triangle: triangles sharing a vertex belong to the same part."""
    keys = np.round(triangles.reshape(-1, 3), 4)
    _, vid = np.unique(keys, axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    parent = np.arange(vid.max() + 1)

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, c in vid:
        ra = find(a)
        for v in (b, c):
            rv = find(v)
            if rv != ra:
                parent[rv] = ra
    return np.array([find(a) for a in vid[:, 0]])


def drop_base(triangles: np.ndarray, height_fraction: float) -> np.ndarray:
    """Remove every separate part that lies completely below `height_fraction` of the model height.

    Typical use: cut a statue off its pedestal (`0.16` = parts entirely in the bottom 16 %).
    Model Z is up.
    """
    z = triangles[..., 2]
    lo, hi = float(z.min()), float(z.max())
    cut = lo + height_fraction * (hi - lo)
    part = connected_parts(triangles)
    tops = {p: float(z[part == p].max()) for p in np.unique(part)}
    keep = np.array([tops[p] > cut for p in part])
    if not keep.any():
        raise ValueError("drop_base would remove the whole model - use a smaller fraction")
    return triangles[keep]
