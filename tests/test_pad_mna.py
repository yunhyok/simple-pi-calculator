"""Review v0.2 (docs/REVIEW-v0.2.md): the N_pad model of §2.8 against a brute-force modified nodal
analysis with explicit pad, via and capacitor branches.

The circuit is built independently of the Schur/parallel-pad formulas: unknowns are all cavity
port currents and voltages, the node between each decap via set and its capacitors, one current
per physical capacitor and the voltage of the common pad node driven by 1 A. Only the cavity
port matrix Z_cav comes from the engine (verified separately against the reference cavity sum).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from simple_pi_calculator.core.cavity import CavityModel, ModeSettings, cluster_port_width
from simple_pi_calculator.core.pdn import DecapGroup, PwrSpec, compute_pwr
from simple_pi_calculator.core.placement import DecapGroupGeom, place_ports
from simple_pi_calculator.core.stackup import Layer, Stackup, derive_plane_pair
from simple_pi_calculator.core.via import ViaSettings, _pair_impedance_geom, via_geometry
from simple_pi_calculator.errors import IssueCollector

MM = 1e-3


class Rlc:
    def __init__(self, r, ind, c):
        self.r, self.ind, self.c = r, ind, c
        self.label = f"RLC {c:g}"

    def impedance(self, f, issues=None):
        w = 2 * math.pi * np.asarray(f, dtype=float)
        return self.r + 1j * w * self.ind + 1 / (1j * w * self.c)


def stackup():
    rows = [(1, "TOP", 0.035, 5.8e7, None, None), (2, "PP", 0.1, None, 4.0, 0.01),
            (3, "GND", 0.035, 5.8e7, None, None), (4, "CORE", 0.1, None, 4.0, 0.01),
            (5, "PWR", 0.035, 5.8e7, None, None)]
    return Stackup(tuple(Layer(n, nm, t * MM, s, dk, df) for n, nm, t, s, dk, df in rows))


def brute_force(z_cav, f, n_pad, placement, models, z_via_dec, z_via_pad, l_mount):
    F, P, _ = z_cav.shape
    K = P - n_pad
    caps = placement.caps_per_port
    gi = placement.group_index
    n_cap = int(caps.sum())
    n = 2 * P + K + n_cap + 1
    out = np.empty(F, dtype=complex)
    for fi in range(F):
        A = np.zeros((n, n), dtype=complex)
        b = np.zeros(n, dtype=complex)
        iv, ia, ij, ic = P, 2 * P, 2 * P + K, n - 1
        r = 0
        for p in range(P):  # cavity: V_p − Σ_q Z_pq I_q = 0 (I_q into the cavity port)
            A[r, iv + p] = 1.0
            A[r, :P] -= z_cav[fi, p]
            r += 1
        c0 = 0
        for k in range(K):
            p = n_pad + k
            zc = models[gi[k]].impedance(f[fi:fi + 1])[0] + 1j * 2 * math.pi * f[fi] * l_mount
            A[r, iv + p], A[r, ia + k], A[r, p] = 1.0, -1.0, z_via_dec[fi]  # via set
            r += 1
            A[r, p] = 1.0  # KCL at the capacitor node: −I_p = Σ J_c
            A[r, ij + c0:ij + c0 + caps[k]] = 1.0
            r += 1
            for c in range(caps[k]):  # each physical capacitor
                A[r, ia + k], A[r, ij + c0 + c] = 1.0, -zc
                r += 1
            c0 += caps[k]
        for p in range(n_pad):  # common node → own via set → pad port
            A[r, ic], A[r, iv + p], A[r, p] = 1.0, -1.0, -z_via_pad[fi]
            r += 1
        A[r, :n_pad] = 1.0  # 1 A into the common node
        b[r] = 1.0
        r += 1
        assert r == n
        out[fi] = np.linalg.solve(A, b)[ic]
    return out


@pytest.mark.parametrize("n_pads", [1, 2, 5])
@pytest.mark.parametrize("pad_vias,vias_per_pad", [(1, 1), (3, 2)])
def test_z_pad_matches_brute_force_mna(n_pads, pad_vias, vias_per_pad):
    st = stackup()
    vias = ViaSettings(drill_diameter_m=0.2 * MM, antipad_diameter_m=0.5 * MM, via_pitch_m=1 * MM,
                       vias_per_pad=vias_per_pad, pad_via_count=pad_vias,
                       mounting_inductance_h=0.3e-9)
    f = np.geomspace(1e5, 2e9, 17)
    models = [Rlc(0.03, 0.45e-9, 100e-9), Rlc(0.005, 0.5e-9, 10e-6)]
    groups = [DecapGroup("P", models[0], 5, 6 * MM, dummy=True),
              DecapGroup("P", models[1], 3, 11 * MM)]
    pwr = PwrSpec("P", 5, 3, 25 * MM, n_pads=n_pads)
    res = compute_pwr(st, pwr, groups, vias, f, [], True, IssueCollector(), workers=3)

    # independent reconstruction of the inputs of the circuit
    w_pad = cluster_port_width(pad_vias, vias.drill_diameter_m, vias.via_pitch_m)
    w_dec = cluster_port_width(vias_per_pad, vias.drill_diameter_m, vias.via_pitch_m)
    pl = place_ports(pwr.width_m, [DecapGroupGeom(g.count, g.distance_m, g.dummy)
                                   for g in groups], w_dec, w_pad, IssueCollector(), "P",
                     n_pads=n_pads)
    assert np.array_equal(pl.xy_m, res.placement.xy_m)
    assert res.placement.port_widths_m[:n_pads] == pytest.approx([w_pad] * n_pads, rel=1e-15)
    pair = derive_plane_pair(st, 5, 3, IssueCollector(), None)
    z_cav = CavityModel(pl.width_m, pl.height_m, pair, pl.xy_m, pl.port_widths_m, f,
                        ModeSettings()).z_matrix(f)
    z_pair = _pair_impedance_geom(f, via_geometry(st, 5, 3, IssueCollector()), vias)
    ref = brute_force(z_cav, f, n_pads, pl, models, z_pair / vias_per_pad, z_pair / pad_vias,
                      vias.mounting_inductance_h)
    assert np.max(np.abs(res.z_pad - ref) / np.abs(ref)) <= 1e-9
    # plane only: the same circuit without decap branches
    ref_plane = np.array([1 / np.sum(np.linalg.solve(z_cav[i, :n_pads, :n_pads]
                                                     + np.eye(n_pads) * z_pair[i] / pad_vias,
                                                     np.ones(n_pads))) for i in range(f.size)])
    assert np.max(np.abs(res.z_plane_only - ref_plane) / np.abs(ref_plane)) <= 1e-12
