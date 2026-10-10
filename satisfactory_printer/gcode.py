"""Read extrusion moves from slicer G-code and turn them into line segments.

This lets you use any slicer (Cura, PrusaSlicer, OrcaSlicer, or a non-planar / "unlayered"
slicer) as a path planner: every printed line becomes a beam. Slice with a layer height and
line width that match your scale, e.g. with --scale 1 one millimetre of G-code = one beam metre.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_WORD = re.compile(r"([A-Z])([-+]?\d*\.?\d+)")


def read_gcode_segments(path: str | Path, min_length: float = 0.0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return (start, end) pairs, in G-code units (usually mm), for every extruding move.

    Consecutive collinear moves are merged into one segment.
    """
    pos = np.zeros(3)
    e = 0.0
    absolute_xyz = True
    absolute_e = True
    segments: list[tuple[np.ndarray, np.ndarray]] = []

    for raw in Path(path).read_text(errors="replace").splitlines():
        line = raw.split(";", 1)[0].strip().upper()
        if not line:
            continue
        words = dict(_WORD.findall(line))
        cmd = line.split()[0]
        if cmd == "G90":
            absolute_xyz = absolute_e = True
        elif cmd == "G91":
            absolute_xyz = absolute_e = False
        elif cmd == "M82":
            absolute_e = True
        elif cmd == "M83":
            absolute_e = False
        elif cmd == "G92":
            if "E" in words:
                e = float(words["E"])
            for i, axis in enumerate("XYZ"):
                if axis in words:
                    pos[i] = float(words[axis])
        elif cmd in ("G0", "G1", "G00", "G01"):
            new = pos.copy()
            for i, axis in enumerate("XYZ"):
                if axis in words:
                    v = float(words[axis])
                    new[i] = v if absolute_xyz else pos[i] + v
            extruding = False
            if "E" in words:
                v = float(words["E"])
                new_e = v if absolute_e else e + v
                extruding = new_e > e + 1e-9
                e = new_e
            if extruding and not np.allclose(new, pos):
                segments.append((pos.copy(), new.copy()))
            pos = new

    merged = _merge_collinear(segments)
    if min_length > 0:
        merged = [(a, b) for a, b in merged if np.linalg.norm(b - a) >= min_length]
    return merged


def _merge_collinear(segments: list[tuple[np.ndarray, np.ndarray]]) -> list[tuple[np.ndarray, np.ndarray]]:
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for a, b in segments:
        if out:
            pa, pb = out[-1]
            if np.allclose(pb, a):
                d1 = (pb - pa) / np.linalg.norm(pb - pa)
                d2 = (b - a) / np.linalg.norm(b - a)
                if np.dot(d1, d2) > 0.9999:
                    out[-1] = (pa, b)
                    continue
        out.append((a, b))
    return out
