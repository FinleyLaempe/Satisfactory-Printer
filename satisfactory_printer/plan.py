"""Turn beams into a keystroke plan.

Placement technique (per beam), with the player standing still and the crosshair resting
on a fixed ANCHOR point:

  1. lock the hologram (H) and nudge the beam START away from the anchor by (start - end).
     The beam now runs from that nudged point to the anchor, so the nudge vector itself
     defines the beam's length and direction.
  2. left click to start drawing (without moving the mouse) -> beam = (start - end) .. anchor
  3. press H again and nudge the whole beam by `end` (= its end point relative to the anchor)
  4. left click to build

The exact sequence is a configurable macro (see DEFAULT_CONFIG["beam_macro"]) so it can be
adapted if the game behaves slightly differently.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .beams import Beam, Vec

DEFAULT_CONFIG: dict[str, Any] = {
    # Which key moves the hologram one metre along each world axis while nudging.
    # +x = right, +y = forward (away from the player), +z = up. Nudge directions in the game
    # follow the camera, so keep the player facing the same direction the whole time.
    "keys": {
        "+x": "right",
        "-x": "left",
        "+y": "up",
        "-y": "down",
        "+z": "pageup",
        "-z": "pagedown",
        "lock": "h",
    },
    # Steps executed for every beam. Tokens:
    #   key:<name>      press a key (names from "keys", or a raw key name)
    #   nudge:shape     nudge by (start - end)  -> sets length/direction
    #   nudge:place     nudge by end            -> moves beam into place
    #   click           left mouse click
    #   roll            mouse wheel notches that turn the beam flat onto the surface
    #   wait:<seconds>  pause
    "beam_macro": [
        "key:lock",
        "nudge:shape",
        "click",
        "roll",
        "key:lock",
        "nudge:place",
        "click",
    ],
    # Where the model's grid origin (its min corner) sits relative to the anchor, in metres.
    # Default: centred left/right, starting 2 m beyond the anchor, on the anchor's level.
    # Keep the model out of the line of sight between camera and anchor, otherwise the
    # crosshair will start hitting already-built beams and the anchor moves.
    "model_offset": "auto",
    "auto_offset_gap": 2,
    # timings (seconds)
    "key_delay": 0.05,  # hold time of one key press
    "key_interval": 0.05,  # pause between key presses
    "click_delay": 0.25,  # pause after a click (game needs time to register)
    "beam_delay": 0.3,  # pause after each beam
    "max_beam_length": 40,  # longest beam the game allows (m)
    "max_nudge": 0,  # 0 = no limit; otherwise warn when one axis needs more nudges
    # beam roll with the mouse wheel while spanning: 6 notches = 90°
    "roll": True,
    "roll_step_deg": 15,
    "roll_direction": 1,  # -1 if the game turns the other way than the preview
}


def load_config(path: str | Path | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        user = json.loads(Path(path).read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def apply_speed(cfg: dict[str, Any], kps: float | None) -> dict[str, Any]:
    """Return a copy of cfg whose key timing gives `kps` keystrokes per second."""
    if not kps:
        return cfg
    per_press = 1.0 / max(float(kps), 0.1)
    return {**cfg, "key_delay": per_press / 2, "key_interval": per_press / 2}


def keystrokes_per_second(cfg: dict[str, Any]) -> float:
    return 1.0 / (float(cfg["key_delay"]) + float(cfg["key_interval"]))


@dataclass
class Action:
    kind: str  # "key", "click", "scroll", "wait"
    key: str = ""
    times: int = 1
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "key":
            return {"key": self.key, "times": self.times}
        if self.kind == "click":
            return {"click": "left"}
        if self.kind == "scroll":
            return {"scroll": self.times}
        return {"wait": self.seconds}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Action":
        if "key" in d:
            return Action("key", key=d["key"], times=int(d.get("times", 1)))
        if "click" in d:
            return Action("click")
        if "scroll" in d:
            return Action("scroll", times=int(d["scroll"]))
        return Action("wait", seconds=float(d["wait"]))


@dataclass
class BeamStep:
    index: int
    beam: Beam
    actions: list[Action] = field(default_factory=list)
    roll: int = 0  # wheel notches (signed)

    @property
    def key_presses(self) -> int:
        return sum(abs(a.times) for a in self.actions if a.kind in ("key", "click", "scroll"))


def compute_offset(beams: list[Beam], cfg: dict[str, Any]) -> Vec:
    if cfg["model_offset"] != "auto":
        return tuple(int(v) for v in cfg["model_offset"])  # type: ignore[return-value]
    xs = [c for b in beams for c in (b.start[0], b.end[0])]
    width = max(xs) - min(xs)
    return (-(width // 2), int(cfg["auto_offset_gap"]), 0)


def nudge_actions(vec: Vec, cfg: dict[str, Any]) -> list[Action]:
    keys = cfg["keys"]
    out = []
    for axis, amount in zip("xyz", vec):
        if amount:
            key = keys[("+" if amount > 0 else "-") + axis]
            out.append(Action("key", key=key, times=abs(amount)))
    return out


def build_plan(
    beams: list[Beam], cfg: dict[str, Any], rolls: list[int] | None = None
) -> tuple[list[BeamStep], Vec, list[str]]:
    """Return (steps, applied model offset, warnings). `rolls`: wheel notches per beam."""
    offset = compute_offset(beams, cfg)
    warnings: list[str] = []
    steps: list[BeamStep] = []
    max_nudge = int(cfg.get("max_nudge") or 0)

    for i, raw in enumerate(beams):
        beam = raw.translated(offset)
        # the end that is placed last gets nudged from the anchor: use the nearer end
        # (the same beam drawn the other way round; roll is symmetric for a square beam)
        if sum(map(abs, beam.start)) < sum(map(abs, beam.end)):
            beam = Beam(beam.end, beam.start, beam.kind)
        shape = tuple(s - e for s, e in zip(beam.start, beam.end))
        place = beam.end
        roll = (rolls[i] if rolls else 0) if cfg.get("roll", True) else 0
        step = BeamStep(i, beam, roll=roll)
        for token in cfg["beam_macro"]:
            kind, _, arg = token.partition(":")
            if kind == "key":
                step.actions.append(Action("key", key=cfg["keys"].get(arg, arg)))
            elif kind == "nudge":
                vec = shape if arg == "shape" else place
                step.actions.extend(nudge_actions(vec, cfg))  # type: ignore[arg-type]
                if max_nudge and any(abs(v) > max_nudge for v in vec):
                    warnings.append(f"beam {i}: nudge {vec} exceeds max_nudge={max_nudge}")
            elif kind == "click":
                step.actions.append(Action("click"))
                step.actions.append(Action("wait", seconds=float(cfg["click_delay"])))
            elif kind == "roll":
                if roll:
                    step.actions.append(Action("scroll", times=roll * int(cfg.get("roll_direction", 1))))
            elif kind == "wait":
                step.actions.append(Action("wait", seconds=float(arg)))
            else:
                raise ValueError(f"Unknown macro token: {token}")
        step.actions.append(Action("wait", seconds=float(cfg["beam_delay"])))
        steps.append(step)
    return steps, offset, warnings


def plan_to_json(steps: list[BeamStep], offset: Vec, cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "model_offset": list(offset),
        "timing": {k: cfg[k] for k in ("key_delay", "key_interval")},
        "beams": [
            {
                "index": s.index,
                "start": list(s.beam.start),
                "end": list(s.beam.end),
                "length": round(s.beam.length, 3),
                "roll": s.roll,
                "actions": [a.to_dict() for a in s.actions],
            }
            for s in steps
        ],
    }


def plan_to_text(steps: list[BeamStep]) -> str:
    """Human readable key list, handy for placing beams by hand or debugging."""
    lines = []
    for s in steps:
        parts = []
        for a in s.actions:
            if a.kind == "key":
                parts.append(f"{a.key.upper()}x{a.times}" if a.times > 1 else a.key.upper())
            elif a.kind == "click":
                parts.append("CLICK")
            elif a.kind == "scroll":
                parts.append(f"WHEEL{'+' if a.times > 0 else ''}{a.times}")
        lines.append(f"#{s.index:04d} {s.beam.start}->{s.beam.end} len={s.beam.length:.2f}:  " + " ".join(parts))
    return "\n".join(lines) + "\n"
