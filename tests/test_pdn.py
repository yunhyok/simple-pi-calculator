"""DESIGN.md §8.11 — port reduction and end-to-end PDN impedance."""

from __future__ import annotations

import dataclasses
import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core.engine import CancelledError, compute_project, validate_inputs
from simple_pi_calculator.core.pdn import (DecapGroup, PwrSpec, SingularReductionError,
                                           compute_pwr, evaluation_frequencies, reduce_ports)
from simple_pi_calculator.core.stackup import Layer, Stackup
from simple_pi_calculator.core.via import ViaSettings
from simple_pi_calculator.errors import IssueCollector
from simple_pi_calculator.io.project_io import load_project, to_inputs

MM = 1e-3
GOLDEN = Path(__file__).parent / "data" / "golden_example.json"


class FuncModel:
    """Synthetic decap model."""

    def __init__(self, fn, label="synthetic"):
        self.fn = fn
        self.label = label

    def impedance(self, f_hz, issues=None):
        return self.fn(np.asarray(f_hz, dtype=float))


def cap_model(c):
    return FuncModel(lambda f: 1 / (1j * 2 * math.pi * f * c), f"C={c:g}")


def rlc_model(r, l, c, scale=1.0):
    return FuncModel(lambda f: scale * (r + 1j * 2 * math.pi * f * l
                                        + 1 / (1j * 2 * math.pi * f * c)))


def simple_stackup(er=4.0, tand=0.0):
    rows = [(1, "TOP", 0.035, 5.8e7, None, None), (2, "PP", 0.1, None, er, tand),
            (3, "GND", 0.035, 5.8e7, None, None), (4, "CORE", 0.1, None, er, tand),
            (5, "PWR", 0.035, 5.8e7, None, None)]
    return Stackup(tuple(Layer(n, nm, t * MM, s, dk, df) for n, nm, t, s, dk, df in rows))


VIAS = ViaSettings(drill_diameter_m=0.2 * MM, antipad_diameter_m=0.5 * MM, via_pitch_m=1 * MM)


# 1 -------------------------------------------------------------------------------------------
def test_reduce_ports_two_port():
    rng = np.random.default_rng(1)
    F = 5
    z00, z01, z11, zl = (rng.normal(size=F) + 1j * rng.normal(size=F) for _ in range(4))
    z = np.empty((F, 2, 2), dtype=complex)
    z[:, 0, 0], z[:, 0, 1], z[:, 1, 0], z[:, 1, 1] = z00, z01, z01, z11
    assert reduce_ports(z, zl[:, None]) == pytest.approx(z00 - z01 ** 2 / (z11 + zl), rel=1e-12)
    assert reduce_ports(z, np.full((F, 1), 1e12 + 0j)) == pytest.approx(z00, rel=1e-9)
    assert reduce_ports(z[:, :1, :1], np.zeros((F, 0))) == pytest.approx(z00, rel=1e-15)


def test_reduce_ports_singular():
    z = np.ones((2, 3, 3), dtype=complex)
    with pytest.raises(SingularReductionError):
        reduce_ports(z, np.zeros((2, 2), dtype=complex))
    z = np.full((1, 2, 2), np.nan + 0j)
    with pytest.raises(SingularReductionError):
        reduce_ports(z, np.zeros((1, 1), dtype=complex))


def test_evaluation_frequencies():
    grid = np.geomspace(1e5, 1e9, 400)
    f_eval, gi, mf, mi = evaluation_frequencies(grid, [1e6, 1e7, 1e8, 1e10])
    assert f_eval.size == 403
    assert np.array_equal(f_eval[gi], grid)
    assert np.array_equal(f_eval[mi], [1e6, 1e7, 1e8]) and mf.tolist() == [1e6, 1e7, 1e8]
    f_eval, gi, mf, mi = evaluation_frequencies(np.array([1e5, 1e6, 1e7]), [1e6, 1e8])
    assert f_eval.size == 3 and mf.tolist() == [1e6]


# 2 -------------------------------------------------------------------------------------------
def test_single_decap_low_frequency():
    st = simple_stackup()
    c = 100e-9
    issues = IssueCollector()
    f = np.array([1e4, 1e5])
    res = compute_pwr(st, PwrSpec("T", 5, 3, 20 * MM), [DecapGroup("T", cap_model(c), 1, 5 * MM)],
                      VIAS, f, [], False, issues)
    assert res.placement.height_m == pytest.approx(7 * MM)
    assert res.info["C_plane"] == pytest.approx(49.58e-12, rel=1e-4)
    expect = 1 / (2 * math.pi * 1e4 * (c + res.info["C_plane"]))
    assert abs(res.z_pad[0]) == pytest.approx(expect, rel=1e-3)
    assert res.marker_f_hz.size == 0 and res.z_plane_only is None


# 3 -------------------------------------------------------------------------------------------
def test_dummy_equivalence():
    st = simple_stackup(tand=0.02)
    f = np.geomspace(1e5, 1e9, 60)
    pwr = PwrSpec("T", 5, 3, 20 * MM)
    rlc = (0.03, 0.45e-9, 100e-9)

    def run(groups):
        return compute_pwr(st, pwr, groups, VIAS, f, [1e6], True, IssueCollector()).z_pad

    z_dummy2 = run([DecapGroup("T", rlc_model(*rlc), 2, 5 * MM, dummy=True)])
    z_half = run([DecapGroup("T", rlc_model(*rlc, scale=0.5), 1, 5 * MM)])
    assert z_dummy2 == pytest.approx(z_half, rel=1e-9)
    model = rlc_model(*rlc)
    z_d1 = run([DecapGroup("T", model, 1, 5 * MM, dummy=True)])
    z_n1 = run([DecapGroup("T", model, 1, 5 * MM, dummy=False)])
    assert np.array_equal(z_d1, z_n1)


# 4 -------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def example_inputs():
    path = Path(__file__).resolve().parent.parent / "examples" / "example_project.spical.json"
    project, _ = load_project(path)
    inputs = to_inputs(project, str(path))
    return dataclasses.replace(inputs, show_plane_only=True)


@pytest.fixture(scope="module")
def example_results(example_inputs):
    t0 = time.perf_counter()
    results, issues = compute_project(example_inputs)
    elapsed = time.perf_counter() - t0
    return {r.name: r for r in results}, issues, elapsed


def _z_at(result, f):
    """|Z| at a grid end point or an exact marker."""
    if np.isclose(f, result.f_hz[0], rtol=1e-12):
        return abs(result.z_pad[0])
    if np.isclose(f, result.f_hz[-1], rtol=1e-12):
        return abs(result.z_pad[-1])
    i = int(np.nonzero(np.isclose(result.marker_f_hz, f, rtol=1e-12))[0][0])
    return abs(result.marker_z[i])


E2E = {
    #          100 kHz            1 MHz      10 MHz     100 MHz    1 GHz            plane 1 MHz
    "VDD_CORE": [(38.69e-3, 0.03), (3.294e-3, 0.05), (35.42e-3, 0.05), (139.5e-3, 0.05),
                 (2.902, 0.10), (331.7, 0.03)],
    "VDD_IO": [(152.0e-3, 0.03), (12.37e-3, 0.05), (62.72e-3, 0.05), (881.7e-3, 0.05),
               (4.494, 0.10), (995.1, 0.03)],
}
FREQS = [1e5, 1e6, 1e7, 1e8, 1e9]


@pytest.mark.parametrize("name", ["VDD_CORE", "VDD_IO"])
def test_end_to_end_example(example_results, name):
    results, issues, _ = example_results
    assert not [i for i in issues if i.severity.name == "ERROR"]
    r = results[name]
    for f, (expect, rel) in zip(FREQS, E2E[name][:5]):
        assert _z_at(r, f) == pytest.approx(expect, rel=rel), f"{name} @ {f:g} Hz"
    plane_1m = abs(r.marker_z_plane_only[list(r.marker_f_hz).index(1e6)])
    assert plane_1m == pytest.approx(E2E[name][5][0], rel=E2E[name][5][1])
    assert r.z_plane_only is not None and r.z_plane_only.shape == r.f_hz.shape
    assert r.f_hz.size == 400 and r.marker_f_hz.tolist() == [1e6, 1e7, 1e8]


def test_end_to_end_derived_quantities(example_results):
    results, _, _ = example_results
    core, io = results["VDD_CORE"].info, results["VDD_IO"].info
    assert (core["W_m"], core["H_m"], core["D_ref_m"]) == pytest.approx((60 * MM, 21 * MM, 15 * MM))
    assert (io["W_m"], io["H_m"], io["D_ref_m"]) == pytest.approx((30 * MM, 14 * MM, 10 * MM))
    assert results["VDD_CORE"].placement.xy_m[0] == pytest.approx([30 * MM, 3 * MM])
    assert results["VDD_IO"].placement.xy_m[0] == pytest.approx([15 * MM, 2 * MM])
    assert (core["h_near_m"], core["h_r_m"]) == pytest.approx((0.135 * MM, 0.305 * MM))
    assert (io["h_near_m"], io["h_r_m"]) == pytest.approx((1.105 * MM, 2.245 * MM))
    assert core["L_loop"] == pytest.approx(0.054411e-9, rel=1e-5)
    assert io["L_loop"] == pytest.approx(0.866012e-9, rel=1e-5)
    for info in (core, io):
        assert info["w_pad_m"] == pytest.approx(0.22369 * MM, rel=1e-5)
        assert info["w_dec_m"] == pytest.approx(0.22369 * MM, rel=1e-5)
        assert info["er_eff"] == pytest.approx(4.3) and info["tand_eff"] == pytest.approx(0.018)
        assert info["d_m"] == pytest.approx(0.1 * MM)
    assert core["C_plane"] == pytest.approx(479.72e-12, rel=1e-5)
    assert io["C_plane"] == pytest.approx(159.91e-12, rel=1e-4)
    assert (core["M"], core["N"], core["n_dynamic"], core["P"]) == (805, 282, 6, 15)
    assert (io["M"], io["N"], io["n_dynamic"], io["P"]) == (403, 188, 2, 4)
    # §3.6: legitimate cases have rcond ≫ 1e-14 (doc: 2.4e-6 and 2.5e-5)
    assert core["min_rcond"] == pytest.approx(2.4e-6, rel=0.05)
    assert io["min_rcond"] == pytest.approx(2.5e-5, rel=0.05)
    assert results["VDD_IO"].placement.caps_per_port.tolist() == [2, 2, 1]


def test_end_to_end_performance(example_results):
    _, _, elapsed = example_results
    assert elapsed < 10.0  # design target < 2 s on a desktop; generous for CI


def test_vdd_io_four_pad_vias(example_inputs):
    inputs = dataclasses.replace(example_inputs,
                                 vias=dataclasses.replace(example_inputs.vias, pad_via_count=4))
    results, issues = compute_project(inputs)
    r = {x.name: x for x in results}["VDD_IO"]
    assert r.info["w_pad_m"] == pytest.approx(1.77893 * MM, rel=1e-5)
    assert r.placement.port_widths_m[0] == pytest.approx(1.77893 * MM, rel=1e-5)
    expect = [152.3e-3, 10.88e-3, 20.79e-3, 445.7e-3, 162.5e-3]
    for f, e in zip(FREQS, expect):
        assert _z_at(r, f) == pytest.approx(e, rel=0.10), f"@ {f:g} Hz"
    plane_1m = abs(r.marker_z_plane_only[0])
    assert plane_1m == pytest.approx(995.1, rel=0.03)


def _golden_doc(results):
    doc = {}
    for name, r in results.items():
        doc[name] = {"f_hz": r.f_hz.tolist(), "re": r.z_pad.real.tolist(),
                     "im": r.z_pad.imag.tolist(),
                     "plane_abs": np.abs(r.z_plane_only).tolist(),
                     "marker_f_hz": r.marker_f_hz.tolist(),
                     "marker_abs": np.abs(r.marker_z).tolist()}
    return doc


def test_golden_regression(example_results):
    """§8.11 #4: full curves stored on first passing run; regressions compare rel 1e-6."""
    results, _, _ = example_results
    doc = _golden_doc(results)
    if not GOLDEN.exists():  # pragma: no cover - first run only
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(doc, indent=1))
    ref = json.loads(GOLDEN.read_text())
    for name, cur in doc.items():
        z_ref = np.array(ref[name]["re"]) + 1j * np.array(ref[name]["im"])
        z_cur = np.array(cur["re"]) + 1j * np.array(cur["im"])
        assert np.array(cur["f_hz"]) == pytest.approx(np.array(ref[name]["f_hz"]), rel=1e-12)
        assert np.max(np.abs(z_cur - z_ref) / np.abs(z_ref)) < 1e-6
        assert np.array(cur["plane_abs"]) == pytest.approx(np.array(ref[name]["plane_abs"]),
                                                           rel=1e-6)
        assert np.array(cur["marker_abs"]) == pytest.approx(np.array(ref[name]["marker_abs"]),
                                                            rel=1e-6)


# 5 -------------------------------------------------------------------------------------------
def test_missing_model_isolated_and_no_decaps(example_inputs):
    rows = [dataclasses.replace(r) for r in example_inputs.decap_rows]
    for r in rows:
        if r.pwr_name == "VDD_IO":
            r.model_file = "does_not_exist.mod"
    inputs = dataclasses.replace(example_inputs, decap_rows=rows)
    assert "E_DECAP_FILE_NOT_FOUND" in [i.code for i in validate_inputs(inputs)]
    results, issues = compute_project(inputs)
    assert [r.name for r in results] == ["VDD_CORE"]
    errs = [i for i in issues if i.code == "E_DECAP_FILE_NOT_FOUND"]
    assert errs and all(i.source == "PWR:VDD_IO" for i in errs)

    extra = PwrSpec("NODECAP", 5, 3, 25 * MM)
    inputs = dataclasses.replace(example_inputs, pwrs=list(example_inputs.pwrs) + [extra])
    results, issues = compute_project(inputs)
    r = {x.name: x for x in results}["NODECAP"]
    assert r.placement.height_m == pytest.approx(25 * MM)
    assert r.placement.n_ports == 1
    assert "W_PWR_NO_DECAPS" in [i.code for i in issues if i.source == "PWR:NODECAP"]
    assert np.array_equal(r.z_pad, r.z_plane_only)
    c = r.info["C_plane"]
    assert c == pytest.approx(8.8541878128e-12 * 4.3 * 0.025 ** 2 / 1e-4, rel=1e-9)


def test_global_errors_return_no_results(example_inputs):
    inputs = dataclasses.replace(example_inputs, f_start_hz=10.0)
    results, issues = compute_project(inputs)
    assert results == [] and "E_SWEEP_RANGE" in [i.code for i in issues]


# 6 -------------------------------------------------------------------------------------------
def test_cancellation(example_inputs):
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(CancelledError):
        compute_project(example_inputs, cancel=cancel)


def test_progress_monotonic(example_inputs):
    seen = []
    compute_project(example_inputs, progress=lambda x, msg: seen.append((x, msg)))
    values = [v for v, _ in seen]
    assert values[-1] == pytest.approx(1.0)
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:]))
    assert all(0.0 <= v <= 1.0 for v in values)


# 7 -------------------------------------------------------------------------------------------
@pytest.mark.slow
def test_performance_guard():
    st = simple_stackup(er=4.3, tand=0.018)
    model = rlc_model(0.03, 0.45e-9, 100e-9)
    f = np.geomspace(1e5, 1e9, 400)
    t0 = time.perf_counter()
    res = compute_pwr(st, PwrSpec("BIG", 5, 3, 100 * MM),
                      [DecapGroup("BIG", model, 50, 70 * MM)], VIAS, f, [1e6, 1e7, 1e8], False,
                      IssueCollector())
    elapsed = time.perf_counter() - t0
    assert res.placement.height_m == pytest.approx(98 * MM)
    assert res.info["P"] == 51
    assert elapsed < 60.0
