"""DESIGN.md §8.3 — cavity model."""

from __future__ import annotations

import math

import numpy as np
import pytest

from simple_pi_calculator.constants import EPS0, MU0
from simple_pi_calculator.core.cavity import (CavityModel, cluster_port_width,
                                              cluster_via_positions, conductor_loss_factor,
                                              resonance_frequency, surface_impedance,
                                              wavenumber_sq)
from simple_pi_calculator.core.pdn import SingularReductionError, reduce_ports
from simple_pi_calculator.core.placement import DecapGroupGeom, place_ports
from simple_pi_calculator.core.stackup import Layer, PlanePair
from simple_pi_calculator.errors import IssueCollector

MM = 1e-3


def make_pair(d=0.1 * MM, er=4.0, tand=0.0, sigma=math.inf, t=35e-6) -> PlanePair:
    metal = Layer(1, "P", t, sigma, None, None)
    gnd = Layer(3, "G", t, sigma, None, None)
    return PlanePair(pwr_layer=metal, gnd_layer=gnd, d_m=d, er_eff=er, tand_eff=tand,
                     d_dielectric_m=d)


def w_of(drill):
    return cluster_port_width(1, drill, 1 * MM)


def loop_l(z, f):
    return (z[:, 0, 0] + z[:, 1, 1] - 2 * z[:, 0, 1]).imag / (2 * math.pi * f)


# 1 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("f", [1e3, 1e4])
def test_dc_capacitive_limit_ideal(f):
    pair = make_pair()
    a = b = 20 * MM
    cav = CavityModel(a, b, pair, [[10 * MM, 10 * MM]], [w_of(0.2 * MM)], np.array([1e3, 1e9]))
    z = cav.z_matrix(np.array([f]))[0, 0, 0]
    c = pair.plane_capacitance(a, b)
    assert c == pytest.approx(141.667e-12, rel=1e-5)
    val = z * 1j * 2 * math.pi * f * c
    assert abs(val - 1) < 1e-4


def test_dc_capacitive_limit_lossy_dielectric():
    pair = make_pair(tand=0.02)
    a = b = 20 * MM
    f = 1e3
    cav = CavityModel(a, b, pair, [[10 * MM, 10 * MM]], [w_of(0.2 * MM)], np.array([1e3, 1e9]))
    z = cav.z_matrix(np.array([f]))[0, 0, 0]
    c = pair.plane_capacitance(a, b)
    expect = 1 / (1j * 2 * math.pi * f * c * (1 - 0.02j))
    assert abs(z / expect - 1) < 1e-3


# 2 -------------------------------------------------------------------------------------------
def test_dc_limit_finite_conductors():
    pair = make_pair(sigma=5.8e7, t=35e-6)
    a = b = 20 * MM
    f = 1e3
    cav = CavityModel(a, b, pair, [[10 * MM, 10 * MM]], [w_of(0.2 * MM)], np.array([1e3, 1e9]))
    z = cav.z_matrix(np.array([f]))[0, 0, 0]
    c = pair.plane_capacitance(a, b)
    assert abs(z * 1j * 2 * math.pi * f * c - 1) < 1e-3
    # Γ_c diverges at DC for finite σt, yet the (0,0) term stays capacitive
    assert abs(conductor_loss_factor(np.array([f]), pair)[0]) > 100


# 3, 4 ----------------------------------------------------------------------------------------
def _peaks(f, mag):
    i = np.nonzero((mag[1:-1] > mag[:-2]) & (mag[1:-1] > mag[2:]))[0] + 1
    return f[i]


def test_resonances_and_nodal_line():
    a, b = 100 * MM, 80 * MM
    pair = make_pair(tand=0.001)
    f = np.arange(600e6, 1000e6 + 1, 0.5e6)
    assert resonance_frequency(1, 0, a, b, 4.0) == pytest.approx(749.481e6, rel=1e-6)
    assert resonance_frequency(0, 1, a, b, 4.0) == pytest.approx(936.851e6, rel=1e-6)
    w = w_of(0.2 * MM)
    cav = CavityModel(a, b, pair, [[2 * MM, 2 * MM]], [w], f)
    mag = np.abs(cav.z_matrix(f)[:, 0, 0])
    pk = _peaks(f, mag)
    assert np.any(np.abs(pk - 749.5e6) <= 1.0e6)
    assert np.any(np.abs(pk - 937.0e6) <= 1.0e6)

    cav2 = CavityModel(a, b, pair, [[a / 2, 2 * MM]], [w], f)
    mag2 = np.abs(cav2.z_matrix(f)[:, 0, 0])
    pk2 = _peaks(f, mag2)
    assert not np.any(np.abs(pk2 - 749.5e6) <= 5e6)
    assert np.any(np.abs(pk2 - 937.0e6) <= 1.0e6)
    i749 = np.argmin(np.abs(f - 749.5e6))
    i700 = np.argmin(np.abs(f - 700e6))
    assert mag2[i749] < 1.5 * mag2[i700]


# 5, 6 ----------------------------------------------------------------------------------------
def test_loop_inductance_image_theory():
    pair = make_pair()
    D = 0.5 * MM
    f = 1e6
    cav = CavityModel(100 * MM, 100 * MM, pair, [[50 * MM, 50 * MM], [60 * MM, 50 * MM]],
                      [w_of(D)] * 2, np.array([1e5, 1e9]))
    L = loop_l(cav.z_matrix(np.array([f])), f)[0]
    ref = MU0 * 0.1 * MM / math.pi * math.log(10 / 0.25)
    assert ref == pytest.approx(0.14756e-9, rel=1e-4)
    assert L == pytest.approx(ref, rel=0.02)
    assert L == pytest.approx(0.1482e-9, rel=2e-3)  # prototype


def test_internal_inductance_thin_planes():
    pair = make_pair(sigma=5.8e7, t=35e-6)
    D = 0.5 * MM
    f = 1e6
    cav = CavityModel(100 * MM, 100 * MM, pair, [[50 * MM, 50 * MM], [60 * MM, 50 * MM]],
                      [w_of(D)] * 2, np.array([1e5, 1e9]))
    L = loop_l(cav.z_matrix(np.array([f])), f)[0]
    ref = MU0 * (0.1 * MM + 2 * 35e-6 / 3) / math.pi * math.log(10 / 0.25)
    assert ref == pytest.approx(0.1820e-9, rel=1e-3)
    assert L == pytest.approx(ref, rel=0.02)
    assert L == pytest.approx(0.1826e-9, rel=2e-3)  # prototype


# 7, 8 ----------------------------------------------------------------------------------------
def _three_port(**kw):
    pair = make_pair(tand=0.02, sigma=5.8e7, t=35e-6)
    ports = [[5 * MM, 4 * MM], [12 * MM, 15 * MM], [17 * MM, 9 * MM]]
    return CavityModel(20 * MM, 20 * MM, pair, ports, [w_of(0.2 * MM)] * 3,
                       np.array([1e5, 1e9]), **kw)


def test_symmetry_reciprocity():
    cav = _three_port()
    z = cav.z_matrix(np.geomspace(1e3, 1e9, 7))
    assert np.all(np.abs(z - np.transpose(z, (0, 2, 1))) <= 1e-12 * np.abs(z))


def test_static_split_accuracy():
    cav = _three_port()
    f = np.array([1e6, 1e9])
    z = cav.z_matrix(f)
    zb = cav.z_matrix_bruteforce(f)
    assert np.max(np.abs(z - zb) / np.abs(zb)) < 1e-3
    assert cav.n_dynamic >= 1 and cav.n_dynamic < (cav.M + 1) * (cav.N + 1)


# 9 -------------------------------------------------------------------------------------------
def test_mode_count_rules():
    f = np.geomspace(1e5, 1e9, 50)
    pair = make_pair(sigma=5.8e7)
    w = w_of(0.2 * MM)
    assert w == pytest.approx(0.22369 * MM, rel=1e-5)
    cav = CavityModel(20 * MM, 20 * MM, pair, [[10 * MM, 10 * MM]], [w], f)
    assert (cav.M, cav.N) == (269, 269) and not cav.capped
    # K_split from lossy k_max (F5): |k²| exceeds k0² with conductor loss
    k0 = 2 * math.pi * 1e9 * 2 / 299792458.0
    assert cav.k_max > k0
    assert cav.k_max == pytest.approx(math.sqrt(np.max(np.abs(wavenumber_sq(f, pair)))))
    cav = CavityModel(100 * MM, 100 * MM, pair, [[10 * MM, 10 * MM]], [w], f)
    assert cav.M == 1342
    cav = CavityModel(400 * MM, 400 * MM, make_pair(), [[10 * MM, 10 * MM]], [w_of(0.1 * MM)],
                      np.array([1e5, 1e9]))
    assert cav.M == 1500 and cav.N == 1500 and cav.capped


# 10 ------------------------------------------------------------------------------------------
def test_surface_impedance_limits():
    sigma = 5.8e7
    f = 1e9
    zs = surface_impedance(np.array([f]), sigma, 1e-3)[0]
    delta = math.sqrt(2 / (2 * math.pi * f * MU0 * sigma))
    assert zs == pytest.approx((1 + 1j) / (sigma * delta), rel=1e-6)
    zs = surface_impedance(np.array([1e3]), sigma, 35e-6)[0]
    assert zs.real == pytest.approx(4.926e-4, rel=1e-3)
    assert surface_impedance(np.array([1e6]), math.inf, 35e-6)[0] == 0
    # skin depth values of §2.4.2
    assert math.sqrt(2 / (2 * math.pi * 1e6 * MU0 * sigma)) == pytest.approx(66.09e-6, rel=1e-4)


# 11 ------------------------------------------------------------------------------------------
def test_cluster_port_width_values():
    vals = [cluster_port_width(n, 0.2 * MM, 1 * MM) / MM for n in (1, 2, 4, 9)]
    assert vals == pytest.approx([0.223689, 0.841204, 1.778930, 3.451317], rel=1e-5)
    xy = cluster_via_positions(4, math.sqrt(2) * MM)
    assert np.allclose(np.sort(np.abs(xy.ravel())), 0.70711 * MM, rtol=1e-5)


def test_cluster_port_loop_vs_explicit_ports():
    pair = make_pair()
    a, b = 30 * MM, 14 * MM
    f = 1e6
    w1 = w_of(0.2 * MM)
    fe = np.array([1e5, 1e9])
    off = cluster_via_positions(4, math.sqrt(2) * MM)
    ports = [[15 * MM + dx, 2 * MM + dy] for dx, dy in off] + [[15 * MM, 12 * MM]]
    cav = CavityModel(a, b, pair, ports, [w1] * 5, fe)
    z = cav.z_matrix(np.array([f]))
    # shorted decap port 4: tie ports 0..3 in parallel (equal voltage), loop impedance seen
    zp = z[0, :4, :4] - np.outer(z[0, :4, 4], z[0, 4, :4]) / z[0, 4, 4]
    y = np.linalg.inv(zp)
    L_explicit = (1 / y.sum()).imag / (2 * math.pi * f)
    assert L_explicit == pytest.approx(0.16653e-9, rel=5e-3)

    wc = cluster_port_width(4, 0.2 * MM, 1 * MM)
    cav2 = CavityModel(a, b, pair, [[15 * MM, 2 * MM], [15 * MM, 12 * MM]], [wc, w1], fe)
    L_cluster = loop_l(cav2.z_matrix(np.array([f])), f)[0]
    assert L_cluster == pytest.approx(0.16760e-9, rel=5e-3)
    assert L_cluster == pytest.approx(L_explicit, rel=0.02)


# 12 ------------------------------------------------------------------------------------------
def test_per_port_widths():
    pair = make_pair(tand=0.01)
    wa, wb = 0.3 * MM, 0.9 * MM
    xy = [[5 * MM, 5 * MM], [14 * MM, 11 * MM]]
    fe = np.array([1e5, 1e9])
    settings_f = np.array([1e6, 3e8])
    from simple_pi_calculator.core.cavity import ModeSettings
    # min_modes dominates so that the three models share identical M, N (widths drive the counts
    # otherwise)
    ms = ModeSettings(min_modes=300)
    cav = CavityModel(20 * MM, 20 * MM, pair, xy, [wa, wb], fe, ms)
    assert (cav.M, cav.N) == (300, 300)
    z = cav.z_matrix(settings_f)
    assert np.all(np.abs(z - np.transpose(z, (0, 2, 1))) <= 1e-12 * np.abs(z))
    za = CavityModel(20 * MM, 20 * MM, pair, xy, [wa, wa], fe, ms).z_matrix(settings_f)
    zb = CavityModel(20 * MM, 20 * MM, pair, xy, [wb, wb], fe, ms).z_matrix(settings_f)
    assert z[:, 0, 0] == pytest.approx(za[:, 0, 0], rel=1e-12)
    assert z[:, 1, 1] == pytest.approx(zb[:, 1, 1], rel=1e-12)


# 13 ------------------------------------------------------------------------------------------
def test_singular_check_coincident_ports():
    issues = IssueCollector()
    w = w_of(0.2 * MM)
    # two rows at (almost) the same distance → same x cells, overlapping footprints
    pl = place_ports(20 * MM, [DecapGroupGeom(1, 5.0 * MM), DecapGroupGeom(1, 5.0 * MM)], w, w,
                     issues, "PWR:T")
    assert "W_PORT_OVERLAP" in issues.codes()
    pair = make_pair()
    f = np.array([1e6, 1e7])
    cav = CavityModel(pl.width_m, pl.height_m, pair, pl.xy_m, pl.port_widths_m, f)
    z = cav.z_matrix(f)
    with pytest.raises(SingularReductionError):
        reduce_ports(z, np.zeros((f.size, 2), dtype=complex))


def test_wavenumber_lossless_matches_k0():
    pair = make_pair()
    f = np.array([1e8])
    k2 = wavenumber_sq(f, pair)[0]
    k0 = 2 * math.pi * 1e8 * math.sqrt(4.0 * MU0 * EPS0)
    assert k2.real == pytest.approx(k0 ** 2, rel=1e-12)
