"""Generate small test models (STL): cube, pyramid, torus, house, ramp, sphere."""

from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from satisfactory_printer.mesh import save_stl_ascii  # noqa: E402

HERE = Path(__file__).parent


def cube(n=1.0):
    v = np.array([[x, y, z] for x in (0, n) for y in (0, n) for z in (0, n)], dtype=float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return np.array([[v[a], v[b], v[c]] for a, b, c, d in quads] + [[v[a], v[c], v[d]] for a, b, c, d in quads])


def pyramid():
    b = [np.array(p, float) for p in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))]
    top = np.array([0.5, 0.5, 0.8])
    tris = [[b[0], b[2], b[1]], [b[0], b[3], b[2]]]
    for i in range(4):
        tris.append([b[i], b[(i + 1) % 4], top])
    return np.array(tris)


def torus(R=1.0, r=0.35, nu=48, nv=24):
    u = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    v = np.linspace(0, 2 * np.pi, nv, endpoint=False)
    def p(i, j):
        a, b = u[i % nu], v[j % nv]
        return np.array([(R + r * np.cos(b)) * np.cos(a), (R + r * np.cos(b)) * np.sin(a), r * np.sin(b)])
    tris = []
    for i in range(nu):
        for j in range(nv):
            tris.append([p(i, j), p(i + 1, j), p(i + 1, j + 1)])
            tris.append([p(i, j), p(i + 1, j + 1), p(i, j + 1)])
    return np.array(tris)


def convex_hull_tris(points):
    """Clean, consistently wound triangles of a convex polyhedron given its vertices."""
    pts = np.asarray(points, float)
    centre = pts.mean(axis=0)
    faces = {}
    n = len(pts)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                nrm = np.cross(pts[j] - pts[i], pts[k] - pts[i])
                if np.linalg.norm(nrm) < 1e-9:
                    continue
                nrm = nrm / np.linalg.norm(nrm)
                if (centre - pts[i]) @ nrm > 0:
                    nrm = -nrm
                d = (pts - pts[i]) @ nrm
                if (d <= 1e-9).all():
                    key = tuple(np.round(np.append(nrm, nrm @ pts[i]), 6))
                    faces[key] = np.flatnonzero(np.abs(d) < 1e-9)
    tris = []
    for key, idx in faces.items():
        nrm = np.array(key[:3])
        face = pts[idx]
        c = face.mean(axis=0)
        u = face[0] - c
        u /= np.linalg.norm(u)
        v = np.cross(nrm, u)
        order = np.argsort(np.arctan2((face - c) @ v, (face - c) @ u))
        ring = face[order]
        for m in range(1, len(ring) - 1):
            tris.append([ring[0], ring[m], ring[m + 1]])
    return np.array(tris)


def house():
    """Box body (8 x 6 x 4) with a gable roof (ridge at 7): two 37° sloped roof planes."""
    return convex_hull_tris(
        [(0, 0, 0), (8, 0, 0), (8, 6, 0), (0, 6, 0), (0, 0, 4), (8, 0, 4), (8, 6, 4), (0, 6, 4), (0, 3, 7), (8, 3, 7)]
    )


def ramp():
    """A wedge: one face is a 30° slope, one side is a vertical diagonal plane."""
    return convex_hull_tris([(0, 0, 0), (10, 0, 0), (10, 4, 0), (0, 4, 0), (10, 0, 5.77), (10, 4, 5.77)])


def sphere(n_lat=24, n_lon=48):
    tris = []
    def p(i, j):
        th, ph = np.pi * i / n_lat, 2 * np.pi * j / n_lon
        return np.array([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)])
    for i in range(n_lat):
        for j in range(n_lon):
            a, b, c, d = p(i, j), p(i + 1, j), p(i + 1, j + 1), p(i, j + 1)
            if i > 0:
                tris.append([a, b, d])
            if i < n_lat - 1:
                tris.append([b, c, d])
    return np.array(tris)


if __name__ == "__main__":
    save_stl_ascii(HERE / "house.stl", house())
    save_stl_ascii(HERE / "ramp.stl", ramp())
    save_stl_ascii(HERE / "sphere.stl", sphere())
    save_stl_ascii(HERE / "cube.stl", cube())
    save_stl_ascii(HERE / "pyramid.stl", pyramid())
    save_stl_ascii(HERE / "torus.stl", torus())
    print("wrote cube, pyramid, torus, house, ramp, sphere (.stl)")
