"""Units and constants (DESIGN.md §1.6, §4.1, §4.5, §5.5, §8.1)."""

from __future__ import annotations

import math

import pytest

from simple_pi_calculator import __version__, constants
from simple_pi_calculator.core import units


def test_version_string():
    import re
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_physical_constants():
    assert constants.MU0 == 4e-7 * math.pi
    assert constants.EPS0 == 8.8541878128e-12
    assert constants.C0 == 299_792_458.0
    assert constants.MARKER_FREQUENCIES_HZ == (1e6, 1e7, 1e8)
    assert (constants.DEFAULT_F_START_HZ, constants.DEFAULT_F_STOP_HZ,
            constants.DEFAULT_N_POINTS) == (1e5, 1e9, 400)


def test_mm_m_conversions():
    assert units.mm_to_m(0.035) == pytest.approx(35e-6, rel=1e-12)
    assert units.m_to_mm(1.41e-3) == pytest.approx(1.41, rel=1e-12)
    assert units.nh_to_h(0.45) == pytest.approx(0.45e-9, rel=1e-12)
    assert units.h_to_nh(5e-10) == pytest.approx(0.5, rel=1e-12)


@pytest.mark.parametrize("unit, factor", [
    (None, 1.0), ("mm", 1.0), ("um", 1e-3), ("mil", 0.0254), ("m", 1000.0), ("in", 25.4),
    ("inch", 25.4)])
def test_length_units(unit, factor):
    assert units.length_unit_factor_mm(unit) == pytest.approx(factor, rel=1e-12)


def test_unknown_length_unit():
    with pytest.raises(KeyError):
        units.length_unit_factor_mm("furlong")


def test_display_scale_factors():
    assert units.z_scale("ohm") == 1.0
    assert units.z_scale("mohm") == 1e3
    assert units.z_scale("uohm") == 1e6
    assert units.z_label("mohm") == "mΩ"
    assert units.z_label("uohm") == "µΩ"


@pytest.mark.parametrize("text, value", [
    ("100k", 1e5), ("1G", 1e9), ("2.5MHz", 2.5e6), ("1MEG", 1e6), ("10 kHz", 1e4), ("3e9", 3e9),
    ("100K", 1e5)])
def test_parse_frequency(text, value):
    assert units.parse_frequency(text) == pytest.approx(value, rel=1e-12)


@pytest.mark.parametrize("text", ["1m", "abc", "", "1 mHz"])
def test_parse_frequency_rejects(text):
    with pytest.raises(ValueError):
        units.parse_frequency(text)


def test_format_sig():
    assert units.format_sig(3.2891) == "3.289"
    assert units.format_sig(139.54) == "139.5"


SPICE_NUMBERS = [
    ("10u", 1e-5), ("10uF", 1e-5), ("1MEG", 1e6), ("1Meg", 1e6), ("2.2nH", 2.2e-9),
    ("5mOhm", 5e-3), ("1e-9", 1e-9), ("3mil", 7.62e-5), ("10Ohm", 10.0), ("1F", 1e-15),
    ("1Farad", 1e-15), ("0.1", 0.1), ("1e3k", 1e6),
]


@pytest.mark.parametrize("text, value", SPICE_NUMBERS)
def test_parse_spice_number_table(text, value):
    spice_expr = pytest.importorskip("simple_pi_calculator.core.spice_expr")
    assert spice_expr.parse_spice_number(text) == pytest.approx(value, rel=1e-12)
