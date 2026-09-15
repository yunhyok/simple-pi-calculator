"""DESIGN.md §3.9 — performance architecture: fast paths are equivalent to the reference
implementation, results are independent of the worker count, cancel works with the thread pools and
the caches hit/miss as documented."""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core import cavity as cavity_mod
from simple_pi_calculator.core.cavity import (CavityCache, CavityModel, cavity_cache_key,
                                              z_matrix_reference)
from simple_pi_calculator.core.decap_model import DecapModelCache, evaluate_impedance
from simple_pi_calculator.core.engine import CancelledError, compute_project
from simple_pi_calculator.core.parallel import plan_chunks, resolve_workers, run_chunks
from simple_pi_calculator.core.pdn import PwrSpec, _reduce
from simple_pi_calculator.core.stackup import Layer, PlanePair
from simple_pi_calculator.core.types import DecapRow
from simple_pi_calculator.errors import IssueCollector
from simple_pi_calculator.io.project_io import load_project, project_to_dict, to_inputs

MM = 1e-3
ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "example_project.spical.json"


def make_pair(er=4.3, tand=0.018, sigma=5.8e7, t=35e-6, d=0.1 * MM):
    pwr = Layer(5, "PWR", t, sigma, None, None)
    gnd = Layer(3, "GND", t, sigma, None, None)
    return PlanePair(pwr, gnd, d, er, tand, d)


@pytest.fixture(scope="module")
def example_inputs():
    project, _ = load_project(EXAMPLE)
    return dataclasses.replace(to_inputs(project, str(EXAMPLE)), show_plane_only=True)


def many_nets(inputs, n=4):
    a, b = "cap_0402_100nF.mod", "cap_0603_10uF.mod"
    pwrs, rows = [], []
    for i in range(n):
        name = f"NET{i + 1}"
        layers = (5, 3) if i % 2 == 0 else (7, 9)
        pwrs.append(PwrSpec(name, layers[0], layers[1], (30 + 6 * i) * MM))
        rows += [DecapRow(name, a, 6 + i, 5.0 + i), DecapRow(name, a, 4 + i, 10.0 + i, bool(i % 2)),
                 DecapRow(name, b, 2, 16.0 + i)]
    return dataclasses.replace(inputs, pwrs=pwrs, decap_rows=rows)


# (a) equivalence with the reference implementation ------------------------------------------
@pytest.mark.parametrize("seed", range(6))
def test_z_matrix_matches_reference_random(seed):
    rng = np.random.default_rng(seed)
    a = rng.uniform(8, 30) * MM
    b = rng.uniform(8, 30) * MM
    P = int(rng.integers(1, 9))
    if seed % 2:  # ports on shared rows (grouped static sums)
        ys = rng.uniform(0.5, b / MM - 0.5, size=3) * MM
        xy = np.column_stack([rng.uniform(0.5, a / MM - 0.5, size=P) * MM,
                              ys[rng.integers(0, 3, size=P)]])
    else:  # fully random ports (no grouping possible)
        xy = np.column_stack([rng.uniform(0.5, a / MM - 0.5, size=P) * MM,
                              rng.uniform(0.5, b / MM - 0.5, size=P) * MM])
    widths = rng.choice([0.22369, 0.5, 1.2], size=P) * MM
    widths[1:] = widths[-1]
    pair = make_pair(tand=float(rng.uniform(0, 0.03)))
    f = np.geomspace(1e4, float(rng.choice([1e9, 5e9])), 37)
    cav = CavityModel(a, b, pair, xy, widths, f)
    ref = z_matrix_reference(cav, f)
    for workers in (1, 3):
        z = cav.z_matrix(f, workers=workers)
        assert np.max(np.abs(z - ref) / np.abs(ref)) <= 1e-10


def test_z_matrix_without_pair_table_matches_reference(monkeypatch):
    pair = make_pair()
    f = np.geomspace(1e5, 3e9, 21)
    xy = [[5 * MM, 4 * MM], [12 * MM, 15 * MM], [17 * MM, 15 * MM]]
    cav = CavityModel(20 * MM, 20 * MM, pair, xy, [0.3 * MM] * 3, f)
    monkeypatch.setattr(cavity_mod, "_DYN_T_MAX_BYTES", 0)
    cav._T = None
    z = cav.z_matrix(f, workers=2)
    ref = z_matrix_reference(cav, f)
    assert np.max(np.abs(z - ref) / np.abs(ref)) <= 1e-10


def test_reduce_parallel_identical_to_serial():
    rng = np.random.default_rng(3)
    F, P = 97, 12
    z = rng.normal(size=(F, P, P)) + 1j * rng.normal(size=(F, P, P))
    z = z + np.swapaxes(z, 1, 2)
    zl = rng.normal(size=(F, P - 1)) + 5.0
    z1, r1 = _reduce(z, zl, workers=1)
    z4, r4 = _reduce(z, zl, workers=4)
    assert np.array_equal(z1, z4)
    assert np.allclose(r1, r4, rtol=1e-12, atol=0)


# (b) worker-count invariance ------------------------------------------------------------------
def test_thread_count_invariance(example_inputs):
    inputs = many_nets(example_inputs, 3)
    runs = {}
    for w in (1, 4):
        res, issues = compute_project(inputs, workers=w, cavity_cache=None,
                                      cache=DecapModelCache())
        assert not [i for i in issues if i.severity.name == "ERROR"]
        runs[w] = (res, [(i.code, i.source, i.message) for i in issues])
    (r1, i1), (r4, i4) = runs[1], runs[4]
    assert [r.name for r in r1] == [r.name for r in r4] == ["NET1", "NET2", "NET3"]
    assert i1 == i4
    for a, b in zip(r1, r4):
        assert np.max(np.abs(a.z_pad - b.z_pad) / np.abs(a.z_pad)) <= 1e-12
        assert np.max(np.abs(a.marker_z - b.marker_z) / np.abs(a.marker_z)) <= 1e-12
        assert a.info["min_rcond"] == pytest.approx(b.info["min_rcond"], rel=1e-9)


def test_workers_setting_resolution_and_roundtrip(example_inputs):
    assert resolve_workers(0) >= 1 and resolve_workers(None) == resolve_workers(0)
    assert resolve_workers(3) == 3
    project, _ = load_project(EXAMPLE)
    assert project.advanced.workers == 0
    project.advanced.workers = 3
    doc = project_to_dict(project, str(EXAMPLE.parent), None)
    assert doc["advanced"]["workers"] == 3
    assert to_inputs(project, str(EXAMPLE)).workers == 3


def test_plan_chunks_cover_range():
    for n in (1, 7, 403):
        for w in (1, 2, 8):
            chunks = plan_chunks(n, 5e5, w)
            assert chunks[0].start == 0 and chunks[-1].stop == n
            assert all(c.stop == d.start for c, d in zip(chunks, chunks[1:]))


# (c) cancellation with the pools --------------------------------------------------------------
def test_cancel_in_the_middle_with_pool(example_inputs):
    inputs = many_nets(example_inputs, 4)
    lock = threading.Lock()
    calls = {"n": 0}
    progress = []

    def cancel():
        with lock:
            calls["n"] += 1
            return calls["n"] > 12

    with pytest.raises(CancelledError):
        compute_project(inputs, workers=4, cavity_cache=None, cancel=cancel,
                        progress=lambda x, m: progress.append(x))
    assert calls["n"] > 12
    assert not progress or max(progress) < 1.0
    names = [t.name for t in threading.enumerate()]
    assert not any(n.startswith(("spical-pwr", "spical-chunk")) for n in names)


def test_run_chunks_cancel_and_error():
    started = []

    def fn(sl):
        started.append(sl.start)
        return sl.start

    with pytest.raises(CancelledError):
        run_chunks(fn, [slice(i, i + 1) for i in range(20)], 4, cancel=lambda: len(started) >= 3,
                   cancelled_exc=CancelledError)
    assert len(started) < 20

    def boom(sl):
        if sl.start == 5:
            raise ValueError("boom")
        return sl.start

    with pytest.raises(ValueError):
        run_chunks(boom, [slice(i, i + 1) for i in range(10)], 3)
    assert run_chunks(fn, [slice(i, i + 1) for i in range(6)], 3) == list(range(6))


# (d) caches -----------------------------------------------------------------------------------
def test_cavity_cache_hit_miss(example_inputs):
    cache = CavityCache()
    models = DecapModelCache()
    r1, _ = compute_project(example_inputs, cavity_cache=cache, cache=models)
    assert (cache.misses, cache.hits, len(cache)) == (2, 0, 2)
    r2, _ = compute_project(example_inputs, cavity_cache=cache, cache=models)
    assert (cache.misses, cache.hits) == (2, 2)
    for a, b in zip(r1, r2):
        assert np.array_equal(a.z_pad, b.z_pad)
        assert a.info == b.info

    # decap-only change and via-model/mounting change: same placement → cache hit
    rows = [dataclasses.replace(r) for r in example_inputs.decap_rows]
    for r in rows:
        r.model_file = "cap_0603_10uF.mod"
    changed = dataclasses.replace(example_inputs, decap_rows=rows,
                                  vias=dataclasses.replace(example_inputs.vias, model="coax",
                                                           mounting_inductance_h=0.2e-9))
    r3, _ = compute_project(changed, cavity_cache=cache, cache=models)
    assert cache.hits == 4 and cache.misses == 2
    r3_cold, _ = compute_project(changed, cavity_cache=None, cache=DecapModelCache())
    for a, b in zip(r3, r3_cold):
        assert np.max(np.abs(a.z_pad - b.z_pad) / np.abs(b.z_pad)) <= 1e-12

    # geometry changes: count (placement), drill (port width), sweep → miss
    rows = [dataclasses.replace(r) for r in example_inputs.decap_rows]
    rows[0].count += 1
    compute_project(dataclasses.replace(example_inputs, decap_rows=rows), cavity_cache=cache,
                    cache=models)
    assert cache.misses == 3  # only VDD_CORE changed
    compute_project(dataclasses.replace(example_inputs, vias=dataclasses.replace(
        example_inputs.vias, drill_diameter_m=0.25 * MM)), cavity_cache=cache, cache=models)
    assert cache.misses == 5
    compute_project(dataclasses.replace(example_inputs, n_points=401), cavity_cache=cache,
                    cache=models)
    assert cache.misses == 7


def test_cavity_cache_bounds_and_key():
    pair = make_pair()
    f = np.geomspace(1e5, 1e9, 5)
    xy = np.array([[5 * MM, 5 * MM]])
    k1 = cavity_cache_key(20 * MM, 20 * MM, pair, xy, [0.3 * MM], f, cavity_mod.ModeSettings())
    k2 = cavity_cache_key(20 * MM, 20 * MM, pair, xy + 1e-12, [0.3 * MM], f,
                          cavity_mod.ModeSettings())
    k3 = cavity_cache_key(20 * MM, 20 * MM, dataclasses.replace(pair, tand_eff=0.02), xy,
                          [0.3 * MM], f, cavity_mod.ModeSettings())
    assert len({k1, k2, k3}) == 3
    cache = CavityCache(max_entries=2, max_bytes=10_000)
    z = np.zeros((4, 5, 5), dtype=complex)  # 1600 B
    for i in range(3):
        cache.put(str(i), z.copy(), {})
    assert len(cache) == 2 and cache.get("0") is None and cache.get("2") is not None
    cache.put("big", np.zeros((100, 5, 5), dtype=complex), {})  # 40 kB > max_bytes
    assert cache.get("big") is None
    stored, _ = cache.get("2")
    with pytest.raises(ValueError):
        stored[0, 0, 0] = 1.0  # read-only
    disabled = CavityCache(max_entries=0)
    disabled.put("x", z.copy(), {})
    assert len(disabled) == 0


def test_decap_impedance_memo_replays_warnings(tmp_path):
    src = ROOT / "examples" / "cap_0402_100nF_series.s2p"
    path = tmp_path / "c.s2p"
    path.write_bytes(src.read_bytes())
    models = DecapModelCache()
    model = models.get(str(path), None, "series", IssueCollector())
    f = np.geomspace(1e2, 1e11, 30)  # outside the file range → extrapolation warnings
    i1, i2 = IssueCollector(), IssueCollector()
    z1 = evaluate_impedance(model, f, i1)
    calls = {"n": 0}
    original = type(model).impedance

    def counting(self, *args, **kwargs):
        calls["n"] += 1
        return original(self, *args, **kwargs)

    type(model).impedance = counting
    try:
        z2 = evaluate_impedance(model, f, i2)
        assert calls["n"] == 0
        evaluate_impedance(model, f[:-1], IssueCollector())
        assert calls["n"] == 1
    finally:
        type(model).impedance = original
    assert z1 is z2
    assert [i.code for i in i1.issues] == [i.code for i in i2.issues]
    assert any(i.code.startswith("W_S2P_EXTRAP") for i in i2.issues)
