"""Stack-up and plane-pair derivation (DESIGN.md §2.1–§2.3, §4.2, §4.3, §8.2)."""

from __future__ import annotations

import math

import pytest

from conftest import build_stackup
from simple_pi_calculator.core.stackup import (
    Layer,
    Stackup,
    combine_dielectrics_first_order,
    derive_plane_pair,
)
from simple_pi_calculator.errors import InputError, IssueCollector

MM = 1e-3


def _codes(issues: IssueCollector) -> list[str]:
    return [i.code for i in issues.issues]


def test_example_geometry(example_stackup: Stackup):
    assert len(example_stackup.layers) == 11
    assert set(example_stackup.metal_layers) == {1, 3, 5, 7, 9, 11}
    assert example_stackup.total_thickness == pytest.approx(1.41 * MM, rel=1e-12)
    assert example_stackup.z_center(5) == pytest.approx(0.2875 * MM, rel=1e-12)
    assert example_stackup.z_center(3) == pytest.approx(0.1525 * MM, rel=1e-12)
    assert example_stackup.z_top(3) == pytest.approx(0.135 * MM, rel=1e-12)
    assert example_stackup.z_top(7) == pytest.approx(1.105 * MM, rel=1e-12)
    assert example_stackup.by_number(6).name == "PP2"


def test_example_validates_without_errors_or_warnings(example_stackup: Stackup):
    issues = IssueCollector()
    example_stackup.validate(issues)
    assert not issues.has_errors()
    assert not issues.warnings
    assert set(_codes(issues)) == {"W_STACK_METAL_DKDF"}


def test_layers_sorted_on_construction():
    s = Stackup((Layer(2, "D", 1e-4, None, 4.0, 0.0), Layer(1, "M", 3.5e-5, 5.8e7, None, None)))
    assert [layer.number for layer in s.layers] == [1, 2]
    assert s.z_top(2) == pytest.approx(3.5e-5)


def test_derive_example_pair(example_stackup: Stackup):
    issues = IssueCollector()
    pair = derive_plane_pair(example_stackup, 5, 3, issues, "PWR:VDD_CORE")
    assert pair.d_m == pytest.approx(0.1 * MM, rel=1e-12)
    assert pair.er_eff == pytest.approx(4.3, rel=1e-12)
    assert pair.tand_eff == pytest.approx(0.018, rel=1e-12)
    assert pair.pwr_layer.number == 5 and pair.gnd_layer.number == 3
    assert pair.nearer_layer.number == 3
    assert not issues.issues
    pair_io = derive_plane_pair(example_stackup, 7, 9, issues, "PWR:VDD_IO")
    assert pair_io.d_m == pytest.approx(0.1 * MM, rel=1e-12)
    assert pair_io.er_eff == pytest.approx(4.3, rel=1e-12)


def _two_dielectrics(er1, td1, er2, td2):
    return build_stackup([
        (1, "P", 0.035, 5.8e7, None, None),
        (2, "D1", 0.05, None, er1, td1),
        (3, "D2", 0.05, None, er2, td2),
        (4, "G", 0.035, 5.8e7, None, None),
    ])


def test_two_prepreg_exact_form():
    pair = derive_plane_pair(_two_dielectrics(4.0, 0.020, 3.5, 0.010), 1, 4, IssueCollector(), "t")
    assert pair.d_m == pytest.approx(0.1 * MM, rel=1e-12)
    assert pair.er_eff == pytest.approx(3.733426, rel=1e-6)
    assert pair.tand_eff == pytest.approx(0.01466592, rel=1e-6)


def test_extreme_mix_exact_form():
    pair = derive_plane_pair(_two_dielectrics(3.0, 0.002, 4.5, 0.03), 4, 1, IssueCollector(), "t")
    assert pair.er_eff == pytest.approx(3.600677, rel=1e-6)
    assert pair.tand_eff == pytest.approx(0.01319398, rel=1e-6)


def test_equal_er_equals_thickness_weighted_tand():
    s = build_stackup([
        (1, "P", 0.035, 5.8e7, None, None),
        (2, "D1", 0.03, None, 4.0, 0.01),
        (3, "D2", 0.07, None, 4.0, 0.03),
        (4, "G", 0.035, 5.8e7, None, None),
    ])
    dielectrics = [s.by_number(2), s.by_number(3)]
    weighted = (0.03 * 0.01 + 0.07 * 0.03) / 0.1
    # First-order form (§2.2): identical to the thickness-weighted average for equal εr.
    _d, er1, td1 = combine_dielectrics_first_order(dielectrics)
    assert er1 == pytest.approx(4.0, rel=1e-12)
    assert td1 == pytest.approx(weighted, rel=1e-12)
    # Exact complex form used by the engine: equal to it for equal tanδ, within 0.1 % otherwise.
    pair = derive_plane_pair(s, 1, 4, IssueCollector(), "t")
    assert pair.er_eff == pytest.approx(4.0, rel=1e-3)
    assert pair.tand_eff == pytest.approx(weighted, rel=1e-3)
    same = build_stackup([(1, "P", 0.035, 5.8e7, None, None), (2, "D1", 0.03, None, 4.0, 0.02),
                          (3, "D2", 0.07, None, 4.0, 0.02), (4, "G", 0.035, 5.8e7, None, None)])
    pair_same = derive_plane_pair(same, 1, 4, IssueCollector(), "t")
    assert pair_same.er_eff == pytest.approx(4.0, rel=1e-12)
    assert pair_same.tand_eff == pytest.approx(0.02, rel=1e-12)


def test_first_order_matches_doc_values():
    s = _two_dielectrics(4.0, 0.020, 3.5, 0.010)
    _d, er, td = combine_dielectrics_first_order([s.by_number(2), s.by_number(3)])
    assert er == pytest.approx(3.733333, rel=1e-6)
    assert td == pytest.approx(0.01466667, rel=1e-6)


def test_metal_between_warns_and_counts_thickness(example_stackup: Stackup):
    issues = IssueCollector()
    pair = derive_plane_pair(example_stackup, 3, 7, issues, "PWR:X")
    assert "W_STACK_METAL_BETWEEN" in _codes(issues)
    # I = {4, 5, 6}: 0.1 + 0.035 + 0.8 mm
    assert pair.d_m == pytest.approx(0.935 * MM, rel=1e-12)
    assert pair.d_dielectric_m == pytest.approx(0.9 * MM, rel=1e-12)
    assert pair.intermediate_metal == (5,)
    expected_er = 0.9 / (0.1 / 4.3 + 0.8 / 4.4)
    assert pair.er_eff == pytest.approx(expected_er, rel=1e-3)
    assert not issues.has_errors()


def test_far_gnd_warning(example_stackup: Stackup):
    issues = IssueCollector()
    derive_plane_pair(example_stackup, 3, 9, issues, "PWR:X")
    assert "W_PWR_FAR_GND" in _codes(issues)


@pytest.mark.parametrize("pwr, gnd, code", [
    (4, 3, "E_PWR_LAYER_NOT_METAL"),
    (5, 5, "E_PWR_SAME_LAYER"),
    (5, 42, "E_PWR_LAYER_NOT_FOUND"),
])
def test_pair_errors(example_stackup: Stackup, pwr, gnd, code):
    issues = IssueCollector()
    with pytest.raises(InputError) as exc:
        derive_plane_pair(example_stackup, pwr, gnd, issues, "PWR:X")
    assert code in _codes(issues)
    assert code in [i.code for i in exc.value.issues]


def test_no_dielectric_error():
    s = build_stackup([
        (1, "P", 0.035, 5.8e7, None, None),
        (2, "G", 0.035, 5.8e7, None, None),
        (3, "D", 0.1, None, 4.0, 0.02),
    ])
    issues = IssueCollector()
    with pytest.raises(InputError):
        derive_plane_pair(s, 1, 2, issues, "PWR:X")
    assert "E_STACK_NO_DIELECTRIC" in _codes(issues)


def test_plane_capacitance_values(example_stackup: Stackup):
    s = build_stackup([(1, "P", 0.035, math.inf, None, None), (2, "D", 0.1, None, 4.0, 0.0),
                       (3, "G", 0.035, math.inf, None, None)])
    pair = derive_plane_pair(s, 1, 3, IssueCollector(), "t")
    assert pair.plane_capacitance(20 * MM, 20 * MM) == pytest.approx(141.667e-12, rel=1e-5)
    pair43 = derive_plane_pair(example_stackup, 5, 3, IssueCollector(), "t")
    assert pair43.plane_capacitance(60 * MM, 21 * MM) == pytest.approx(479.720e-12, rel=1e-5)
    assert pair43.plane_capacitance(30 * MM, 14 * MM) == pytest.approx(159.907e-12, rel=1e-5)


def test_validation_codes():
    s = build_stackup([
        (1, "M", 0.035, 1e3, None, None),        # sigma range warning
        (2, "M2", 0.035, 5.8e7, None, None),     # adjacent metal info
        (3, "D", 0.0, None, 0.5, None),          # thickness, dk error, df missing
        (3, "Ddup", 0.1, None, 4.0, 1.5),        # duplicate number, df range
        (5, "G", 0.035, 5.8e7, None, None),      # gap
    ])
    issues = IssueCollector()
    s.validate(issues)
    codes = set(_codes(issues))
    assert {"W_STACK_SIGMA_RANGE", "W_STACK_ADJ_METAL", "E_STACK_THICKNESS", "E_STACK_DK",
            "W_STACK_DF_MISSING", "E_STACK_LAYER_DUP", "E_STACK_DF", "W_STACK_LAYER_GAP"} <= codes


def test_empty_stackup():
    issues = IssueCollector()
    Stackup(()).validate(issues)
    assert _codes(issues) == ["E_STACK_EMPTY"]


def test_metal_detection():
    assert Layer(1, "", 1e-5, 5.8e7, None, None).is_metal
    assert Layer(1, "", 1e-5, math.inf, None, None).is_metal
    assert not Layer(1, "", 1e-5, 0.0, 4.0, 0.0).is_metal
    assert not Layer(1, "", 1e-5, None, 4.0, 0.0).is_metal
