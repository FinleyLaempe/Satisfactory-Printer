"""Beam roll: turn each beam around its own axis so a flat face lies on the surface.

In-game the mouse wheel rolls the beam while it is being spanned (6 notches = 90°, so 15°
per notch). Wheel up turns the beam counter-clockwise seen from its end looking back at its
start - for a beam pointing up, counter-clockwise seen from above - i.e. a positive
(right-hand) turn about start->end. A beam's square cross-section looks the same every 90°, so the roll needed is
always between -45° and +45°, i.e. at most 3 notches either way.

Reference orientation (roll 0) is assumed to be the game's default: one face points "up" -
world up projected onto the plane perpendicular to the beam. For vertical beams, where that
is undefined, the reference is the player's forward direction (+y). Calibrate the sign with
`roll_direction` in the config if beams come out rolled the wrong way.
"""

from __future__ import annotations

import math

import numpy as np

from .beams import Beam


def reference_face(axis: np.ndarray) -> np.ndarray:
    """Unit vector perpendicular to `axis` that one face points to at roll 0."""
    for ref in (np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0])):
        r = ref - (ref @ axis) * axis
        n = np.linalg.norm(r)
        if n > 0.2:
            return r / n
    r = np.array([1.0, 0.0, 0.0]) - axis[0] * axis
    return r / np.linalg.norm(r)


def roll_steps(beams: list[Beam], tris: np.ndarray, step_deg: float = 15.0) -> list[int]:
    """Wheel notches per beam (signed) that put one beam face flat against the surface."""
    if not len(tris) or not beams:
        return [0] * len(beams)
    tris = np.asarray(tris, dtype=float)
    cent = tris.mean(axis=1)
    nrm = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    cell = 3.0
    cells: dict[tuple, list[int]] = {}
    for i, k in enumerate(map(tuple, np.floor(cent / cell).astype(int).tolist())):
        cells.setdefault(k, []).append(i)
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]

    def surface_normal(p: np.ndarray) -> np.ndarray | None:
        k = np.floor(p / cell).astype(int)
        cand = [i for o in offs for i in cells.get((k[0] + o[0], k[1] + o[1], k[2] + o[2]), ())]
        if not cand:
            return None
        j = cand[int(np.argmin(np.linalg.norm(cent[cand] - p, axis=1)))]
        return nrm[j]

    max_steps = int(round(45 / step_deg))
    out = []
    for b in beams:
        s, e = np.array(b.start, float), np.array(b.end, float)
        axis = (e - s) / max(np.linalg.norm(e - s), 1e-9)
        # average surface normal along the beam (sign-aligned)
        acc = np.zeros(3)
        for t in (0.15, 0.5, 0.85):
            n = surface_normal(s + (e - s) * t)
            if n is not None:
                acc += n if acc @ n >= 0 else -n
        m = acc - (acc @ axis) * axis
        if np.linalg.norm(m) < 1e-6:
            out.append(0)
            continue
        m /= np.linalg.norm(m)
        r = reference_face(axis)
        ang = math.degrees(math.atan2(float(np.cross(r, m) @ axis), float(r @ m)))
        ang = (ang + 45) % 90 - 45  # square cross-section: every 90° looks the same
        out.append(int(max(-max_steps, min(max_steps, round(ang / step_deg)))))
    return out
