"""Reproducible computation benchmark (DESIGN.md §3.9).

Usage::

    python tools/bench.py                      # all scenarios, 3 repeats, default workers
    python tools/bench.py --workers 1 --repeat 1 --scenario large_mlo
    python tools/bench.py --profile large_mlo  # cProfile top functions
    python tools/bench.py --save-ref ref.npz   # store full-precision results
    python tools/bench.py --check-ref ref.npz  # compare against stored results (max rel. error)

Scenarios
---------
* ``example``   — the bundled §8 example project (2 PWR nets, 400 points).
* ``large_mlo`` — one 80 mm wide plane, 8 decap rows (150 capacitors, dummy and normal rows,
  distances up to 45 mm), 400 points.
* ``many_nets`` — 6 PWR nets of 30–60 mm width with 3 decap rows each, 400 points.

Per-stage wall times are measured by wrapping the engine's stage functions (placement, cavity
static sums, cavity Z(f), decap MNA, reduction). With worker threads the stage times are summed over
threads, so they can exceed the total wall time. Every run uses cold caches unless ``--warm`` is
given (then a second run with a decap-only change is timed as well).
"""

from __future__ import annotations

import argparse
import cProfile
import dataclasses
import io
import os
import platform
import pstats
import sys
import threading
import time
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from simple_pi_calculator.core import cavity, decap_model, engine, pdn  # noqa: E402
from simple_pi_calculator.core.pdn import PwrSpec  # noqa: E402
from simple_pi_calculator.core.types import DecapRow  # noqa: E402
from simple_pi_calculator.io.project_io import load_project, to_inputs  # noqa: E402

MM = 1e-3
EXAMPLE = ROOT / "examples" / "example_project.spical.json"


# =============================================================================================
# Scenarios
# =============================================================================================
def _example_inputs():
    project, _ = load_project(EXAMPLE)
    return to_inputs(project, str(EXAMPLE))


def _row(pwr, model, count, dist_mm, dummy=False, mode=None):
    return DecapRow(pwr_name=pwr, model_file=model, count=count, distance_mm=dist_mm, dummy=dummy,
                    subckt=None, s2p_mode=mode, enabled=True)


def scenario_example():
    return dataclasses.replace(_example_inputs(), show_plane_only=True)


def scenario_large_mlo():
    base = _example_inputs()
    a, b, c = "cap_0402_100nF.mod", "cap_0603_10uF.mod", "cap_0402_100nF_series.s2p"
    rows = [_row("MLO", a, 24, 4.0), _row("MLO", a, 24, 8.0, dummy=True),
            _row("MLO", c, 20, 12.0), _row("MLO", a, 20, 18.0, dummy=True),
            _row("MLO", b, 16, 24.0), _row("MLO", c, 16, 30.0),
            _row("MLO", b, 16, 38.0, dummy=True), _row("MLO", a, 14, 45.0)]
    assert sum(r.count for r in rows) == 150
    return dataclasses.replace(base, pwrs=[PwrSpec("MLO", 5, 3, 80 * MM)], decap_rows=rows,
                               n_points=400, show_plane_only=True)


def scenario_many_nets():
    base = _example_inputs()
    a, b = "cap_0402_100nF.mod", "cap_0603_10uF.mod"
    pwrs, rows = [], []
    for i in range(6):
        name = f"NET{i + 1}"
        layers = (5, 3) if i % 2 == 0 else (7, 9)
        pwrs.append(PwrSpec(name, layers[0], layers[1], (30 + 6 * i) * MM))
        rows += [_row(name, a, 6 + i, 5.0 + i), _row(name, a, 4 + i, 10.0 + i, dummy=bool(i % 2)),
                 _row(name, b, 2 + i // 2, 16.0 + i)]
    return dataclasses.replace(base, pwrs=pwrs, decap_rows=rows, n_points=400,
                               show_plane_only=True)


SCENARIOS = {"example": scenario_example, "large_mlo": scenario_large_mlo,
             "many_nets": scenario_many_nets}


# =============================================================================================
# Stage timers
# =============================================================================================
class StageTimer:
    """Wraps engine stage functions and accumulates their wall time (thread-safe)."""

    TARGETS = [
        (pdn, "place_ports", "placement"),
        (cavity.CavityModel, "__init__", "cavity static sums"),
        (cavity.CavityModel, "z_matrix", "cavity Z(f)"),
        (decap_model.SpiceDecapModel, "impedance", "decap MNA"),
        (decap_model.S2pDecapModel, "impedance", "decap MNA"),
        (decap_model.DecapModelCache, "get", "model load"),
        (pdn, "_reduce", "reduction"),
    ]

    def __init__(self):
        self.times = defaultdict(float)
        self._lock = threading.Lock()
        self._orig = []
        self._local = threading.local()

    def _wrap(self, fn, label):
        timer = self

        def wrapper(*args, **kwargs):
            depth = getattr(timer._local, label, 0)
            setattr(timer._local, label, depth + 1)
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                setattr(timer._local, label, depth)
                if depth == 0:
                    with timer._lock:
                        timer.times[label] += time.perf_counter() - t0
        wrapper.__wrapped__ = fn
        return wrapper

    def __enter__(self):
        for owner, name, label in self.TARGETS:
            fn = owner.__dict__.get(name) if isinstance(owner, type) else getattr(owner, name, None)
            if fn is None:
                continue
            self._orig.append((owner, name, fn))
            setattr(owner, name, self._wrap(fn, label))
        return self

    def __exit__(self, *exc):
        for owner, name, fn in reversed(self._orig):
            setattr(owner, name, fn)
        self._orig.clear()


def _compute(inputs, workers, cache_state):
    kwargs = {}
    params = engine.compute_project.__code__.co_varnames
    if "workers" in params and workers is not None:
        kwargs["workers"] = workers
    if cache_state is not None:
        kwargs.update(cache_state)
    else:
        kwargs["cache"] = decap_model.DecapModelCache()
        if "cavity_cache" in params:
            kwargs["cavity_cache"] = None
    return engine.compute_project(inputs, **kwargs)


def run_scenario(name, workers, repeat, warm):
    inputs = SCENARIOS[name]()
    best = None
    for _ in range(repeat):
        with StageTimer() as st:
            t0 = time.perf_counter()
            results, issues = _compute(inputs, workers, None)
            wall = time.perf_counter() - t0
        errors = [i for i in issues if i.severity.name == "ERROR"]
        if errors:
            raise SystemExit(f"{name}: errors {[e.message for e in errors]}")
        if best is None or wall < best[0]:
            best = (wall, dict(st.times), results)
    out = {"wall": best[0], "stages": best[1], "results": best[2]}
    if warm and "cavity_cache" in engine.compute_project.__code__.co_varnames:
        from simple_pi_calculator.core.engine import CavityCache
        state = {"cache": decap_model.DecapModelCache(), "cavity_cache": CavityCache()}
        _compute(inputs, workers, state)
        rows = [dataclasses.replace(r, subckt=None) for r in inputs.decap_rows]
        for r in rows:  # decap-only change: swap the model files, same placement
            if r.model_file == "cap_0402_100nF.mod":
                r.model_file = "cap_0603_10uF.mod"
        changed = dataclasses.replace(inputs, decap_rows=rows)
        t0 = time.perf_counter()
        _compute(changed, workers, state)
        out["warm"] = time.perf_counter() - t0
    return out


def _single_net(args):
    inputs, name = args
    sub = dataclasses.replace(inputs, pwrs=[p for p in inputs.pwrs if p.name == name])
    results, _ = engine.compute_project(sub, workers=1, cavity_cache=None,
                                        cache=decap_model.DecapModelCache())
    return results[0].z_pad


def compare_executors(name, workers):
    """Threads (engine) vs a spawn ProcessPoolExecutor computing one PWR net per process."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    inputs = SCENARIOS[name]()
    jobs = [(inputs, p.name) for p in inputs.pwrs]
    t0 = time.perf_counter()
    engine.compute_project(inputs, workers=workers, cavity_cache=None,
                           cache=decap_model.DecapModelCache())
    t_thr = time.perf_counter() - t0
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as pool:
        t_start = time.perf_counter()
        list(pool.map(_single_net, jobs))  # includes interpreter + numpy import per process
        t_cold = time.perf_counter() - t0
        t1 = time.perf_counter()
        list(pool.map(_single_net, jobs))  # warm pool: pickling + compute only
        t_warm = time.perf_counter() - t1
    print(f"{name}: threads {t_thr:.3f} s | processes (spawn, {workers}) cold {t_cold:.3f} s "
          f"(pool creation {t_start - t0:.3f} s), warm pool {t_warm:.3f} s")


def describe_results(results):
    info = []
    for r in results:
        i = r.info
        info.append(f"{r.name}: P={i['P']} M={i['M']} N={i['N']} L={i['n_dynamic']}")
    return "; ".join(info)


def machine_info():
    lines = [f"python {platform.python_version()} on {platform.system()} {platform.machine()}",
             f"os.cpu_count() = {os.cpu_count()}", f"numpy {np.__version__}"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            np.show_config()
        text = buf.getvalue()
        keep = [ln.strip() for ln in text.splitlines()
                if any(k in ln.lower() for k in ("name:", "openblas configuration", "version:"))]
        lines.append("numpy config: " + " | ".join(keep[:8]))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"numpy config unavailable: {exc}")
    try:
        import threadpoolctl
        for pool in threadpoolctl.threadpool_info():
            lines.append(f"BLAS pool: {pool.get('internal_api')} {pool.get('version')} "
                         f"threads={pool.get('num_threads')}")
    except ImportError:
        lines.append("threadpoolctl not installed")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--scenario", action="append", choices=list(SCENARIOS))
    ap.add_argument("--workers", type=int, default=None, help="0 = auto (default engine setting)")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--warm", action="store_true", help="also time a cached decap-only re-run")
    ap.add_argument("--profile", choices=list(SCENARIOS))
    ap.add_argument("--compare-executors", action="store_true",
                    help="threads vs spawn processes for the selected scenarios")
    ap.add_argument("--save-ref")
    ap.add_argument("--check-ref")
    args = ap.parse_args(argv)
    names = args.scenario or list(SCENARIOS)

    for line in machine_info():
        print(line)
    if args.profile:
        inputs = SCENARIOS[args.profile]()
        prof = cProfile.Profile()
        prof.enable()
        _compute(inputs, args.workers, None)
        prof.disable()
        pstats.Stats(prof).sort_stats("cumulative").print_stats(25)
        return 0

    if args.compare_executors:
        for name in names:
            compare_executors(name, engine.resolve_workers(args.workers)
                              if hasattr(engine, "resolve_workers") else 2)
        return 0

    ref = dict(np.load(args.check_ref)) if args.check_ref else None
    store = {}
    print(f"\n{'scenario':<11} {'wall [s]':>9}  stages [s]")
    for name in names:
        res = run_scenario(name, args.workers, args.repeat, args.warm)
        stages = ", ".join(f"{k} {v:.3f}" for k, v in sorted(res["stages"].items()))
        warm = f", warm re-run {res['warm']:.3f}" if "warm" in res else ""
        print(f"{name:<11} {res['wall']:9.3f}  {stages}{warm}")
        print(f"{'':<11} {'':>9}  {describe_results(res['results'])}")
        for r in res["results"]:
            key = f"{name}/{r.name}"
            store[key + "/z_pad"] = r.z_pad
            store[key + "/z_plane"] = r.z_plane_only
            store[key + "/marker_z"] = r.marker_z
        if ref is not None:
            worst = 0.0
            for k, v in store.items():
                if k.startswith(name + "/") and k in ref:
                    worst = max(worst, float(np.max(np.abs(v - ref[k]) / np.abs(ref[k]))))
            print(f"{'':<11} {'':>9}  max rel. deviation vs reference: {worst:.3e}")
    if args.save_ref:
        np.savez(args.save_ref, **store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
