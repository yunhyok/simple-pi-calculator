"""DESIGN.md §8.5 — via loop inductance, resistance, via-set impedance and port loads."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from simple_pi_calculator.core.pdn import port_loads
from simple_pi_calculator.core.placement import Placement
from simple_pi_calculator.core.via import (ViaSettings, antipad_inductance, coax_inductance,
                                           decap_via_impedance, goldfarb_pucel_inductance,
                                           loop_inductance, pad_via_impedance, pair_inductance,
                                           partial_mutual_inductance, validate_via_settings,
                                           via_geometry, via_pair_impedance, via_resistance)
from simple_pi_calculator.errors import InputError, IssueCollector

MM = 1e-3
NH = 1e-9
D = 0.2 * MM
DAP = 0.5 * MM
REL_L = 1e-5
MU0 = 4e-7 * math.pi


def vs(**kw) -> ViaSettings:
    return ViaSettings(drill_diameter_m=D, antipad_diameter_m=DAP, **kw)


def test_partial_mutual_inductance():
    assert partial_mutual_inductance(1 * MM, 1 * MM) == pytest.approx(0.093432 * NH, rel=REL_L)
    assert partial_mutual_inductance(1 * MM, 0.1 * MM) == pytest.approx(0.418647 * NH, rel=REL_L)


@pytest.mark.parametrize("h_mm, s_mm, expect_nh", [
    (1.0, 1.0, 0.765061), (0.1525, 0.5, 0.049604), (1.1225, 1.0, 0.875391), (5.0, 1.0, 4.430114),
])
def test_pair_inductance(h_mm, s_mm, expect_nh):
    assert pair_inductance(h_mm * MM, s_mm * MM, D) == pytest.approx(expect_nh * NH, rel=REL_L)


def test_pair_inductance_long_limit():
    h, s = 100 * MM, 1 * MM
    ratio = pair_inductance(h, s, D) / (MU0 * h / math.pi * math.log(s / (D / 2)))
    assert abs(ratio - 1) < 0.03


def test_single_formulas():
    assert antipad_inductance(35e-6, D, DAP) == pytest.approx(0.0064140 * NH, rel=REL_L)
    assert coax_inductance(1 * MM, D, DAP) == pytest.approx(0.183258 * NH, rel=REL_L)
    assert goldfarb_pucel_inductance(1 * MM, D) == pytest.approx(0.328148 * NH, rel=REL_L)
    gp = 2 * (goldfarb_pucel_inductance(1.1225 * MM, D)
              - partial_mutual_inductance(1.1225 * MM, 1 * MM))
    assert gp == pytest.approx(0.549560 * NH, rel=REL_L)


def test_via_geometry(example_stackup):
    issues = IssueCollector()
    g = via_geometry(example_stackup, 5, 3, issues)
    assert (g.nearer_layer, g.h_near_m, g.t_near_m, g.h_r_m) == pytest.approx(
        (3, 0.135 * MM, 0.035 * MM, 0.305 * MM), rel=1e-12)
    g = via_geometry(example_stackup, 7, 9, issues)
    assert g.nearer_layer == 7
    assert g.h_near_m == pytest.approx(1.105 * MM, rel=1e-12)
    assert g.h_r_m == pytest.approx(2.245 * MM, rel=1e-12)
    assert issues.codes() == []
    g = via_geometry(example_stackup, 3, 1, issues)
    assert g.nearer_layer == 1 and g.h_near_m == 0.0
    assert g.h_r_m == pytest.approx(0.035 * MM, rel=1e-12)
    assert issues.codes() == ["W_VIA_ZERO_LENGTH"]
    assert loop_inductance(g, vs()) == pytest.approx(0.0064140 * NH, rel=REL_L)


@pytest.mark.parametrize("model, core_nh, io_nh", [
    ("pair", 0.054411, 0.866012), ("goldfarb_pucel", 0.021836, 0.544438),
    ("coax", 0.055894, 0.411415),
])
def test_loop_inductance_models(example_stackup, model, core_nh, io_nh):
    issues = IssueCollector()
    s = vs(via_pitch_m=1 * MM, model=model)
    assert loop_inductance(via_geometry(example_stackup, 5, 3, issues), s) == pytest.approx(
        core_nh * NH, rel=REL_L)
    assert loop_inductance(via_geometry(example_stackup, 7, 9, issues), s) == pytest.approx(
        io_nh * NH, rel=REL_L)


def test_via_resistance():
    f = np.array([1e3, 1e6, 1e8, 1e9])
    r = via_resistance(f, 1 * MM, D, 25e-6, 5.8e7)
    assert r == pytest.approx(np.array([1.2544, 1.3369, 4.8665, 13.826]) * 1e-3, rel=1e-3)
    assert via_resistance(np.array([1e6]), 0.305 * MM, D, 25e-6, 5.8e7)[0] == pytest.approx(
        0.40775e-3, rel=1e-4)
    assert via_resistance(np.array([1e6]), 2.245 * MM, D, 25e-6, 5.8e7)[0] == pytest.approx(
        3.00130e-3, rel=1e-4)
    # solid via when plating ≥ r0: DC limit → 1/(σπr0²)
    r_solid = via_resistance(np.array([1.0]), 1.0, D, 1.0, 5.8e7)[0]
    assert r_solid == pytest.approx(1 / (5.8e7 * math.pi * (D / 2) ** 2), rel=1e-6)


def test_via_set_impedance(example_stackup):
    f = np.geomspace(1e5, 1e9, 9)
    z2 = decap_via_impedance(f, example_stackup, 7, 9, vs())
    z4 = decap_via_impedance(f, example_stackup, 7, 9, vs(vias_per_pad=2))  # 2 PWR + 2 GND
    assert z4 == pytest.approx(z2 / 2, rel=1e-12)
    zp = via_pair_impedance(f, example_stackup, 7, 9, vs())
    assert z2 == pytest.approx(zp, rel=1e-12)
    assert zp.imag == pytest.approx(2 * math.pi * f * 0.866012 * NH, rel=REL_L)
    assert pad_via_impedance(f, example_stackup, 7, 9, vs(pad_via_count=4)) == pytest.approx(
        zp / 4, rel=1e-12)
    assert decap_via_impedance(f, example_stackup, 7, 9, vs(vias_per_pad=4)) == pytest.approx(
        zp / 4, rel=1e-12)
    assert vs(vias_per_pad=3).n_pair_dec == 3  # odd counts are valid (one via per pad each)
    with pytest.raises(InputError) as exc:
        decap_via_impedance(f, example_stackup, 7, 9, vs(vias_per_pad=0))
    assert exc.value.issues[0].code == "E_VIA_COUNT"


@pytest.mark.parametrize("change, code", [
    (dict(vias_per_pad=0), "E_VIA_COUNT"),
    (dict(vias_per_pad=1.5), "E_VIA_COUNT"),
    (dict(antipad_diameter_m=D), "E_VIA_ANTIPAD"),
    (dict(via_pitch_m=0.2 * MM), "E_VIA_PITCH"),
    (dict(via_pitch_m=0.3 * MM), "W_VIA_PITCH_SMALL"),
])
def test_via_validation(change, code):
    issues = IssueCollector()
    validate_via_settings(dataclasses.replace(vs(), **change), issues)
    assert code in issues.codes()
    ok = IssueCollector()
    validate_via_settings(vs(), ok)
    assert ok.codes() == []


def _placement(caps):
    n = len(caps)
    return Placement(width_m=0.03, height_m=0.014, d_ref_m=0.01, xy_m=np.zeros((n + 1, 2)),
                     group_index=np.zeros(n, dtype=int), caps_per_port=np.asarray(caps),
                     port_widths_m=np.full(n + 1, 2e-4))


def test_port_loads():
    f = np.array([1e6])
    pl = _placement([2, 2, 1])
    zl = port_loads(f, pl, [None], [np.array([1.0 + 0j])], np.array([0.1 + 0j]), 0.0)
    assert zl[0] == pytest.approx([0.6, 0.6, 1.1], rel=1e-12)
    zl = port_loads(f, pl, [None], [np.array([1.0 + 0j])], np.array([0.1 + 0j]), 1 * NH)
    assert zl[0, 0] == pytest.approx((1 + 1j * 6.2832e-3) / 2 + 0.1, rel=1e-6)
