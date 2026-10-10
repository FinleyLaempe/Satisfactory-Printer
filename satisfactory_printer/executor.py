"""Play a keystroke plan into the game.

Run this on the Windows PC where Satisfactory runs. It uses `pydirectinput` (DirectInput scan
codes, which games actually register); falls back to `pynput` if that's not installed.

Hotkeys while running (needs `pynput`):  F8 = pause / resume (between beams),  F9 = abort now.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .plan import Action


class _Control:
    """Global hotkeys: F8 = pause / resume (between beams), F9 = abort immediately."""

    def __init__(self, runner: "PlanRunner") -> None:
        try:
            from pynput import keyboard  # type: ignore

            def on_press(key: Any) -> None:
                if key == keyboard.Key.f8:
                    runner.toggle_pause()
                    print("\n[pause requested - stops after this beam]" if runner._pause.is_set() else "\n[resumed]")
                elif key == keyboard.Key.f9:
                    runner.abort()
                    print("\n[abort requested]")

            listener = keyboard.Listener(on_press=on_press)
            listener.daemon = True
            listener.start()
        except Exception:
            print("(pynput not available: F8/F9 hotkeys disabled, use Ctrl+C in this window to stop)")


class _Backend:
    def __init__(self, key_delay: float) -> None:
        self.key_delay = key_delay
        try:
            import pydirectinput  # type: ignore

            pydirectinput.PAUSE = 0
            pydirectinput.FAILSAFE = False
            self._pdi = pydirectinput
            self.name = "pydirectinput"
        except ImportError:
            self._pdi = None
            from pynput import keyboard, mouse  # type: ignore

            self._kb = keyboard.Controller()
            self._mouse = mouse.Controller()
            self._button = mouse.Button.left
            self._keymap = {
                "up": keyboard.Key.up,
                "down": keyboard.Key.down,
                "left": keyboard.Key.left,
                "right": keyboard.Key.right,
                "pageup": keyboard.Key.page_up,
                "pagedown": keyboard.Key.page_down,
                "space": keyboard.Key.space,
                "enter": keyboard.Key.enter,
                "esc": keyboard.Key.esc,
            }
            self.name = "pynput"

    def press(self, key: str) -> None:
        if self._pdi:
            self._pdi.keyDown(key)
            time.sleep(self.key_delay)
            self._pdi.keyUp(key)
        else:
            k = self._keymap.get(key, key)
            self._kb.press(k)
            time.sleep(self.key_delay)
            self._kb.release(k)

    def click(self) -> None:
        if self._pdi:
            self._pdi.mouseDown()
            time.sleep(self.key_delay)
            self._pdi.mouseUp()
        else:
            self._mouse.press(self._button)
            time.sleep(self.key_delay)
            self._mouse.release(self._button)

    def scroll(self, notches: int) -> None:
        """Mouse wheel, one notch at a time (+ = up). Rolls the beam while spanning."""
        step = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            if getattr(self._pdi, "scroll", None):  # pydirectinput forks that support it
                self._pdi.scroll(step)
            else:
                self._wheel(step)
            time.sleep(self.key_delay)

    def _wheel(self, step: int) -> None:
        try:
            from pynput import mouse  # type: ignore

            if not hasattr(self, "_wheel_mouse"):
                self._wheel_mouse = mouse.Controller()
            self._wheel_mouse.scroll(0, step)
        except ImportError:  # plain Windows API as a last resort
            import ctypes

            ctypes.windll.user32.mouse_event(0x0800, 0, 0, 120 * step, 0)  # MOUSEEVENTF_WHEEL


class _NullBackend:
    """Simulation: honours all timings but presses nothing."""

    name = "simulation (no keys pressed)"

    def __init__(self, key_delay: float) -> None:
        self.key_delay = key_delay

    def press(self, key: str) -> None:
        time.sleep(self.key_delay)

    def click(self) -> None:
        time.sleep(self.key_delay)

    def scroll(self, notches: int) -> None:
        time.sleep(self.key_delay * abs(notches))


def beam_duration(beam: dict, key_delay: float, key_interval: float) -> float:
    """Seconds one beam takes to type."""
    t = 0.0
    for raw in beam["actions"]:
        a = Action.from_dict(raw)
        if a.kind == "key":
            t += a.times * (key_delay + key_interval)
        elif a.kind == "click":
            t += key_delay
        elif a.kind == "scroll":
            t += abs(a.times) * (key_delay + key_interval)
        else:
            t += a.seconds
    return t


class PlanRunner:
    """Types a plan into the game in a controllable way (used by `run` and the live preview).

    pause() / stop() take effect between beams so no beam is left half-placed;
    abort() (F9) stops immediately.
    """

    def __init__(self, plan: dict, start_at: int = 0, countdown: int = 5, simulate: bool = False,
                 hotkeys: bool = True, log=print) -> None:
        self.plan = plan
        self.key_delay = float(plan["timing"]["key_delay"])
        self.key_interval = float(plan["timing"]["key_interval"])
        self.beams = plan["beams"]
        self.total = len(self.beams)
        self.start_at = max(0, min(start_at, self.total))
        self.countdown = countdown
        self.simulate = simulate
        self.log = log
        self.durations = [beam_duration(b, self.key_delay, self.key_interval) for b in self.beams]

        self.status = "ready"  # ready, countdown, running, paused, stopped, aborted, done, error
        self.index = self.start_at  # beams [0, index) are placed
        self.countdown_left = 0
        self.message = ""
        self.backend_name = ""
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._hotkeys = hotkeys

    # ---- control
    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def toggle_pause(self) -> None:
        self.resume() if self._pause.is_set() else self.pause()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def abort(self) -> None:
        self._abort.set()
        self._pause.clear()

    def state(self) -> dict:
        remaining = sum(self.durations[self.index:])
        return {
            "status": self.status,
            "placed": self.index,
            "total": self.total,
            "countdown": self.countdown_left,
            "eta_s": round(remaining, 1),
            "message": self.message,
            "backend": self.backend_name,
        }

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    # ---- main loop
    def run(self) -> None:
        try:
            backend = _NullBackend(self.key_delay) if self.simulate else _Backend(self.key_delay)
        except ImportError:
            self.status = "error"
            self.message = "No input library: pip install pydirectinput pynput (Windows)"
            self.log(self.message)
            return
        self.backend_name = backend.name
        if self._hotkeys and not self.simulate:
            _Control(self)
        self.log(f"Input backend: {backend.name}")
        self.log(f"{self.total - self.start_at} beams to place (starting at #{self.start_at} of {self.total}).")

        self.status = "countdown"
        self.log("Switch to Satisfactory now: beam selected, crosshair on the anchor.")
        for s in range(self.countdown, 0, -1):
            self.countdown_left = s
            self.log(f"  {s}...")
            for _ in range(10):
                if self._abort.is_set() or self._stop.is_set():
                    self.status = "stopped"
                    return
                time.sleep(0.1)
        self.countdown_left = 0

        started = time.time()
        for i in range(self.start_at, self.total):
            # beam boundary: honour pause / stop
            while self._pause.is_set() and not (self._stop.is_set() or self._abort.is_set()):
                self.status = "paused"
                time.sleep(0.05)
            if self._stop.is_set() or self._abort.is_set():
                self.status = "stopped" if self._stop.is_set() else "aborted"
                self.message = f"resume with start at beam #{i}"
                self.log(f"Stopped. Resume later with:  --start-at {i}")
                return
            self.status = "running"
            b = self.beams[i]
            self.log(f"beam #{i}/{self.total - 1}  {b['start']} -> {b['end']}")
            for raw in b["actions"]:
                if self._abort.is_set():
                    self.status = "aborted"
                    self.message = f"aborted during beam #{i} - cancel the hologram in-game, resume at #{i}"
                    self.log(f"Aborted. Resume later with:  --start-at {i}")
                    return
                a = Action.from_dict(raw)
                if a.kind == "key":
                    for _ in range(a.times):
                        backend.press(a.key)
                        time.sleep(self.key_interval)
                elif a.kind == "click":
                    backend.click()
                elif a.kind == "scroll":
                    backend.scroll(a.times)
                    time.sleep(self.key_interval)
                else:
                    time.sleep(a.seconds)
            self.index = i + 1
        self.status = "done"
        self.message = f"done in {time.time() - started:.0f}s"
        self.log(f"Done in {time.time() - started:.0f}s.")


def run_plan(plan_path: str | Path, start_at: int = 0, countdown: int = 5, dry_run: bool = False,
             simulate: bool = False) -> None:
    plan = json.loads(Path(plan_path).read_text())
    if dry_run:
        total = len(plan["beams"])
        for b in plan["beams"][start_at:]:
            print(f"beam #{b['index']}/{total - 1}  {b['start']} -> {b['end']}")
        return
    runner = PlanRunner(plan, start_at, countdown, simulate=simulate)
    try:
        runner.run()
    except KeyboardInterrupt:
        print(f"\nInterrupted. Resume later with:  --start-at {runner.index}")
