"""Play a keystroke plan into the game.

Run this on the Windows PC where Satisfactory runs. It uses `pydirectinput` (DirectInput scan
codes, which games actually register); falls back to `pynput` if that's not installed.

Hotkeys while running (needs `pynput`):  F8 = pause / resume,  F9 = abort.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .plan import Action


class _Control:
    def __init__(self) -> None:
        self.paused = threading.Event()
        self.aborted = threading.Event()
        self._listener = None
        try:
            from pynput import keyboard  # type: ignore

            def on_press(key: Any) -> None:
                if key == keyboard.Key.f8:
                    if self.paused.is_set():
                        self.paused.clear()
                        print("\n[resumed]")
                    else:
                        self.paused.set()
                        print("\n[paused - press F8 to resume, F9 to abort]")
                elif key == keyboard.Key.f9:
                    self.aborted.set()
                    print("\n[abort requested]")

            self._listener = keyboard.Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
        except Exception:
            print("(pynput not available: F8/F9 hotkeys disabled, use Ctrl+C in this window to stop)")

    def wait_if_paused(self) -> None:
        while self.paused.is_set() and not self.aborted.is_set():
            time.sleep(0.05)


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


def run_plan(plan_path: str | Path, start_at: int = 0, countdown: int = 5, dry_run: bool = False) -> None:
    plan = json.loads(Path(plan_path).read_text())
    key_delay = float(plan["timing"]["key_delay"])
    key_interval = float(plan["timing"]["key_interval"])
    beams = [b for b in plan["beams"] if b["index"] >= start_at]
    total = len(plan["beams"])

    backend = None if dry_run else _Backend(key_delay)
    control = None if dry_run else _Control()
    if backend:
        print(f"Input backend: {backend.name}")
    print(f"{len(beams)} beams to place (starting at #{start_at} of {total}).")
    if not dry_run:
        print(f"Switch to Satisfactory now: beam selected, crosshair on the anchor. Starting in {countdown}s...")
        for s in range(countdown, 0, -1):
            print(f"  {s}...")
            time.sleep(1)

    started = time.time()
    for b in beams:
        print(f"beam #{b['index']}/{total - 1}  {b['start']} -> {b['end']}", flush=True)
        for raw in b["actions"]:
            a = Action.from_dict(raw)
            if dry_run:
                continue
            assert backend and control
            control.wait_if_paused()
            if control.aborted.is_set():
                print(f"Aborted. Resume later with:  --start-at {b['index']}")
                return
            if a.kind == "key":
                for _ in range(a.times):
                    backend.press(a.key)
                    time.sleep(key_interval)
            elif a.kind == "click":
                backend.click()
            else:
                time.sleep(a.seconds)
    if not dry_run:
        print(f"Done in {time.time() - started:.0f}s.")
