"""Run heavy numeric work on all CPU cores.

Python threads can't run Python code in parallel (the GIL), so the work is spread over a
pool of worker *processes*. One pool is created on first use and reused. Set the number of
workers with `set_workers()` (the CLI's --workers); 1 = everything in this process.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Iterable

_workers = max(os.cpu_count() or 1, 1)
_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()


def set_workers(n: int | None) -> None:
    global _workers, _pool
    with _lock:
        n = max(int(n or os.cpu_count() or 1), 1)
        if n != _workers and _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None
        _workers = n


def workers() -> int:
    return _workers


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=_workers)
        return _pool


def pmap(func: Callable[..., Any], arg_tuples: Iterable[tuple], serial: bool = False) -> list[Any]:
    """[func(*args) for args in arg_tuples], spread over the worker processes.

    `func` must be a top-level function (it is sent to the workers by name).
    """
    jobs = list(arg_tuples)
    if serial or _workers <= 1 or len(jobs) <= 1:
        return [func(*a) for a in jobs]
    try:
        pool = _get_pool()
        futures = [pool.submit(func, *a) for a in jobs]
        return [f.result() for f in futures]
    except (OSError, RuntimeError):  # no processes available here: just do it in-line
        return [func(*a) for a in jobs]


def chunks(n_items: int, min_size: int = 1) -> list[slice]:
    """Split range(n_items) into about 2 x workers contiguous slices (for load balance)."""
    parts = max(min(_workers * 2, n_items // max(min_size, 1)), 1)
    bounds = [round(i * n_items / parts) for i in range(parts + 1)]
    return [slice(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]
