"""Turn a triangle mesh into a 1 m voxel grid (one voxel = one beam cross-section)."""

from __future__ import annotations

import numpy as np


def scale_to_grid(triangles: np.ndarray, size: float | None = None, scale: float | None = None) -> np.ndarray:
    """Scale the mesh to grid units (1 unit = 1 m = 1 nudge) and move its min corner to the origin.

    size:  target length (in metres) of the model's largest dimension.
    scale: metres per model unit. Ignored if size is given.
    Model Z is treated as "up".
    """
    tris = triangles.copy()
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    if size is not None:
        extent = float((hi - lo).max())
        factor = size / extent if extent else 1.0
    else:
        factor = scale if scale is not None else 1.0
    tris = (tris - lo) * factor
    return tris


def voxelize_solid(tris: np.ndarray) -> np.ndarray:
    """Solid voxelization by ray parity along +Z through every column centre.

    Requires a reasonably watertight mesh. Returns a bool array indexed [x, y, z].
    """
    hi = tris.reshape(-1, 3).max(axis=0)
    nx, ny, nz = (max(int(np.ceil(h)), 1) for h in hi)
    grid = np.zeros((nx, ny, nz), dtype=bool)

    # tiny irrational jitter so rays don't hit edges/vertices exactly
    jx, jy = 0.5 + 1.234567e-4, 0.5 + 7.654321e-5
    hits: dict[tuple[int, int], list[float]] = {}

    for tri in tris:
        a, b, c = tri
        x0, x1 = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
        y0, y1 = min(a[1], b[1], c[1]), max(a[1], b[1], c[1])
        ix = np.arange(max(int(np.ceil(x0 - jx)), 0), min(int(np.floor(x1 - jx)), nx - 1) + 1)
        iy = np.arange(max(int(np.ceil(y0 - jy)), 0), min(int(np.floor(y1 - jy)), ny - 1) + 1)
        if ix.size == 0 or iy.size == 0:
            continue
        px, py = np.meshgrid(ix + jx, iy + jy, indexing="ij")
        px, py = px.ravel(), py.ravel()

        # barycentric coordinates in XY projection
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-12:
            continue  # triangle is vertical in XY -> never hit by a Z ray
        w0 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
        w1 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        z = w0 * a[2] + w1 * b[2] + w2 * c[2]
        for x, y, zz in zip(px[inside], py[inside], z[inside]):
            hits.setdefault((int(x), int(y)), []).append(float(zz))

    for (x, y), zs in hits.items():
        zs.sort()
        for k in range(0, len(zs) - 1, 2):
            z_in, z_out = zs[k], zs[k + 1]
            # fill voxels whose centre lies between entry and exit
            k0 = max(int(np.ceil(z_in - 0.5)), 0)
            k1 = min(int(np.floor(z_out - 0.5)), nz - 1)
            if k1 >= k0:
                grid[x, y, k0 : k1 + 1] = True
    return grid


def voxelize_surface(tris: np.ndarray) -> np.ndarray:
    """Shell voxelization by dense sampling of each triangle. Works on non-watertight meshes."""
    hi = tris.reshape(-1, 3).max(axis=0)
    shape = tuple(max(int(np.ceil(h)), 1) for h in hi)
    grid = np.zeros(shape, dtype=bool)
    for tri in tris:
        a, b, c = tri
        longest = max(np.linalg.norm(b - a), np.linalg.norm(c - a), np.linalg.norm(c - b))
        n = max(int(np.ceil(longest * 2)), 1)  # 0.5 m sample spacing
        u, v = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        mask = (u + v) <= n
        u, v = u[mask] / n, v[mask] / n
        pts = a + np.outer(u, b - a) + np.outer(v, c - a)
        idx = np.floor(pts).astype(int)
        for axis in range(3):
            idx[:, axis] = np.clip(idx[:, axis], 0, shape[axis] - 1)
        grid[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return grid


def hollow(grid: np.ndarray) -> np.ndarray:
    """Keep only voxels that touch empty space on one of their 6 faces."""
    padded = np.pad(grid, 1, constant_values=False)
    interior = padded[1:-1, 1:-1, 1:-1].copy()
    for axis in range(3):
        for shift in (-1, 1):
            interior &= np.roll(padded, shift, axis=axis)[1:-1, 1:-1, 1:-1]
    return grid & ~interior
