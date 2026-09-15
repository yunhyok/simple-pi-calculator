"""DESIGN.md §8.8 — Touchstone v1 reader, S→Z and interpolation."""

from __future__ import annotations

import numpy as np
import pytest

from simple_pi_calculator.core.touchstone import (TwoPortData, interpolate_impedance,
                                                  normalize_s2p_mode, parse_s2p_text, read_s2p,
                                                  s2p_to_impedance)
from simple_pi_calculator.errors import InputError, IssueCollector

Z0 = 50.0
UNIT_SCALE = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


def rlc(f, r=30e-3, l=0.45e-9, c=100e-9):
    w = 2 * np.pi * f
    return r + 1j * w * l + 1 / (1j * w * c)


def fmt_pair(v: complex, fmt: str) -> tuple[float, float]:
    if fmt == "RI":
        return v.real, v.imag
    ang = np.degrees(np.angle(v))
    if fmt == "MA":
        return abs(v), ang
    return 20 * np.log10(abs(v)), ang


def make_s2p(f, s11, s21, s12, s22, unit="HZ", fmt="RI", z0=50.0, option=True, per_line=False,
             extra_header="") -> str:
    lines = ["! synthetic test file", extra_header]
    if option:
        lines.append(f"# {unit} S {fmt} R {z0:g}")
    for i, fi in enumerate(f):
        vals = [fi / UNIT_SCALE[unit]]
        for v in (s11[i], s21[i], s12[i], s22[i]):
            vals.extend(fmt_pair(v, fmt))
        toks = [f"{x:.17g}" for x in vals]
        if per_line:   # split record over 3 physical lines with a trailing comment
            lines.append(" ".join(toks[:3]) + "  ! first part")
            lines.append(" ".join(toks[3:7]))
            lines.append(" ".join(toks[7:]))
        else:
            lines.append(" ".join(toks))
    return "\n".join(lines) + "\n"


def series_sparams(z, z0=Z0):
    s21 = 2 * z0 / (2 * z0 + z)
    s11 = z / (2 * z0 + z)
    return s11, s21, s21.copy(), s11.copy()


def shunt_sparams(z, z0=Z0):
    s21 = 2 * z / (2 * z + z0)
    s11 = -z0 / (2 * z + z0)
    return s11, s21, s21.copy(), s11.copy()


F = np.logspace(3, 9, 61)


@pytest.mark.parametrize("unit", ["HZ", "KHZ", "MHZ", "GHZ"])
@pytest.mark.parametrize("fmt", ["RI", "MA", "DB"])
def test_series_roundtrip(unit, fmt):
    z = rlc(F)
    text = make_s2p(F, *series_sparams(z), unit=unit, fmt=fmt)
    issues = IssueCollector()
    data = parse_s2p_text(text, issues)
    np.testing.assert_allclose(data.f_hz, F, rtol=1e-12)
    assert data.z0 == 50.0
    zr = s2p_to_impedance(data, "series", issues)
    np.testing.assert_allclose(zr, z, rtol=1e-9)
    assert not issues.issues


def test_shunt_roundtrip():
    z = rlc(F)
    data = parse_s2p_text(make_s2p(F, *shunt_sparams(z), fmt="MA"), IssueCollector())
    np.testing.assert_allclose(s2p_to_impedance(data, "shunt", IssueCollector()), z, rtol=1e-9)


def test_wrong_mode_differs():
    z = rlc(F)
    data = parse_s2p_text(make_s2p(F, *series_sparams(z)), IssueCollector())
    i = int(np.argmin(np.abs(F - 1e6)))
    z_series = s2p_to_impedance(data, "series", IssueCollector())[i]
    z_shunt = s2p_to_impedance(data, "shunt", IssueCollector())[i]
    ratio = abs(z_shunt) / abs(z_series)
    assert ratio > 10 or ratio < 0.1


def test_nondefault_z0():
    z = rlc(F)
    data = parse_s2p_text(make_s2p(F, *series_sparams(z, 25.0), z0=25.0), IssueCollector())
    assert data.z0 == 25.0
    np.testing.assert_allclose(s2p_to_impedance(data, "series", IssueCollector()), z, rtol=1e-9)


def test_v1_data_order_11_21_12_22():
    f = np.array([1e6, 2e6])
    s11 = np.array([0.11 + 0.01j, 0.12])
    s21 = np.array([0.21 + 0.02j, 0.22])
    s12 = np.array([0.31 + 0.03j, 0.32])
    s22 = np.array([0.41 + 0.04j, 0.42])
    data = parse_s2p_text(make_s2p(f, s11, s21, s12, s22), IssueCollector())
    np.testing.assert_allclose(data.s[:, 0, 0], s11)
    np.testing.assert_allclose(data.s[:, 1, 0], s21)
    np.testing.assert_allclose(data.s[:, 0, 1], s12)
    np.testing.assert_allclose(data.s[:, 1, 1], s22)
    # averaging of S21 and S12
    zs = s2p_to_impedance(data, "series", IssueCollector())
    savg = (s21 + s12) / 2
    np.testing.assert_allclose(zs, 2 * Z0 * (1 - savg) / savg, rtol=1e-12)


def test_multiline_records_and_comments():
    z = rlc(F)
    text = make_s2p(F, *series_sparams(z), per_line=True, extra_header="! another comment\n\n")
    data = parse_s2p_text(text, IssueCollector())
    np.testing.assert_allclose(s2p_to_impedance(data, "series", IssueCollector()), z, rtol=1e-9)


def test_missing_option_line_defaults():
    f = np.array([1.0, 2.0])   # GHz
    s = np.array([0.5 * np.exp(1j * np.pi / 4), 0.25 + 0j])
    text = "\n".join(f"{fi} {abs(v)} {np.degrees(np.angle(v))} " * 1 + "0 0 0 0 0 0" for fi, v in zip(f, s))
    data = parse_s2p_text(text, IssueCollector())
    np.testing.assert_allclose(data.f_hz, [1e9, 2e9])
    np.testing.assert_allclose(data.s[:, 0, 0], s, rtol=1e-12)
    assert data.z0 == 50.0


def test_only_first_option_line_counts():
    text = "# MHZ S RI R 50\n# HZ S MA R 75\n1 0 0 0 0 0 0 0 0\n2 0 0 0 0 0 0 0 0\n"
    data = parse_s2p_text(text, IssueCollector())
    np.testing.assert_allclose(data.f_hz, [1e6, 2e6])
    assert data.z0 == 50.0


def test_option_tokens_any_order_case():
    text = "# r 75 ri s khz\n1 0 0 0 0 0 0 0 0\n2 0 0 0 0 0 0 0 0\n"
    data = parse_s2p_text(text, IssueCollector())
    assert data.z0 == 75.0
    np.testing.assert_allclose(data.f_hz, [1e3, 2e3])


def expect(text: str, code: str):
    issues = IssueCollector()
    with pytest.raises(InputError) as exc:
        parse_s2p_text(text, issues)
    assert exc.value.issues[0].code == code
    assert code in issues.codes()


def test_version2_rejected():
    expect("[Version] 2.0\n# HZ S RI R 50\n1 0 0 0 0 0 0 0 0\n2 0 0 0 0 0 0 0 0\n", "E_S2P_V2")


def test_y_parameters_rejected():
    expect("# HZ Y RI R 50\n1 0 0 0 0 0 0 0 0\n2 0 0 0 0 0 0 0 0\n", "E_S2P_PARAM")


def test_format_errors():
    expect("# HZ S RI R 50\n1 0 0 0 0 0 0 0 0\n2 0 0 0 0 0 0 0\n", "E_S2P_FORMAT")
    expect("# HZ S RI R 50\n1 0 0 0 0 0 0 0 0\n", "E_S2P_FORMAT")
    expect("# HZ S RI R 50\n1 0 0 0 x 0 0 0 0\n2 0 0 0 0 0 0 0 0\n", "E_S2P_FORMAT")


def test_noise_block_ignored():
    text = ("# GHZ S MA R 50\n1 0.1 0 0.9 0 0.9 0 0.1 0\n2 0.2 0 0.8 0 0.8 0 0.2 0\n"
            "! noise parameters\n1 1.5 0.3 45 0.2\n2 1.8 0.25 60 0.25\n")
    issues = IssueCollector()
    data = parse_s2p_text(text, issues)
    assert data.f_hz.size == 2
    assert "W_S2P_NOISE_IGNORED" in issues.codes()


def test_read_s2p_file(tmp_path):
    z = rlc(F)
    p = tmp_path / "x.s2p"
    p.write_text(make_s2p(F, *series_sparams(z)), encoding="utf-8")
    data = read_s2p(p, IssueCollector())
    assert isinstance(data, TwoPortData)
    assert data.source == str(p)
    np.testing.assert_allclose(s2p_to_impedance(data, "series", IssueCollector()), z, rtol=1e-9)


def test_singular_clamp_warns():
    data = TwoPortData(f_hz=np.array([1.0, 2.0]), s=np.zeros((2, 2, 2), dtype=complex), z0=50.0)
    issues = IssueCollector()
    z = s2p_to_impedance(data, "series", issues)
    assert np.all(np.isfinite(z))
    assert "W_S2P_SINGULAR" in issues.codes()
    data.s[:, 1, 0] = data.s[:, 0, 1] = 1.0
    issues = IssueCollector()
    z = s2p_to_impedance(data, "shunt", issues)
    assert np.all(np.isfinite(z))
    assert "W_S2P_SINGULAR" in issues.codes()


def test_mode_normalisation():
    assert normalize_s2p_mode(None) == "series"
    assert normalize_s2p_mode("") == "series"
    assert normalize_s2p_mode("Series") == "series"
    assert normalize_s2p_mode("ser") == "series"
    assert normalize_s2p_mode("SHUNT") == "shunt"
    assert normalize_s2p_mode("sh") == "shunt"
    with pytest.raises(ValueError):
        normalize_s2p_mode("parallel")


# interpolation ----------------------------------------------------------------------------------
def test_interpolation_capacitor_midpoints():
    fs = np.logspace(3, 9, 25)
    zc = 1 / (1j * 2 * np.pi * fs * 1e-7)
    fmid = np.sqrt(fs[:-1] * fs[1:])
    issues = IssueCollector()
    z = interpolate_impedance(fs, zc, fmid, issues, "cap")
    np.testing.assert_allclose(z, 1 / (1j * 2 * np.pi * fmid * 1e-7), rtol=1e-3)
    np.testing.assert_allclose(interpolate_impedance(fs, zc, fs, issues, "cap"), zc, rtol=1e-12)
    assert not issues.issues


def test_interpolation_rlc_through_resonance():
    fs = np.logspace(3, 9, 601)
    z = rlc(fs)
    fd = np.logspace(3.001, 8.999, 333)
    zi = interpolate_impedance(fs, z, fd, IssueCollector(), "rlc")
    np.testing.assert_allclose(np.abs(zi), np.abs(rlc(fd)), rtol=2e-2)


def test_low_extrapolation_capacitor_exact():
    fs = np.logspace(4, 8, 9)
    c, r = 1e-7, 0.02
    z = r + 1 / (1j * 2 * np.pi * fs * c)
    fd = np.array([1e3, 3e3, 9.99e3])
    issues = IssueCollector()
    zi = interpolate_impedance(fs, z, fd, issues, "cap")
    np.testing.assert_allclose(zi, r + 1 / (1j * 2 * np.pi * fd * c), rtol=1e-9)
    assert "W_S2P_EXTRAP_LOW" in issues.codes()
    assert "W_S2P_EXTRAP_HIGH" not in issues.codes()


def test_high_extrapolation_inductor_exact():
    fs = np.logspace(4, 8, 9)
    l, r = 1e-9, 0.01
    z = r + 1j * 2 * np.pi * fs * l
    fd = np.array([2e8, 1e9, 2e10])
    issues = IssueCollector()
    zi = interpolate_impedance(fs, z, fd, issues, "ind")
    np.testing.assert_allclose(zi, r + 1j * 2 * np.pi * fd * l, rtol=1e-9)
    assert "W_S2P_EXTRAP_HIGH" in issues.codes()


def test_extrapolation_hold_constant():
    fs = np.array([1e4, 1e5, 1e6])
    z = np.array([1 + 1j, 2 + 0j, 3 - 1j])   # Im Z1 > 0 and Im Zn < 0 → hold
    zi = interpolate_impedance(fs, z, np.array([1e3, 1e7]), IssueCollector(), "x")
    np.testing.assert_allclose(zi, [1 + 1j, 3 - 1j])


def test_edge_tolerance_no_warning():
    fs = np.logspace(3, 9, 7)
    z = rlc(fs)
    fd = np.array([1e3 * (1 - 1e-12), 1e9 * (1 + 1e-12)])
    issues = IssueCollector()
    interpolate_impedance(fs, z, fd, issues, "x")
    assert not issues.issues
