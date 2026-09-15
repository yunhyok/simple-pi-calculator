"""Via-pair loop impedance above the planes (DESIGN.md §2.6.1–§2.6.4).

Both decaps and the PAD are on the Top side. One via pair = PWR via + GND via at pitch s_v. A decap via set has
``vias_per_pad`` vias on each of the two decap pads, i.e. that many PWR/GND pairs in parallel.
Default model ``pair``: image partial-inductance pair of length h_near (to the component-side face
of the nearer plane) plus a coaxial anti-pad segment through the nearer plane's thickness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from simple_pi_calculator.constants import MU0, VIA_ZERO_LENGTH_M
from simple_pi_calculator.core.stackup import Stackup
from simple_pi_calculator.errors import InputError, IssueCollector

__all__ = [
    "ViaSettings",
    "ViaGeometry",
    "validate_via_settings",
    "via_geometry",
    "partial_mutual_inductance",
    "pair_inductance",
    "antipad_inductance",
    "coax_inductance",
    "goldfarb_pucel_inductance",
    "loop_inductance",
    "via_resistance",
    "via_pair_impedance",
    "decap_via_impedance",
    "pad_via_impedance",
]

_MU0_2PI = MU0 / (2.0 * math.pi)


@dataclass(frozen=True)
class ViaSettings:
    """Common via settings (§1.3, §4.7), SI units."""

    drill_diameter_m: float
    antipad_diameter_m: float
    via_pitch_m: float = 1.0e-3  # s_v, PWR–GND via centre spacing
    vias_per_pad: int = 1  # n_pad: parallel vias on EACH decap pad (n PWR + n GND vias per set)
    pad_via_count: int = 1  # PWR/GND via pairs at the observation PAD
    model: Literal["pair", "goldfarb_pucel", "coax"] = "pair"
    plating_thickness_m: float = 25e-6
    conductivity: float = 5.8e7
    mounting_inductance_h: float = 0.0  # per capacitor, applied in pdn (§2.6.5)

    @property
    def n_pair_dec(self) -> int:
        """PWR/GND via pairs of one decap via set = vias per decap pad (§2.6.4)."""
        return int(self.vias_per_pad)

    @property
    def n_pad(self) -> int:
        return int(self.pad_via_count)


@dataclass(frozen=True)
class ViaGeometry:
    """Via-loop lengths for Top mounting (§2.6.1)."""

    nearer_layer: int
    h_near_m: float
    t_near_m: float
    h_r_m: float  # 2·h_near + t_near (or t_near when h_near < 1 µm)

    @property
    def zero_length(self) -> bool:
        return self.h_near_m < VIA_ZERO_LENGTH_M


def validate_via_settings(vs: ViaSettings, issues: IssueCollector,
                          source: str | None = "Vias") -> None:
    """Validation of §2.6.2, §2.6.4, §4.7. Adds issues; does not raise."""
    d = vs.drill_diameter_m
    if not (d > 0.0):
        issues.error("E_VIA_DRILL", f"Drill diameter must be > 0 (got {d * 1e3:g} mm).", source)
        return
    if not (vs.antipad_diameter_m > d):
        issues.error("E_VIA_ANTIPAD",
                     f"Anti-pad diameter {vs.antipad_diameter_m * 1e3:g} mm must be larger than the "
                     f"drill diameter {d * 1e3:g} mm.", source)
    if not (vs.via_pitch_m > d):
        issues.error("E_VIA_PITCH",
                     f"Via pitch {vs.via_pitch_m * 1e3:g} mm must be larger than the drill "
                     f"diameter {d * 1e3:g} mm.", source)
    elif vs.via_pitch_m < 0.5 * (d + vs.antipad_diameter_m):
        issues.warning("W_VIA_PITCH_SMALL",
                       f"Via pitch {vs.via_pitch_m * 1e3:g} mm is smaller than (drill + anti-pad)/2; "
                       "the GND via cuts into the PWR via's anti-pad.", source)
    if int(vs.vias_per_pad) != vs.vias_per_pad or vs.vias_per_pad < 1:
        issues.error("E_VIA_COUNT",
                     f"Vias per decap pad must be an integer ≥ 1 (got {vs.vias_per_pad}).", source)
    if int(vs.pad_via_count) != vs.pad_via_count or vs.pad_via_count < 1:
        issues.error("E_VIA_COUNT", f"PAD via count must be ≥ 1 (got {vs.pad_via_count}).", source)
    if vs.model not in ("pair", "goldfarb_pucel", "coax"):
        issues.error("E_VIA_MODEL", f"Unknown via model {vs.model!r}.", source)
    if not (vs.plating_thickness_m > 0.0):
        issues.error("E_VIA_PLATING", "Plating thickness must be > 0.", source)
    if not (vs.conductivity > 0.0):
        issues.error("E_VIA_SIGMA", "Via conductivity must be > 0.", source)
    if not (vs.mounting_inductance_h >= 0.0):
        issues.error("E_VIA_MOUNT_L", "Mounting inductance must be ≥ 0.", source)


def via_geometry(stackup: Stackup, pwr_layer: int, gnd_layer: int,
                 issues: IssueCollector, source: str | None = None) -> ViaGeometry:
    """h_near = z_top(nearer plane), t_near, h_R = 2·h_near + t_near (§2.6.1)."""
    nearer = min(int(pwr_layer), int(gnd_layer))
    h_near = stackup.z_top(nearer)
    t_near = stackup.by_number(nearer).thickness_m
    if h_near < VIA_ZERO_LENGTH_M:
        issues.warning("W_VIA_ZERO_LENGTH",
                       f"The nearer plane (layer {nearer}) is the Top metal: the via pair above the "
                       "planes has zero length (only the anti-pad segment is kept).", source)
        return ViaGeometry(nearer, h_near, t_near, t_near)
    return ViaGeometry(nearer, h_near, t_near, 2.0 * h_near + t_near)


def partial_mutual_inductance(length_m: float, distance_m: float) -> float:
    """M_p(l, x) of two parallel filaments (x = r0: partial self of a tube) [H] (§2.6.2)."""
    l = float(length_m)
    x = float(distance_m)
    if l <= 0.0:
        return 0.0
    root = math.sqrt(l * l + x * x)
    return _MU0_2PI * (l * math.log((l + root) / x) - root + x)


def pair_inductance(h_m: float, pitch_m: float, drill_d_m: float) -> float:
    """L_pair(h, s_v) = M_p(2h, r0) − M_p(2h, s_v) [H] (§2.6.2)."""
    r0 = 0.5 * drill_d_m
    return partial_mutual_inductance(2.0 * h_m, r0) - partial_mutual_inductance(2.0 * h_m, pitch_m)


def antipad_inductance(t_m: float, drill_d_m: float, antipad_d_m: float) -> float:
    """L_ap = (μ0/2π)·t·ln(r_ap/r0) [H] (§2.6.2)."""
    return _MU0_2PI * t_m * math.log(antipad_d_m / drill_d_m)


def coax_inductance(length_m: float, drill_d_m: float, antipad_d_m: float) -> float:
    """Legacy coax loop (μ0/2π)·ln(r_ap/r0)·l [H] (§2.6.2)."""
    return _MU0_2PI * math.log(antipad_d_m / drill_d_m) * length_m


def goldfarb_pucel_inductance(length_m: float, drill_d_m: float) -> float:
    """L_GP(h) partial self-inductance of a via post [Goldfarb91] [H] (§2.6.2)."""
    h = float(length_m)
    r0 = 0.5 * drill_d_m
    if h <= 0.0:
        return 0.0
    root = math.sqrt(r0 * r0 + h * h)
    return _MU0_2PI * (h * math.log((h + root) / r0) + 1.5 * (r0 - root))


def loop_inductance(geom: ViaGeometry, vs: ViaSettings) -> float:
    """Loop inductance of one PWR/GND via pair for the selected model [H] (§2.6.2)."""
    d = vs.drill_diameter_m
    l_ap = antipad_inductance(geom.t_near_m, d, vs.antipad_diameter_m)
    if vs.model == "coax":
        return coax_inductance(geom.h_r_m, d, vs.antipad_diameter_m)
    h = 0.0 if geom.zero_length else geom.h_near_m
    if vs.model == "goldfarb_pucel":
        return 2.0 * (goldfarb_pucel_inductance(h, d)
                      - partial_mutual_inductance(h, vs.via_pitch_m)) + l_ap
    if vs.model == "pair":
        return (pair_inductance(h, vs.via_pitch_m, d) if h > 0.0 else 0.0) + l_ap
    raise ValueError(f"unknown via model {vs.model!r}")


def via_resistance(f_hz: np.ndarray, length_m: float, drill_d_m: float,
                   plating_t_m: float, sigma: float) -> np.ndarray:
    """R = h/(σ_v A_e(ω)) with the smooth wall-depth interpolation of §2.6.3 [Ω]."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    r0 = 0.5 * drill_d_m
    t_eff = min(plating_t_m, r0)
    delta_v = np.sqrt(2.0 / (2.0 * math.pi * f * MU0 * sigma))
    delta_e = t_eff * (1.0 - np.exp(-delta_v / t_eff))
    area = math.pi * (r0 ** 2 - (r0 - delta_e) ** 2)
    return length_m / (sigma * area)


def _pair_impedance_geom(f_hz: np.ndarray, geom: ViaGeometry, vs: ViaSettings) -> np.ndarray:
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    r = via_resistance(f, geom.h_r_m, vs.drill_diameter_m, vs.plating_thickness_m,
                       vs.conductivity)
    return r + 1.0j * 2.0 * math.pi * f * loop_inductance(geom, vs)


def via_pair_impedance(f_hz: np.ndarray, stackup: Stackup, pwr_layer: int, gnd_layer: int,
                       vs: ViaSettings, issues: IssueCollector | None = None) -> np.ndarray:
    """Z_viapair(ω) = R_loop(ω) + jωL_loop of one via pair (§2.6.4)."""
    geom = via_geometry(stackup, pwr_layer, gnd_layer,
                        issues if issues is not None else IssueCollector())
    return _pair_impedance_geom(f_hz, geom, vs)


def decap_via_impedance(f_hz, stackup, pwr_layer, gnd_layer, vs: ViaSettings,
                        issues: IssueCollector | None = None) -> np.ndarray:
    """Z_via,dec = Z_viapair / n_pad, n_pad = vias per decap pad (§2.6.4)."""
    _check_counts(vs)
    return via_pair_impedance(f_hz, stackup, pwr_layer, gnd_layer, vs, issues) / vs.n_pair_dec


def pad_via_impedance(f_hz, stackup, pwr_layer, gnd_layer, vs: ViaSettings,
                      issues: IssueCollector | None = None) -> np.ndarray:
    """Z_via,pad = Z_viapair / pad_via_count (§2.6.4)."""
    _check_counts(vs)
    return via_pair_impedance(f_hz, stackup, pwr_layer, gnd_layer, vs, issues) / vs.n_pad


def _check_counts(vs: ViaSettings) -> None:
    probe = IssueCollector()
    validate_via_settings(vs, probe)
    bad = [i for i in probe.errors if i.code == "E_VIA_COUNT"]
    if bad:
        raise InputError(bad)
