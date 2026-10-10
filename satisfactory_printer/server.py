"""The app: a small local web server (127.0.0.1 only) around the pipeline.

  python -m satisfactory_printer

Pick or upload a model, adjust size / pedestal / detail / finish and see the beams live.
Export blueprints (download as zip) or, as a fallback, type the beams into the game with
the keyboard while the page shows which beams are already placed.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import webbrowser
import zipfile
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import pipeline
from .pipeline import Settings

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MODEL_SUFFIXES = (".stl", ".obj")
MAX_UPLOAD = 300 * 1024 * 1024


def _num(q: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        return min(max(float(q[key][0]), lo), hi) if key in q and q[key] else default
    except ValueError:
        return default


class App:
    def __init__(self, models: Path, out: Path):
        self.models_dir = models
        self.out = out
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cache: OrderedDict[tuple, tuple] = OrderedDict()
        self.runner = None
        self.run_params: dict | None = None

    # ---- models
    def models(self) -> list[dict]:
        found = []
        for folder, label in ((self.models_dir, "Meine Modelle"), (EXAMPLES, "Beispiele")):
            if folder.is_dir():
                for f in sorted(folder.iterdir()):
                    if f.suffix.lower() in MODEL_SUFFIXES:
                        found.append({"id": f"{label}/{f.name}", "name": f.stem, "group": label})
        return found

    def model_path(self, model_id: str) -> Path:
        label, _, fname = model_id.partition("/")
        folder = {"Meine Modelle": self.models_dir, "Beispiele": EXAMPLES}.get(label)
        if folder is None or "/" in fname or "\\" in fname:
            raise ValueError("unknown model")
        p = folder / fname
        if not p.is_file():
            raise ValueError(f"model not found: {fname}")
        return p

    def upload(self, filename: str, data: bytes) -> dict:
        name = re.sub(r"[^A-Za-z0-9_.\- ]", "_", Path(filename).name).strip() or "model.stl"
        if Path(name).suffix.lower() not in MODEL_SUFFIXES:
            raise ValueError("nur .stl oder .obj")
        (self.models_dir / name).write_bytes(data)
        return {"id": f"Meine Modelle/{name}", "models": self.models()}

    # ---- settings from the query string
    def params(self, q: dict) -> tuple[str, Settings, dict]:
        models = self.models()
        model = q.get("model", [models[0]["id"] if models else ""])[0]
        s = Settings(
            size=_num(q, "size", 40, 3, 300),
            drop_base=_num(q, "drop_base", 0, 0, 0.9),
            tolerance=_num(q, "tolerance", 1.0, 0.1, 5),
            spacing=_num(q, "spacing", 1.0, 1, 5),
            finish=q.get("finish", [""])[0] if q.get("finish", [""])[0] in pipeline.FINISH_COLORS else "",
            output="keys" if q.get("output", ["blueprint"])[0] == "keys" else "blueprint",
            designer=q.get("designer", ["auto"])[0] or "auto",
            cost_per_m=_num(q, "cost_per_m", 0.25, 0, 100),
            kps=_num(q, "kps", 10, 0.5, 100),
        )
        run = {
            "start_at": int(_num(q, "start_at", 0, 0, 10**7)),
            "countdown": int(_num(q, "countdown", 5, 0, 120)),
            "simulate": q.get("simulate", ["0"])[0].lower() in ("1", "true", "on", "yes"),
            "name": re.sub(r"[^A-Za-z0-9_\-]", "_", q.get("name", [""])[0])[:60],
        }
        return model, s, run

    # ---- building (cached by model + shape settings)
    def build(self, model: str, s: Settings):
        key = (model, s.shape_key())
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                res, st, acc = self.cache[key]
                res.settings = s  # finish / designer / cost etc. don't change the beams
                return res, pipeline.stats(res), acc
            path = self.model_path(model)
            res = pipeline.build(path, s, path.stem)
            entry = (res, pipeline.stats(res), {})
            self.cache[key] = entry
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
            return entry

    def preview(self, model: str, s: Settings) -> dict:
        res, st, acc = self.build(model, s)
        return pipeline.preview_data(res, {**st, **acc})

    def accuracy(self, model: str, s: Settings) -> dict:
        res, _, acc = self.build(model, s)
        if not acc and res.beams:
            acc.update(pipeline.accuracy(res))
        return acc

    # ---- exports
    def export(self, model: str, s: Settings, run: dict) -> dict:
        res, _, _ = self.build(model, s)
        if not res.beams:
            return {"error": "keine Träger"}
        name = run["name"] or re.sub(r"[^A-Za-z0-9_\-]", "_", f"{res.name}_{s.size:g}m")
        folder = self.out / name
        if s.output == "blueprint":
            from .blueprint import check_node

            problem = check_node()
            if problem:
                return {"error": problem}
            info = pipeline.export_blueprints(res, folder, name)
        else:
            info = pipeline.export_keys(res, folder, s.kps)
        zpath = self.out / f"{name}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(folder.iterdir()):
                z.write(f, f.name)
        return {"folder": str(folder), "download": f"/api/download?file={quote(zpath.name)}",
                "guide": info.get("guide", "")}

    def download(self, fname: str) -> Path:
        p = (self.out / Path(fname).name).resolve()
        if p.parent != self.out.resolve() or p.suffix != ".zip" or not p.is_file():
            raise ValueError("not found")
        return p

    # ---- typing a keyboard plan into the game
    def busy(self) -> bool:
        return self.runner is not None and self.runner.status in ("ready", "countdown", "running", "paused")

    def run_start(self, model: str, s: Settings, run: dict, query: str) -> dict:
        from .executor import PlanRunner
        from .plan import apply_speed, plan_to_json

        if s.output != "keys":
            return {"error": "Tastatur-Ausgabe wählen"}
        if self.busy():
            return {"error": "es läuft schon ein Bau"}
        self.export(model, s, run)  # plan.json on disk, so `run --start-at` can resume later
        res, _, _ = self.build(model, s)
        plan = plan_to_json(res.steps, res.offset, apply_speed(res.cfg, s.kps))
        self.runner = PlanRunner(plan, run["start_at"], run["countdown"], simulate=run["simulate"])
        self.run_params = {"query": query}
        self.runner.start_thread()
        return self.run_status()

    def run_status(self) -> dict:
        if self.runner is None:
            return {"status": "idle"}
        return {**self.runner.state(), **(self.run_params or {})}

    def run_control(self, action: str) -> dict:
        if self.runner is not None:
            {"pause": self.runner.pause, "resume": self.runner.resume, "stop": self.runner.stop,
             "abort": self.runner.abort}[action]()
        return self.run_status()


def make_handler(app: App):
    from .preview_html import render_app

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

        def _handle(self, method: str) -> None:
            url = urlparse(self.path)
            q = parse_qs(url.query)
            try:
                if method == "GET" and url.path in ("/", "/index.html"):
                    self._send(200, render_app({"models": app.models(), "run": app.run_status()}).encode(),
                               "text/html; charset=utf-8")
                elif method == "GET" and url.path == "/api/models":
                    self._json({"models": app.models()})
                elif method == "POST" and url.path == "/api/upload":
                    size = int(self.headers.get("Content-Length", 0))
                    if size <= 0 or size > MAX_UPLOAD:
                        raise ValueError("Datei leer oder zu groß")
                    self._json(app.upload(q.get("name", ["model.stl"])[0], self.rfile.read(size)))
                elif method == "GET" and url.path == "/api/build":
                    model, s, _ = app.params(q)
                    self._json(app.preview(model, s))
                elif method == "GET" and url.path == "/api/accuracy":
                    model, s, _ = app.params(q)
                    self._json(app.accuracy(model, s))
                elif method == "POST" and url.path == "/api/export":
                    model, s, run = app.params(q)
                    self._json(app.export(model, s, run))
                elif method == "GET" and url.path == "/api/download":
                    p = app.download(q.get("file", [""])[0])
                    self._send(200, p.read_bytes(), "application/zip",
                               {"Content-Disposition": f'attachment; filename="{p.name}"'})
                elif method == "POST" and url.path == "/api/run/start":
                    model, s, run = app.params(q)
                    self._json(app.run_start(model, s, run, url.query))
                elif method == "GET" and url.path == "/api/run/status":
                    self._json(app.run_status())
                elif method == "POST" and url.path in ("/api/run/pause", "/api/run/resume", "/api/run/stop"):
                    self._json(app.run_control(url.path.rsplit("/", 1)[1]))
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
    app = App(Path(args.models), Path(args.output))
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Satisfactory Printer läuft: {url}   (Strg+C beendet)")
    print(f"Modelle: {app.models_dir}/   Exporte: {app.out}/")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbeendet")
    finally:
        httpd.server_close()
    return 0
