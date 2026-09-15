"""Lossy planar-cavity (plane-pair) port impedance matrix (DESIGN.md §2.4, §3.2–§3.4).

The PWR/GND plane pair is modelled as a rectangular planar circuit with magnetic-wall edges
[Okoshi85], [Lei99]. Ports are small squares (width from the via-cluster GMD rule, §2.4.5).

Z_ij(ω) = (Z_p/(ab)) Σ_mn C_m C_n cos·cos·cos·cos · S_m,i S_n,i S_m,j S_n,j / (k_mn² − k²)

with Z_p = jωμ0 d Γ_c (conductor-loss factor in the prefactor, §2.4.1) and a quasi-static tail
extraction for modes with k_mn > K_split (§3.3). SI units throughout; no Qt, numpy only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from simple_pi_calculator.constants import (C0, EPS0, K_SPLIT_FACTOR, MAX_MODES_PER_AXIS,
                                            MIN_MODES, MU0, PORT_MODE_FACTOR,
                                            SQUARE_GMD_FACTOR, TAND_FLOOR)
from simple_pi_calculator.core.stackup import PlanePair

__all__ = [
    "CancelledError",
    "surface_impedance",
    "conductor_loss_factor",
    "plane_series_impedance",
    "wavenumber_sq",
    "ModeSettings",
    "CavityModel",
    "cluster_via_positions",
    "cluster_port_width",
    "single_via_port_width",
    "resonance_frequency",
]


class CancelledError(Exception):
    """Computation cancelled through the ``cancel`` callback (§5.2, §5.4).

    Defined here (the lowest module that polls the callback) and re-exported by ``core.pdn`` and
    ``core.engine`` so that all layers raise and catch the same class.
    """


# =============================================================================================
# Plane-pair per-square quantities (§2.4.1, §2.4.2)
# =============================================================================================
def _coth(z: np.ndarray) -> np.ndarray:
    """coth(z) for Re z > 0 with the small-argument series branch of §2.4.2."""
    z = np.asarray(z, dtype=complex)
    out = np.empty_like(z)
    small = np.abs(z) < 1e-3
    if np.any(small):
        zs = z[small]
        out[small] = 1.0 / zs + zs / 3.0
    big = ~small
    if np.any(big):
        e = np.exp(-2.0 * z[big])
        out[big] = (1.0 + e) / (1.0 - e)
    return out


def surface_impedance(f_hz: np.ndarray, sigma: float, t_m: float) -> np.ndarray:
    """Z_s = η_c coth(γ t) of a plane of conductivity σ and thickness t [Ω/sq] (§2.4.2).

    σ = +∞ (or None/≤ 0 treated as ideal) → 0.
    """
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    if sigma is None or not math.isfinite(sigma) or sigma <= 0.0:
        return np.zeros(f.shape, dtype=complex)
    omega = 2.0 * math.pi * f
    delta = np.sqrt(2.0 / (omega * MU0 * sigma))
    gamma = (1.0 + 1.0j) / delta
    eta = (1.0 + 1.0j) / (sigma * delta)
    return eta * _coth(gamma * t_m)


def _plane_zs_sum(f_hz: np.ndarray, pair: PlanePair) -> np.ndarray:
    return (surface_impedance(f_hz, _sigma(pair.pwr_layer.conductivity), pair.pwr_layer.thickness_m)
            + surface_impedance(f_hz, _sigma(pair.gnd_layer.conductivity),
                                pair.gnd_layer.thickness_m))


def _sigma(value: float | None) -> float:
    return math.inf if value is None else float(value)


def conductor_loss_factor(f_hz: np.ndarray, pair: PlanePair) -> np.ndarray:
    """Γ_c(ω) = 1 + (Z_s,PWR + Z_s,GND)/(jωμ0 d) (§2.4.1)."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    omega = 2.0 * math.pi * f
    return 1.0 + _plane_zs_sum(f, pair) / (1.0j * omega * MU0 * pair.d_m)


def plane_series_impedance(f_hz: np.ndarray, pair: PlanePair) -> np.ndarray:
    """Z_p(ω) = jωμ0 d + Z_s,PWR + Z_s,GND = jωμ0 d Γ_c [Ω/sq] (§2.4.1)."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    omega = 2.0 * math.pi * f
    return 1.0j * omega * MU0 * pair.d_m + _plane_zs_sum(f, pair)


def _tand_used(pair: PlanePair) -> float:
    return max(float(pair.tand_eff), TAND_FLOOR)  # loss floor, §3.4


def wavenumber_sq(f_hz: np.ndarray, pair: PlanePair) -> np.ndarray:
    """Lossy k²(ω) = ω² μ0 ε0 εr_eff (1 − j tanδ_used) Γ_c(ω) [rad²/m²] (§2.4.1, §3.4)."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    omega = 2.0 * math.pi * f
    return (omega ** 2 * MU0 * EPS0 * pair.er_eff * (1.0 - 1.0j * _tand_used(pair))
            * conductor_loss_factor(f, pair))


# =============================================================================================
# Via-cluster port width (§2.4.5)
# =============================================================================================
def single_via_port_width(drill_diameter_m: float) -> float:
    """w = r0/0.44705 = 1.11845·D (§2.4.5)."""
    return 0.5 * drill_diameter_m / SQUARE_GMD_FACTOR


def cluster_via_positions(n: int, pitch_m: float) -> np.ndarray:
    """(n, 2) centre coordinates of n vias on a square grid of pitch ``pitch_m`` (§2.4.5).

    Row-major fill with cols = ceil(√n) (via i at column i mod cols, row i div cols); the
    arrangement is centred on its bounding box. ``pitch_m`` is the grid pitch (√2·s_v for the
    checkerboard rule of §2.4.5).
    """
    if n < 1:
        raise ValueError("cluster needs at least one via")
    cols = int(math.ceil(math.sqrt(n)))
    idx = np.arange(n)
    col = (idx % cols).astype(float)
    row = (idx // cols).astype(float)
    xy = np.column_stack([col, row]) * pitch_m
    centre = 0.5 * (xy.min(axis=0) + xy.max(axis=0))
    return xy - centre


def cluster_port_width(n_vias: int, drill_diameter_m: float, via_pitch_m: float) -> float:
    """Equivalent square-port width of an n-via cluster from its self-GMD (§2.4.5).

    ln g = (1/n²) Σ_i Σ_j ln d_ij with d_ii = r0; grid pitch √2·s_v; w = g/0.44705.
    n = 1 → 1.11845·D.
    """
    n = int(n_vias)
    r0 = 0.5 * drill_diameter_m
    if n <= 1:
        return r0 / SQUARE_GMD_FACTOR
    xy = cluster_via_positions(n, math.sqrt(2.0) * via_pitch_m)
    diff = xy[:, None, :] - xy[None, :, :]
    dist = np.sqrt(np.sum(diff ** 2, axis=-1))
    np.fill_diagonal(dist, r0)
    ln_g = float(np.sum(np.log(dist))) / (n * n)
    return math.exp(ln_g) / SQUARE_GMD_FACTOR


def resonance_frequency(m: int, n: int, a_m: float, b_m: float, er: float) -> float:
    """Lossless f_mn = c0/(2√εr)·√((m/a)² + (n/b)²) [Hz] (§2.4.4)."""
    return C0 / (2.0 * math.sqrt(er)) * math.sqrt((m / a_m) ** 2 + (n / b_m) ** 2)


# =============================================================================================
# Cavity model (§2.4.3, §3.2, §3.3)
# =============================================================================================
@dataclass(frozen=True)
class ModeSettings:
    """Mode truncation parameters (§3.2)."""

    k_split_factor: float = K_SPLIT_FACTOR
    port_factor: float = PORT_MODE_FACTOR
    min_modes: int = MIN_MODES
    max_modes_per_axis: int = MAX_MODES_PER_AXIS


#: Upper bound for the (F_chunk, P, L) complex work array of the dynamic sum [bytes]
_DYN_CHUNK_BYTES = 64 * 1024 * 1024


class CavityModel:
    """Modal port impedance matrix of a rectangular a × b plane pair (§2.4.3, §3.3).

    Frequency-independent work (port factors, static sums S0/S1, dynamic mode set) is done in
    the constructor; :meth:`z_matrix` evaluates Z(ω) for any frequencies.
    """

    def __init__(self, a_m: float, b_m: float, pair: PlanePair,
                 port_xy_m: np.ndarray,
                 port_widths_m: np.ndarray,
                 f_eval_hz: np.ndarray,
                 settings: ModeSettings = ModeSettings(),
                 progress: Callable[[float], None] | None = None,
                 cancel: Callable[[], bool] | None = None):
        self.a = float(a_m)
        self.b = float(b_m)
        self.pair = pair
        xy = np.atleast_2d(np.asarray(port_xy_m, dtype=float))
        if xy.shape[1] != 2:
            raise ValueError("port_xy_m must have shape (P, 2)")
        widths = np.broadcast_to(np.asarray(port_widths_m, dtype=float), (xy.shape[0],)).copy()
        self.port_xy = xy
        self.port_widths = widths
        self.P = xy.shape[0]
        self.settings = settings

        # ---- mode counts (§3.2) ---------------------------------------------------------------
        f_eval = np.atleast_1d(np.asarray(f_eval_hz, dtype=float))
        k2_eval = wavenumber_sq(f_eval, pair)
        k0_max = 2.0 * math.pi * float(np.max(f_eval)) * math.sqrt(pair.er_eff) / C0
        self.k_max = max(math.sqrt(float(np.max(np.abs(k2_eval)))), k0_max)
        self.k_split = settings.k_split_factor * self.k_max
        w_min = float(np.min(widths))
        cap = int(settings.max_modes_per_axis)

        def count(length: float) -> tuple[int, bool]:
            want = max(int(settings.min_modes),
                       int(math.ceil(self.k_split * length / math.pi)),
                       int(math.ceil(settings.port_factor * length / w_min)))
            return min(cap, want), want > cap

        self.M, cap_m = count(self.a)
        self.N, cap_n = count(self.b)
        self.capped = bool(cap_m or cap_n)

        # ---- separable port factors (§3.3) ----------------------------------------------------
        m = np.arange(self.M + 1, dtype=float)
        n = np.arange(self.N + 1, dtype=float)
        c_m = np.where(m == 0, 1.0, 2.0)
        c_n = np.where(n == 0, 1.0, 2.0)
        x = xy[:, 0:1]
        y = xy[:, 1:2]
        w = widths[:, None]
        # np.sinc(m w/(2a)) = sinc_u(mπw/(2a)) — exact argument required by §2.4.3
        self.X = np.sqrt(c_m) * np.cos(m * math.pi * x / self.a) * np.sinc(m * w / (2.0 * self.a))
        self.Y = np.sqrt(c_n) * np.cos(n * math.pi * y / self.b) * np.sinc(n * w / (2.0 * self.b))
        kx2 = (m * math.pi / self.a) ** 2
        ky2 = (n * math.pi / self.b) ** 2

        # ---- static sums S0, S1 row-by-row in m (§3.3) -----------------------------------------
        ks2 = self.k_split ** 2
        P = self.P
        S0 = np.zeros((P, P))
        S1 = np.zeros((P, P))
        dyn_m: list[np.ndarray] = []
        dyn_n: list[np.ndarray] = []
        n_rows = self.M + 1
        for mi in range(n_rows):
            if cancel is not None and cancel():
                raise CancelledError("computation cancelled")
            kmn2 = kx2[mi] + ky2
            is_static = kmn2 > ks2  # (0,0) has kmn2 = 0 → never static
            dyn_idx = np.nonzero(~is_static)[0]
            if dyn_idx.size:
                dyn_m.append(np.full(dyn_idx.size, mi))
                dyn_n.append(dyn_idx)
            if np.any(is_static):
                safe = np.where(is_static, kmn2, 1.0)
                w0 = np.where(is_static, 1.0 / safe, 0.0)
                w1 = w0 * w0
                xx = np.outer(self.X[:, mi], self.X[:, mi])
                S0 += xx * ((self.Y * w0) @ self.Y.T)
                S1 += xx * ((self.Y * w1) @ self.Y.T)
            if progress is not None and (mi % 16 == 0 or mi == n_rows - 1):
                progress((mi + 1) / n_rows)
        self.S0 = S0
        self.S1 = S1
        mm = np.concatenate(dyn_m)
        nn = np.concatenate(dyn_n)
        self.dynamic_modes = np.column_stack([mm, nn])
        self.U_L = self.X[:, mm] * self.Y[:, nn]  # (P, L)
        self.kappa_L = kx2[mm] + ky2[nn]  # (L,)
        self.n_dynamic = int(mm.size)

    # -----------------------------------------------------------------------------------------
    def z_matrix(self, f_hz: np.ndarray,
                 progress: Callable[[float], None] | None = None,
                 cancel: Callable[[], bool] | None = None) -> np.ndarray:
        """Z(ω) for all frequencies, shape (F, P, P) complex128 (§3.3)."""
        f = np.atleast_1d(np.asarray(f_hz, dtype=float))
        F = f.size
        P = self.P
        L = self.n_dynamic
        k2 = wavenumber_sq(f, self.pair)
        zp = plane_series_impedance(f, self.pair)
        pref = zp / (self.a * self.b)
        out = np.empty((F, P, P), dtype=complex)
        per_f = max(1, 16 * P * max(L, P))
        chunk = max(1, min(F, _DYN_CHUNK_BYTES // per_f))
        U = self.U_L
        for start in range(0, F, chunk):
            if cancel is not None and cancel():
                raise CancelledError("computation cancelled")
            stop = min(F, start + chunk)
            g = 1.0 / (self.kappa_L[None, :] - k2[start:stop, None])  # (Fc, L)
            dyn = (U[None, :, :] * g[:, None, :]) @ U.T  # (Fc, P, P)
            dyn += self.S0[None, :, :]
            dyn += k2[start:stop, None, None] * self.S1[None, :, :]
            out[start:stop] = pref[start:stop, None, None] * dyn
            if progress is not None:
                progress(stop / F)
        return out

    def z_matrix_bruteforce(self, f_hz: np.ndarray) -> np.ndarray:
        """Full dynamic double sum without the static split (testing only; same M, N)."""
        f = np.atleast_1d(np.asarray(f_hz, dtype=float))
        k2 = wavenumber_sq(f, self.pair)
        zp = plane_series_impedance(f, self.pair)
        m = np.arange(self.M + 1, dtype=float)
        n = np.arange(self.N + 1, dtype=float)
        kx2 = (m * math.pi / self.a) ** 2
        ky2 = (n * math.pi / self.b) ** 2
        out = np.zeros((f.size, self.P, self.P), dtype=complex)
        for fi in range(f.size):
            acc = np.zeros((self.P, self.P), dtype=complex)
            for mi in range(self.M + 1):
                g = 1.0 / (kx2[mi] + ky2 - k2[fi])
                xx = np.outer(self.X[:, mi], self.X[:, mi])
                acc += xx * ((self.Y * g) @ self.Y.T)
            out[fi] = zp[fi] / (self.a * self.b) * acc
        return out
