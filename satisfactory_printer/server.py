"""Live preview: a tiny local web server around the build pipeline.

  python -m satisfactory_printer serve model.stl

opens the preview in the browser with sliders (size, mode, tolerance, spacing) and a typing
speed box. Each change rebuilds the beams here and the page re-renders them; "Save plan"
writes plan.json etc. for the current settings, and "Start" types the plan into the game
while the page shows which beams are already placed. Only listens on 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .mesh import load_mesh
from .plan import keystrokes_per_second, load_config, plan_to_json

LIVE_PARAMS = ("size", "mode", "tolerance", "spacing")  # change the beams -> cache key
RUN_PARAMS = ("kps", "start_at", "countdown", "simulate")  # only change how it's typed


class LiveSession:
    """Holds the loaded model and a small cache of recent builds."""

    def __init__(self, args: argparse.Namespace):
        from . import cli

        self.cli = cli
        self.base = args
        self.src = Path(args.input)
        self.raw = load_mesh(self.src)
        self.out = Path(args.output)
        self.lock = threading.Lock()
        self.cache: OrderedDict[tuple, tuple] = OrderedDict()
        self.runner = None
        self.run_params: dict | None = None

    def params(self, query: dict[str, list[str]] | None = None) -> dict:
        p = {
            "size": float(self.base.size or 20),
            "mode": self.base.mode,
            "tolerance": float(self.base.tolerance),
            "spacing": float(self.base.spacing),
            "kps": float(self.base.kps or keystrokes_per_second(load_config(self.base.config))),
            "start_at": 0,
            "countdown": 5,
            "simulate": False,
        }
        for k, v in (query or {}).items():
            if not v or k not in LIVE_PARAMS + RUN_PARAMS:
                continue
            if k == "mode":
                p[k] = v[0]
            elif k == "simulate":
                p[k] = v[0].lower() in ("1", "true", "on", "yes")
            elif k in ("start_at", "countdown"):
                p[k] = max(int(float(v[0])), 0)
            else:
                p[k] = float(v[0])
        p["kps"] = min(max(p["kps"], 0.5), 100.0)
        if p["mode"] not in self.cli.MODES:
            raise ValueError(f"unknown mode {p['mode']!r}")
        p["size"] = min(max(p["size"], 1.0), 500.0)
        p["tolerance"] = min(max(p["tolerance"], 0.05), 10.0)
        p["spacing"] = min(max(p["spacing"], 1.0), 20.0)
        return p

    def _args(self, p: dict) -> argparse.Namespace:
        args = argparse.Namespace(**vars(self.base))
        args.size, args.scale = p["size"], None
        args.mode = p["mode"]
        args.tolerance = p["tolerance"]
        # one "spacing" slider drives both flat-area and contour spacing
        args.spacing = args.ring_spacing = p["spacing"]
        return args

    def build(self, p: dict):
        key = tuple(p[k] for k in LIVE_PARAMS)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            res = self.cli.run_build(self.raw, self._args(p), self.src.name)
            entry = (res, self.cli.summary_stats(res), {})
            self.cache[key] = entry
            while len(self.cache) > 12:
                self.cache.popitem(last=False)
            return entry

    def preview(self, p: dict) -> dict:
        res, stats, metrics = self.build(p)
        return self.cli.preview_data(res, {**stats, **metrics})

    def metrics(self, p: dict) -> dict:
        res, _, metrics = self.build(p)
        if not metrics and res.beams:
            metrics.update(self.cli.metric_stats(res))  # cached in place
        return metrics

    def save(self, p: dict) -> dict:
        res, _, _ = self.build(p)
        if not res.beams:
            return {"error": "no beams to save"}
        res = self.cli.with_speed(res, p["kps"])
        metrics = self.metrics(p)
        folder = self.out / f"{self.src.stem}_{p['mode']}_{p['size']:g}m"
        self.cli.write_outputs(res, folder, {**self.cli.summary_stats(res), **metrics})
        return {"folder": str(folder), "plan": str(folder / "plan.json")}

    # ---- typing the plan into the game
    def _busy(self) -> bool:
        return self.runner is not None and self.runner.status in ("ready", "countdown", "running", "paused")

    def run_start(self, p: dict) -> dict:
        from .executor import PlanRunner

        if self._busy():
            return {"error": "a build is already running"}
        saved = self.save(p)  # keep plan.json on disk so `run --start-at` can resume later
        if "error" in saved:
            return saved
        res, _, _ = self.build(p)
        res = self.cli.with_speed(res, p["kps"])
        plan = plan_to_json(res.steps, res.offset, res.cfg)
        self.runner = PlanRunner(plan, p["start_at"], p["countdown"], simulate=p["simulate"])
        self.run_params = {k: p[k] for k in LIVE_PARAMS + RUN_PARAMS}
        self.runner.start_thread()
        return self.run_status()

    def run_status(self) -> dict:
        if self.runner is None:
            return {"status": "idle"}
        return {**self.runner.state(), "params": self.run_params}

    def run_control(self, action: str) -> dict:
        if self.runner is None:
            return {"status": "idle"}
        {"pause": self.runner.pause, "resume": self.runner.resume, "stop": self.runner.stop,
         "abort": self.runner.abort}[action]()
        return self.run_status()


def make_handler(session: LiveSession):
    from .preview_html import render_page

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

        def _handle(self, method: str) -> None:
            url = urlparse(self.path)
            try:
                p = session.params(parse_qs(url.query))
                if method == "GET" and url.path in ("/", "/index.html"):
                    if session._busy() and session.run_params:
                        p = {**p, **session.run_params}  # show the beams that are being built
                    live = {"params": p, "modes": session.cli.MODES, "run": session.run_status()}
                    self._send(200, render_page(session.preview(p), live).encode(), "text/html; charset=utf-8")
                elif method == "GET" and url.path == "/api/build":
                    self._json(session.preview(p))
                elif method == "GET" and url.path == "/api/metrics":
                    self._json(session.metrics(p))
                elif method == "POST" and url.path == "/api/save":
                    self._json(session.save(p))
                elif method == "POST" and url.path == "/api/run/start":
                    self._json(session.run_start(p))
                elif method == "GET" and url.path == "/api/run/status":
                    self._json(session.run_status())
                elif method == "POST" and url.path in ("/api/run/pause", "/api/run/resume", "/api/run/stop", "/api/run/abort"):
                    self._json(session.run_control(url.path.rsplit("/", 1)[1]))
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # report to the page instead of dropping the connection
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

    return Handler


def serve(args: argparse.Namespace) -> int:
    session = LiveSession(args)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(session))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Live preview for {session.src.name}: {url}")
    print(f"'Save plan' writes to {session.out}/<model>_<mode>_<size>m/.  Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
