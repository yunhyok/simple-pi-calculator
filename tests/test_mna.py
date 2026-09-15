"""DESIGN.md §8.7 — AC MNA solver."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core import mna
from simple_pi_calculator.core.mna import (MnaSingularError, build_mna, impedance_from_elements,
                                           impedance_two_terminal, solve_impedance)
from simple_pi_calculator.core.spice_parser import Element, parse_spice_file, parse_spice_text
from simple_pi_calculator.errors import IssueCollector

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DATA = Path(__file__).resolve().parent / "data"


def net_of(text: str):
    return parse_spice_text(text, None, IssueCollector())


def w(f):
    return 2 * np.pi * np.asarray(f, dtype=float)


def test_series_rlc_vs_analytic():
    net = net_of(".SUBCKT C 1 2\nR1 1 11 30m\nL1 11 12 0.45nH\nC1 12 2 100nF\n.ENDS\n")
    f = np.logspace(3, 9, 601)
    z = impedance_two_terminal(net, f)
    za = 30e-3 + 1j * w(f) * 0.45e-9 + 1 / (1j * w(f) * 100e-9)
    assert z.shape == f.shape and z.dtype == np.complex128
    # gmin = 1e-12 S on each of the 3 non-reference nodes shunts |Z_C| = 1.6 kΩ at 1 kHz → ≈ 5e-9 rel
    np.testing.assert_allclose(z, za, rtol=1e-8)
    np.testing.assert_allclose(impedance_two_terminal(net, f, gmin=0.0), za, rtol=1e-12)


def test_parallel_rlc_resonance():
    net = net_of(".SUBCKT P 1 2\nR1 1 2 1\nL1 1 2 1u\nC1 1 2 1n\n.ENDS\n")
    f0 = 1 / (2 * np.pi * np.sqrt(1e-6 * 1e-9))
    assert f0 == pytest.approx(5.0329e6, rel=1e-4)
    z = impedance_two_terminal(net, np.array([f0]))
    assert abs(z[0]) == pytest.approx(1.0, rel=1e-6)


@pytest.mark.parametrize("line", ["R1 1 2 0", "L1 1 2 0"])
def test_zero_branches(line):
    net = net_of(f".SUBCKT S 1 2\n{line}\n.ENDS\n")
    z = impedance_two_terminal(net, np.logspace(3, 9, 7))
    assert np.all(np.abs(z) < 1e-12)


def test_zero_r_and_l_in_series_with_rc():
    net = net_of(".SUBCKT S 1 2\nR0 1 a 0\nL0 a b 0\nR1 b c 2\nC1 c 2 1n\n.ENDS\n")
    f = np.logspace(3, 9, 13)
    np.testing.assert_allclose(impedance_two_terminal(net, f, gmin=0.0), 2 + 1 / (1j * w(f) * 1e-9),
                               rtol=1e-12)


def test_zero_capacitor_ignored_and_c_only_node():
    net = net_of(".SUBCKT S 1 2\nR1 1 2 5\nC0 1 x 0\nC1 1 y 1p\nC2 y 2 1p\n.ENDS\n")
    f = np.array([1e3, 1e6, 1e9])
    zc = 1 / (1j * w(f) * 0.5e-12)
    za = 1 / (1 / 5 + 1 / zc)
    np.testing.assert_allclose(impedance_two_terminal(net, f), za, rtol=1e-9)
    system = build_mna(net.elements, net.pin1, net.pin2)
    assert "x" not in system.node_index


@pytest.mark.parametrize("l2_nodes,l_eq", [("m 2", 3e-9), ("2 m", 1e-9)])
def test_coupled_inductors(l2_nodes, l_eq):
    net = net_of(f".SUBCKT S 1 2\nL1 1 m 1n\nL2 {l2_nodes} 1n\nK1 L1 L2 0.5\n.ENDS\n")
    f = np.array([1e6])
    z = impedance_two_terminal(net, f)
    assert (z[0].imag / w(f)[0]) == pytest.approx(l_eq, rel=1e-9)
    assert abs(z[0].real) < 1e-9 * abs(z[0])


def test_three_coupled_inductors_matrix():
    # L_eq of series-aiding chain = sum over full inductance matrix
    net = net_of(".SUBCKT S 1 2\nL1 1 a 1n\nL2 a b 2n\nL3 b 2 3n\nK1 L1 L2 L3 0.3\n.ENDS\n")
    lv = np.array([1e-9, 2e-9, 3e-9])
    lm = 0.3 * np.sqrt(np.outer(lv, lv))
    np.fill_diagonal(lm, lv)
    z = impedance_two_terminal(net, np.array([1e7]))
    assert z[0].imag / w(1e7) == pytest.approx(lm.sum(), rel=1e-9)


def test_bundled_10uF_srf_and_esr():
    net = parse_spice_file(EXAMPLES / "cap_0603_10uF.mod", None, IssueCollector())
    f = np.logspace(np.log10(1e6), np.log10(5e6), 20001)
    z = impedance_two_terminal(net, f)
    i = int(np.argmin(np.abs(z)))
    assert f[i] == pytest.approx(2.2508e6, rel=5e-3)
    assert f[i] == pytest.approx(2.251e6, rel=1e-3)
    assert abs(z[i]) == pytest.approx(5.0e-3, rel=1e-2)


def test_batch_vs_loop_equal(monkeypatch):
    net = parse_spice_file(DATA / "vendor_style_0402_104.mod", None, IssueCollector())
    f = np.logspace(3, 9.5, 97)
    zb = impedance_two_terminal(net, f)
    zl = np.array([impedance_two_terminal(net, np.array([fi]))[0] for fi in f])
    np.testing.assert_allclose(zb, zl, rtol=1e-12)
    monkeypatch.setattr(mna, "BATCH_MAX_UNKNOWNS", 0)
    zf = impedance_two_terminal(net, f)
    np.testing.assert_allclose(zb, zf, rtol=1e-12)


def test_vendor_style_ladder_vs_independent_formula():
    """Mesh-current formulation of the coupled R-L ladder, composed by hand."""
    net = parse_spice_file(DATA / "vendor_style_0402_104.mod", None, IssueCollector())
    f = np.logspace(3, 9.5, 131)
    om = w(f)
    la, lb, lc = 180e-12, 144e-12, 90e-12
    rb, rc, k = 25e-3, 60e-3, 0.3
    mab, mac, mbc = k * np.sqrt(la * lb), k * np.sqrt(la * lc), k * np.sqrt(lb * lc)
    z_ladder = np.empty(f.size, dtype=complex)
    for n, wn in enumerate(om):
        j = 1j * wn
        # unknowns i_b, i_c with I = 1:
        # j(Lb i_b + Mab + Mbc i_c) = Rb (1 - i_b);  j(Lc i_c + Mac + Mbc i_b) = Rc (1 - i_c)
        a = np.array([[j * lb + rb, j * mbc], [j * mbc, j * lc + rc]])
        rhs = np.array([rb - j * mab, rc - j * mac])
        ib, ic = np.linalg.solve(a, rhs)
        z_ladder[n] = j * (la + mab * ib + mac * ic) + rb * (1 - ib) + rc * (1 - ic)
    zc1 = 1 / (1j * om * 98e-9) + 4.5e-3
    zc2 = 1 / (1j * om * 2e-9) + 1.2
    z_series = z_ladder + 12e-3 + 1 / (1 / zc1 + 1 / zc2)
    za = 1 / (1 / z_series + 1 / 5e9)
    np.testing.assert_allclose(impedance_two_terminal(net, f), za, rtol=1e-8)


def test_singular_detection():
    # two parallel ideal shorts (L = 0) form a loop of zero-impedance branches → singular matrix
    elements = [
        Element("L", "l1", ("1", "2"), 0.0),
        Element("L", "l2", ("1", "2"), 0.0),
    ]
    with pytest.raises(MnaSingularError):
        impedance_from_elements(elements, "1", "2", np.array([1e3, 1e6]))


def test_nonfinite_check(monkeypatch):
    net = net_of(".SUBCKT S 1 2\nR1 1 2 1\n.ENDS\n")
    system = build_mna(net.elements, net.pin1, net.pin2)
    system.a0[0, 0] = np.inf
    with pytest.raises(MnaSingularError):
        solve_impedance(system, np.array([1e6]))


def test_empty_frequency_array():
    net = net_of(".SUBCKT S 1 2\nR1 1 2 1\n.ENDS\n")
    assert impedance_two_terminal(net, np.array([])).shape == (0,)
