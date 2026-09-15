"""DESIGN.md §8.11 — port reduction and end-to-end PDN impedance."""

from __future__ import annotations

import dataclasses
import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core import pdn as pdn_mod
from simple_pi_calculator.core.cavity import (CavityModel, cavity_cache_key, cluster_port_width,
                                              cluster_via_positions)
from simple_pi_calculator.core.engine import CancelledError, compute_project, validate_inputs
from simple_pi_calculator.core.pdn import (DecapGroup, PwrSpec, SingularReductionError,
                                           combine_pads, compute_pwr, evaluation_frequencies,
                                           reduce_ports)
from simple_pi_calculator.core.stackup import Layer, PlanePair, Stackup
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


def _example_core_groups(inputs, mult, dummy, scale=1.0):
    """VDD_CORE decap groups of the example with counts × ``mult`` and model impedance × scale."""
    from simple_pi_calculator.core.decap_model import DecapModelCache
    from simple_pi_calculator.core.types import resolve_model_path
    cache = DecapModelCache()
    groups = []
    for row in inputs.decap_rows:
        if row.pwr_name != "VDD_CORE":
            continue
        path = resolve_model_path(row.model_file, inputs.decap_source_dir, inputs.project_dir)
        model = cache.get(path, row.subckt, "series", IssueCollector())
        if scale != 1.0:
            model = FuncModel(lambda f, m=model: scale * np.asarray(m.impedance(f)))
        groups.append(DecapGroup("VDD_CORE", model, row.count * mult, row.distance_m, dummy))
    return groups


def _core_run(inputs, groups, f):
    pwr = next(p for p in inputs.pwrs if p.name == "VDD_CORE")
    return compute_pwr(inputs.stackup, pwr, groups, inputs.vias, f, [1e6, 1e7, 1e8], False,
                       IssueCollector(), workers=1)


def test_dummy_load_rule_with_identical_positions(example_inputs):
    """§2.6.5 with the placement forced identical: 2N caps on N via sets (Dummy Cap) equal N
    ports loaded by Z_cap/2 + Z_via,dec — the shared via set is in series and not divided."""
    f = np.geomspace(1e5, 1e9, 120)
    dummy = _core_run(example_inputs, _example_core_groups(example_inputs, 2, True), f)
    half = _core_run(example_inputs, _example_core_groups(example_inputs, 1, False, 0.5), f)
    assert np.array_equal(dummy.placement.xy_m, half.placement.xy_m)  # same ports, same places
    assert dummy.placement.caps_per_port.tolist() == [2] * 14
    assert np.max(np.abs(dummy.z_pad / half.z_pad - 1.0)) < 1e-9
    # ... and differ from the original N single-cap ports (the flag is not a no-op)
    single = _core_run(example_inputs, _example_core_groups(example_inputs, 1, False), f)
    assert np.max(np.abs(dummy.z_pad / single.z_pad - 1.0)) > 0.1


def test_dummy_ten_mhz_is_an_antiresonance_shift(example_inputs):
    """User observation: VDD_CORE with 2× count, 10 MHz is lower with Dummy Cap (18.1 mΩ) than
    without (23.2 mΩ). 10 MHz lies on the anti-resonance between the 10 µF bank (inductive) and
    the 100 nF bank (capacitive). Halving the via sets raises the bank-to-bank loop inductance
    by ≈ 20 %, which moves the peak down by ≈ 1/√1.2 (9.66 → 8.81 MHz) at almost the same peak
    height, so 10 MHz is further down the peak's upper flank. Expected, not a bug."""
    f = np.geomspace(5e6, 11e6, 301)  # between the two series resonances (≈1 and ≈12 MHz)
    runs = {}
    for key, mult, dummy in (("2x", 2, False), ("2x_dummy", 2, True)):
        r = _core_run(example_inputs, _example_core_groups(example_inputs, mult, dummy), f)
        z = np.abs(r.z_pad)
        i = int(np.argmax(z))
        runs[key] = (f[i], z[i], abs(r.marker_z[0]))  # marker 10 MHz
    (f2, p2, z2), (fd, pd, zd) = runs["2x"], runs["2x_dummy"]
    assert (z2, zd) == pytest.approx((23.15e-3, 18.13e-3), rel=5e-3)
    assert f2 == pytest.approx(9.66e6, rel=0.01) and fd == pytest.approx(8.81e6, rel=0.01)
    assert pd == pytest.approx(p2, rel=0.03)  # same peak height ...
    assert (f2 / fd) ** 2 == pytest.approx(1.20, abs=0.03)  # ... shifted by the L ratio
    assert fd < f2 < 10e6  # both peaks below 10 MHz: the lower one reads lower at 10 MHz


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


@pytest.mark.parametrize("n_pad, w_mm", [(2, 0.84120), (4, 1.77893)])
def test_vias_per_decap_pad(example_inputs, example_results, n_pad, w_mm):
    """§2.6.4: n vias on each decap pad = n PWR/GND pairs in parallel: Z_via,dec = Z_viapair/n
    and a via-cluster decap port of n cavity-crossing vias (§2.4.5); the PAD is unchanged."""
    inputs = dataclasses.replace(example_inputs,
                                 vias=dataclasses.replace(example_inputs.vias, vias_per_pad=n_pad))
    assert inputs.vias.n_pair_dec == n_pad
    results, issues = compute_project(inputs)
    assert not [i for i in issues if i.severity.name == "ERROR"]
    base = example_results[0]
    for r in results:
        assert r.info["w_dec_m"] == pytest.approx(w_mm * MM, rel=1e-5)
        assert r.info["w_pad_m"] == pytest.approx(0.22369 * MM, rel=1e-5)
        assert np.all(r.placement.port_widths_m[1:] == r.info["w_dec_m"])
        assert r.info["P"] == base[r.name].info["P"]
        # more parallel vias per decap: lower |Z| above the capacitive region
        assert np.all(np.abs(r.marker_z[1:]) < np.abs(base[r.name].marker_z[1:]))


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


# ---------------------------------------------------------------------------------------------
# Several observation pads per PWR net (§2.5.1, §2.8, schema 3)
# ---------------------------------------------------------------------------------------------
def _with_pads(inputs, n_pads, **via_changes):
    return dataclasses.replace(
        inputs, pwrs=[dataclasses.replace(p, n_pads=n_pads) for p in inputs.pwrs],
        vias=dataclasses.replace(inputs.vias, **via_changes))


def _old_reduction(z_cav, z_load):
    """The v0.1 single-PAD Schur reduction written out independently (§2.8)."""
    K = z_cav.shape[1] - 1
    out = np.empty(z_cav.shape[0], dtype=complex)
    for i in range(z_cav.shape[0]):
        a = z_cav[i, 1:, 1:] + np.diag(z_load[i])
        u = np.linalg.solve(a, z_cav[i, 1:, 0])
        out[i] = z_cav[i, 0, 0] - z_cav[i, 0, 1:] @ u
    return out if K else z_cav[:, 0, 0]


def test_single_pad_is_the_old_code_path(example_inputs, example_results):
    """(i) N_pad = 1 reproduces the pre-schema-3 result: the explicit ``n_pads = 1`` project equals
    the example to 1e-12, and the general formula Z = 1/(1ᵀ(Z_pp,red + Z_via)⁻¹1) on the 1×1
    reduced matrix equals Z_red + Z_via,pad of the old reduction to 1e-12."""
    base = example_results[0]
    results, _ = compute_project(_with_pads(example_inputs, 1))
    for r in results:
        assert r.n_pads == 1 and r.info["n_pads"] == 1
        np.testing.assert_allclose(r.z_pad, base[r.name].z_pad, rtol=1e-12, atol=0)
        np.testing.assert_allclose(r.marker_z, base[r.name].marker_z, rtol=1e-12, atol=0)
        np.testing.assert_allclose(r.z_plane_only, base[r.name].z_plane_only, rtol=1e-12, atol=0)
    rng = np.random.default_rng(7)
    F, P = 6, 5
    m = rng.normal(size=(F, P, P)) + 1j * rng.normal(size=(F, P, P))
    z_cav = m + np.transpose(m, (0, 2, 1)) + 10 * np.eye(P)
    z_load = rng.normal(size=(F, P - 1)) + 1j * rng.normal(size=(F, P - 1))
    z_via = rng.normal(size=F) * 1e-3 + 1j * rng.normal(size=F) * 1e-3
    old = _old_reduction(z_cav, z_load)
    new = reduce_ports(z_cav, z_load, n_pads=1)
    np.testing.assert_allclose(new, old, rtol=1e-12, atol=0)
    general = combine_pads(new[:, None, None], z_via)
    np.testing.assert_allclose(general, old + z_via, rtol=1e-12, atol=0)


def test_coincident_pads_equal_one_pad_with_parallel_vias():
    """Algebraic identity of §2.8: N pads with identical rows/columns (same position and width) and
    Z_via each equal one pad with Z_via/N: 1/(1ᵀ(z·J + Z_via·I)⁻¹1) = z + Z_via/N."""
    rng = np.random.default_rng(3)
    F, K, N = 4, 3, 4
    m = rng.normal(size=(F, K + 1, K + 1)) + 1j * rng.normal(size=(F, K + 1, K + 1))
    z1 = m + np.transpose(m, (0, 2, 1)) + 8 * np.eye(K + 1)
    idx = [0] * N + list(range(1, K + 1))
    zN = z1[:, idx][:, :, idx]
    z_load = 0.5 + 1j * rng.normal(size=(F, K))
    z_via = 0.01 + 0.02j
    single = reduce_ports(z1, z_load) + z_via / N
    multi = combine_pads(reduce_ports(zN, z_load, n_pads=N), np.full(F, z_via))
    np.testing.assert_allclose(multi, single, rtol=1e-9)


def test_explicit_pad_cluster_matches_cluster_port():
    """Four single-via pads at the §2.4.5 cluster positions, joined in parallel through
    reduce_ports(n_pads=4) + combine_pads, give the explicit-port loop inductance 0.16653 nH of
    §8.3 #11; the one-port cluster model (w = 1.77893 mm) is within 2 %."""
    pair = PlanePair(pwr_layer=Layer(1, "P", 35e-6, math.inf, None, None),
                     gnd_layer=Layer(3, "G", 35e-6, math.inf, None, None), d_m=0.1 * MM,
                     er_eff=4.0, tand_eff=0.0, d_dielectric_m=0.1 * MM)
    a, b, f = 30 * MM, 14 * MM, np.array([1e6])
    w1 = cluster_port_width(1, 0.2 * MM, 1 * MM)
    off = cluster_via_positions(4, math.sqrt(2) * MM)
    ports = [[15 * MM + dx, 2 * MM + dy] for dx, dy in off] + [[15 * MM, 12 * MM]]
    z = CavityModel(a, b, pair, ports, [w1] * 5, np.array([1e5, 1e9])).z_matrix(f)
    short = np.zeros((1, 1), dtype=complex)
    z_pad = combine_pads(reduce_ports(z, short, n_pads=4), np.zeros(1))
    L4 = z_pad[0].imag / (2 * math.pi * f[0])
    assert L4 == pytest.approx(0.16653e-9, rel=5e-3)
    w4 = cluster_port_width(4, 0.2 * MM, 1 * MM)
    zc = CavityModel(a, b, pair, [[15 * MM, 2 * MM], [15 * MM, 12 * MM]], [w4, w1],
                     np.array([1e5, 1e9])).z_matrix(f)
    Lc = reduce_ports(zc, short)[0].imag / (2 * math.pi * f[0])
    assert Lc == pytest.approx(L4, rel=0.02)


def test_combine_pads_singular():
    """Coincident pads without via impedance make 1ᵀ(z·J)⁻¹1 singular → E_SINGULAR path."""
    z = np.ones((2, 3, 3), dtype=complex) * (1 + 1j)
    with pytest.raises(SingularReductionError):
        combine_pads(z, np.zeros(2))


#: VDD_IO / VDD_CORE with four observation pads (1 PAD via pair each), regression of this
#: implementation: |Z| at 100 kHz, 1 MHz, 10 MHz, 100 MHz, 1 GHz [Ω]
FOUR_PADS = {
    "VDD_IO": [152.28e-3, 10.786e-3, 18.742e-3, 425.88e-3, 110.05e-3],
    "VDD_CORE": [38.724e-3, 3.1319e-3, 28.767e-3, 57.218e-3, 2.2357],
}


def test_four_pads_end_to_end(example_inputs, example_results):
    """N_pad = 4 on the example: pad row at y = 0.2·D_ref, P = 4 + decap ports, regression values,
    and comparison with the single-pad "VDD_IO, 4 PAD vias" variant (§8.11 #4)."""
    results, issues = compute_project(_with_pads(example_inputs, 4))
    assert not [i for i in issues if i.severity.name == "ERROR"]
    r = {x.name: x for x in results}
    io_, core = r["VDD_IO"], r["VDD_CORE"]
    assert io_.n_pads == 4 and io_.info["n_pads"] == 4 and io_.info["P"] == 7
    assert core.info["P"] == 18
    m_p = 0.5 * io_.info["w_pad_m"] + 0.1 * 30 * MM
    l_p = 30 * MM - 2 * m_p
    expect_x = [m_p + (i + 0.5) * l_p / 4 for i in range(4)]
    assert io_.placement.pads_xy_m[:, 0] == pytest.approx(expect_x, abs=1e-12)
    assert np.all(io_.placement.pads_xy_m[:, 1] == pytest.approx(2 * MM))
    # decap ports and D_ref semantics unchanged
    assert io_.placement.xy_m[4:] == pytest.approx(example_results[0]["VDD_IO"].placement.xy_m[1:])
    for name, values in FOUR_PADS.items():
        for f, e in zip(FREQS, values):
            assert _z_at(r[name], f) == pytest.approx(e, rel=1e-3), f"{name} @ {f:g} Hz"
    # not geometrically equivalent to one 4-via cluster pad at (15, 2) mm (445.7 mΩ @100 MHz):
    # four pads spread across the width at the same total via count give a lower mid-band |Z|
    single, _ = compute_project(_with_pads(example_inputs, 1, pad_via_count=4))
    single = {x.name: x for x in single}["VDD_IO"]
    assert _z_at(single, 1e8) == pytest.approx(445.7e-3, rel=0.10)
    assert _z_at(io_, 1e8) < _z_at(single, 1e8)
    assert abs(io_.marker_z_plane_only[0]) == pytest.approx(995.1, rel=0.03)


def test_more_pads_lower_mid_band_impedance(example_inputs):
    """|Z| at 100 MHz decreases monotonically for N_pad = 1 → 2 → 4 (both example nets)."""
    z = {}
    for n in (1, 2, 4):
        results, _ = compute_project(_with_pads(example_inputs, n))
        for res in results:
            z.setdefault(res.name, []).append(_z_at(res, 1e8))
    for name, values in z.items():
        assert values[0] > values[1] > values[2], (name, values)


def test_n_pads_validation_and_cache_key(example_inputs):
    bad = _with_pads(example_inputs, 0)
    assert "E_PWR_NPADS" in [i.code for i in validate_inputs(bad)]
    results, issues = compute_project(bad)
    assert not results and "E_PWR_NPADS" in [i.code for i in issues]
    pair = PlanePair(pwr_layer=Layer(1, "P", 35e-6, 5.8e7, None, None),
                     gnd_layer=Layer(3, "G", 35e-6, 5.8e7, None, None), d_m=0.1 * MM,
                     er_eff=4.0, tand_eff=0.0, d_dielectric_m=0.1 * MM)
    xy, wid, f = np.zeros((3, 2)), np.full(3, 0.3 * MM), np.geomspace(1e5, 1e9, 5)
    k1 = cavity_cache_key(20 * MM, 20 * MM, pair, xy, wid, f, pdn_mod.ModeSettings())
    k2 = cavity_cache_key(20 * MM, 20 * MM, pair, xy, wid, f, pdn_mod.ModeSettings(), n_pads=2)
    assert k1 != k2


def test_multi_pad_reduction_workers_and_exact_rcond():
    """The N_pad reduction and the pad combination are independent of the worker count and of
    the probe-estimate screening (§3.6, §3.9)."""
    rng = np.random.default_rng(11)
    F, P, N = 40, 9, 3
    m = rng.normal(size=(F, P, P)) + 1j * rng.normal(size=(F, P, P))
    z_cav = m + np.transpose(m, (0, 2, 1)) + 12 * np.eye(P)
    z_load = 1 + 1j * rng.normal(size=(F, P - N))
    zr1, rc1, _ = pdn_mod._reduce(z_cav, z_load, workers=1, n_pads=N)
    zr4, _, _ = pdn_mod._reduce(z_cav, z_load, workers=4, n_pads=N)
    zre, rce, _ = pdn_mod._reduce(z_cav, z_load, exact_rcond=True, n_pads=N)
    np.testing.assert_allclose(zr4, zr1, rtol=1e-12)
    np.testing.assert_allclose(zre, zr1, rtol=1e-12)
    assert zr1.shape == (F, N, N)
    zv = np.full(F, 0.05 + 0.01j)
    zp1, _, ex1 = pdn_mod._combine_pads(zr1, zv, workers=1)
    zp4, _, _ = pdn_mod._combine_pads(zr1, zv, workers=4)
    zpe, rcp, _ = pdn_mod._combine_pads(zr1, zv, exact_rcond=True)
    np.testing.assert_allclose(zp4, zp1, rtol=1e-12)
    np.testing.assert_allclose(zpe, zp1, rtol=1e-12)
    y = np.linalg.inv(zr1 + zv[:, None, None] * np.eye(N))
    np.testing.assert_allclose(zp1, 1 / y.sum(axis=(1, 2)), rtol=1e-10)
    assert np.all(rcp > 1e-14)
