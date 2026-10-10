"""Generate small test models: a cube, a pyramid and a torus (STL)."""

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


def spiral_gcode(radius=8.0, height=12.0, turns=3, points_per_turn=24):
    """A rising spiral like a non-planar slicer would emit: X, Y and Z change in every move."""
    lines = ["; synthetic non-planar toolpath (units = mm, use --scale 1)", "G90", "M82", "G92 E0"]
    n = turns * points_per_turn
    e = 0.0
    for k in range(n + 1):
        a = 2 * np.pi * k / points_per_turn
        x, y, z = radius * np.cos(a) + radius, radius * np.sin(a) + radius, height * k / n
        cmd = "G0" if k == 0 else "G1"
        e += 0 if k == 0 else 1
        lines.append(f"{cmd} X{x:.3f} Y{y:.3f} Z{z:.3f}" + ("" if k == 0 else f" E{e:.3f}"))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (HERE / "spiral.gcode").write_text(spiral_gcode())
    save_stl_ascii(HERE / "cube.stl", cube())
    save_stl_ascii(HERE / "pyramid.stl", pyramid())
    save_stl_ascii(HERE / "torus.stl", torus())
    print("wrote cube.stl, pyramid.stl, torus.stl, spiral.gcode")
