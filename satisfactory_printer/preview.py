"""Previews: export beams as an OBJ you can open in any 3D viewer, or print ASCII layers."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .beams import Beam


def export_obj(beams: list[Beam], path: str | Path, thickness: float = 1.0) -> None:
    """Each beam becomes a box with a thickness x thickness cross-section."""
    lines = ["# Satisfactory-Printer beam preview"]
    v_count = 0
    half = thickness / 2
    for beam in beams:
        s = np.array(beam.start, dtype=float)
        e = np.array(beam.end, dtype=float)
        d = e - s
        length = np.linalg.norm(d)
        if length == 0:
            continue
        d /= length
        helper = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        u = np.cross(d, helper)
        u /= np.linalg.norm(u)
        w = np.cross(d, u)
        corners = []
        for p in (s, e):
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                corners.append(p + half * (a * u + b * w))
        for c in corners:
            lines.append(f"v {c[0]:.4f} {c[2]:.4f} {-c[1]:.4f}")  # Z-up -> Y-up for viewers
        o = v_count + 1
        faces = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
        for f in faces:
            lines.append("f " + " ".join(str(o + i) for i in f))
        v_count += 8
    Path(path).write_text("\n".join(lines) + "\n")


def ascii_layers(grid: np.ndarray, max_layers: int = 200) -> str:
    """Top-down slices of the voxel grid, bottom layer first ('#' = filled)."""
    out = []
    for z in range(min(grid.shape[2], max_layers)):
        out.append(f"--- layer z={z} ---")
        for y in reversed(range(grid.shape[1])):
            out.append("".join("#" if grid[x, y, z] else "." for x in range(grid.shape[0])))
    return "\n".join(out) + "\n"
