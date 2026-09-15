"""Lossy planar-cavity (plane-pair) port impedance matrix (DESIGN.md §2.4, §3.2–§3.4).

The PWR/GND plane pair is modelled as a rectangular planar circuit with magnetic-wall edges
[Okoshi85], [Lei99]. Ports are small squares (width from the via-cluster GMD rule, §2.4.5).

Z_ij(ω) = (Z_p/(ab)) Σ_mn C_m C_n cos·cos·cos·cos · S_m,i S_n,i S_m,j S_n,j / (k_mn² − k²)

with Z_p = jωμ0 d Γ_c (conductor-loss factor in the prefactor, §2.4.1) and a quasi-static tail
extraction for modes with k_mn > K_split (§3.3). SI units throughout; no Qt, numpy only.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
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
    "z_matrix_reference",
    "cavity_cache_key",
    "CavityCache",
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


#: Upper bound for the (F_chunk, P, L) complex work array of the reference dynamic sum [bytes]
_DYN_CHUNK_BYTES = 64 * 1024 * 1024
#: Upper bound for the (L, P²) real table of dynamic-mode port products [bytes]
_DYN_T_MAX_BYTES = 64 * 1024 * 1024


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

        # ---- static sums S0, S1 grouped by distinct port factors (§3.3, §3.9) ------------------
        self.S0, self.S1, mm, nn = _static_sums(self.X, self.Y, kx2, ky2, self.k_split ** 2,
                                                progress=progress, cancel=cancel)
        self.dynamic_modes = np.column_stack([mm, nn])
        self.U_L = self.X[:, mm] * self.Y[:, nn]  # (P, L)
        self.kappa_L = kx2[mm] + ky2[nn]  # (L,)
        self.n_dynamic = int(mm.size)
        self._kx2 = kx2
        self._ky2 = ky2
        self._T: np.ndarray | None = None
        self._T_lock = threading.Lock()

    # -----------------------------------------------------------------------------------------
    def _pair_products(self) -> np.ndarray | None:
        """T = [u_l u_lᵀ] flattened to (L, P²), or None when too large (lazily, thread-safe)."""
        L, P = self.n_dynamic, self.P
        if L * P * P * 8 > _DYN_T_MAX_BYTES:
            return None
        with self._T_lock:
            if self._T is None:
                U = self.U_L
                self._T = np.ascontiguousarray(
                    (U.T[:, :, None] * U.T[:, None, :]).reshape(L, P * P))
            return self._T

    def z_matrix_into(self, out: np.ndarray, f_hz: np.ndarray, sl: slice,
                      k2: np.ndarray | None = None, zp: np.ndarray | None = None) -> None:
        """Write Z(ω) for the frequencies ``f_hz[sl]`` into ``out[sl]`` (shape (F, P, P) complex).

        Thread-safe for disjoint slices; used by :meth:`z_matrix` and the parallel engine.
        ``k2`` / ``zp`` are the precomputed k²(ω) and Z_p(ω) over the whole ``f_hz``.
        """
        f = np.atleast_1d(np.asarray(f_hz, dtype=float))
        if k2 is None:
            k2 = wavenumber_sq(f, self.pair)
        if zp is None:
            zp = plane_series_impedance(f, self.pair)
        P = self.P
        k2c = k2[sl]
        pref = zp[sl] / (self.a * self.b)
        Fc = k2c.size
        if Fc == 0:
            return
        g = 1.0 / (self.kappa_L[None, :] - k2c[:, None])  # (Fc, L) complex
        T = self._pair_products()
        if T is not None:
            re = (g.real @ T).reshape(Fc, P, P)
            im = (g.imag @ T).reshape(Fc, P, P)
        else:  # very many dynamic modes: row-wise product without the (L, P²) table
            U = self.U_L
            re = np.empty((Fc, P, P))
            im = np.empty((Fc, P, P))
            for i in range(Fc):
                re[i] = (U * g[i].real) @ U.T
                im[i] = (U * g[i].imag) @ U.T
        # D = dyn + S0 + k²·S1 ; Z = pref·D  (same complex arithmetic as §3.3, split into parts)
        re += self.S0[None, :, :]
        re += k2c.real[:, None, None] * self.S1[None, :, :]
        im += k2c.imag[:, None, None] * self.S1[None, :, :]
        o = out[sl]
        pr = pref.real[:, None, None]
        pi = pref.imag[:, None, None]
        o.real = pr * re - pi * im
        o.imag = pr * im + pi * re

    def z_matrix(self, f_hz: np.ndarray,
                 progress: Callable[[float], None] | None = None,
                 cancel: Callable[[], bool] | None = None,
                 workers: int = 1) -> np.ndarray:
        """Z(ω) for all frequencies, shape (F, P, P) complex128 (§3.3, §3.9).

        Frequency chunks are evaluated on ``workers`` threads; the result does not depend on
        ``workers``.
        """
        from simple_pi_calculator.core.parallel import plan_chunks, run_chunks
        f = np.atleast_1d(np.asarray(f_hz, dtype=float))
        F, P = f.size, self.P
        k2 = wavenumber_sq(f, self.pair)
        zp = plane_series_impedance(f, self.pair)
        out = np.empty((F, P, P), dtype=complex)
        self._pair_products()  # build the shared table once, outside the threads
        chunks = plan_chunks(F, 48.0 * P * P + 16.0 * self.n_dynamic, workers)

        def done(i: int, n: int) -> None:
            if progress is not None:
                progress(i / n)

        run_chunks(lambda sl: self.z_matrix_into(out, f, sl, k2, zp), chunks, workers,
                   cancel=cancel, on_done=done, cancelled_exc=CancelledError)
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


# =============================================================================================
# Static sums (§3.3) — grouped evaluation (§3.9)
# =============================================================================================
def _static_weights(kx2: np.ndarray, ky2: np.ndarray, ks2: float
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """W0 = 1/k_mn² on the static set 𝓗 (else 0), W1 = W0², and the dynamic mask (M+1, N+1)."""
    kmn2 = kx2[:, None] + ky2[None, :]
    static = kmn2 > ks2  # (0,0) has k_mn² = 0 → never static
    w0 = np.zeros_like(kmn2)
    np.divide(1.0, kmn2, out=w0, where=static)
    return w0, w0 * w0, ~static


def _static_sums(X: np.ndarray, Y: np.ndarray, kx2: np.ndarray, ky2: np.ndarray, ks2: float,
                 progress: Callable[[float], None] | None = None,
                 cancel: Callable[[], bool] | None = None,
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """S0, S1 and the dynamic mode indices (m, n) in row-major order (§3.3).

    S_ij = Σ_m X_im X_jm C_{ij,m} with C_{ij,m} = Σ_n Y_in Y_jn W_mn. Ports on the same decap
    (sub-)row share identical Y rows (same y and width), so the n-sum only has to be done for each
    pair of *distinct* Y rows: cost R²/2·(M+1)(N+1) instead of 2·P²·(M+1)(N+1) for R distinct
    rows. The axis with fewer distinct rows is chosen (roles of X/Y swapped, W transposed).
    Exactly the same terms are summed as in the row-by-row loop of §3.3; only the grouping (and
    therefore the floating-point rounding, ≈ 1e-16 relative) differs.
    """
    P = X.shape[0]
    w0, w1, dyn = _static_weights(kx2, ky2, ks2)
    mm, nn = np.nonzero(dyn)
    _, inv_y = np.unique(Y, axis=0, return_inverse=True)
    _, inv_x = np.unique(X, axis=0, return_inverse=True)
    inv_y = inv_y.reshape(-1)
    inv_x = inv_x.reshape(-1)
    if len(np.unique(inv_x)) < len(np.unique(inv_y)):  # group along x instead
        X, Y, inv = Y, X, inv_x
        w0, w1 = w0.T, w1.T
    else:
        inv = inv_y
    # distinct rows in order of first appearance; ports grouped per distinct row
    first = np.unique(inv, return_index=True)[1]
    order = np.argsort(first, kind="stable")
    remap = np.empty(order.size, dtype=int)
    remap[order] = np.arange(order.size)
    grp = remap[inv]  # (P,) group id 0..R-1
    R = order.size
    reps = np.array([np.nonzero(grp == r)[0][0] for r in range(R)], dtype=int)
    Yu = Y[reps]  # (R, N+1)
    members = [np.nonzero(grp == r)[0] for r in range(R)]
    w0t = np.ascontiguousarray(w0.T)  # (N+1, M+1)
    w1t = np.ascontiguousarray(w1.T)
    S0 = np.zeros((P, P))
    S1 = np.zeros((P, P))
    for r in range(R):
        if cancel is not None and cancel():
            raise CancelledError("computation cancelled")
        B = Yu[r][None, :] * Yu[r:]  # (R-r, N+1): Y_r ∘ Y_s for s ≥ r
        C0 = B @ w0t  # (R-r, M+1)
        C1 = B @ w1t
        I = members[r]
        J = np.concatenate(members[r:])
        gJ = grp[J] - r
        XI = X[I]
        XJ = X[J]
        blk0 = XI @ (XJ * C0[gJ]).T  # (|I|, |J|)
        blk1 = XI @ (XJ * C1[gJ]).T
        S0[np.ix_(I, J)] = blk0
        S0[np.ix_(J, I)] = blk0.T
        S1[np.ix_(I, J)] = blk1
        S1[np.ix_(J, I)] = blk1.T
        if progress is not None:
            progress((r + 1) / R)
    return S0, S1, mm, nn


# =============================================================================================
# Reference implementation (tests only)
# =============================================================================================
def z_matrix_reference(cav: CavityModel, f_hz: np.ndarray) -> np.ndarray:
    """Straightforward v0.1.0 evaluation of Z(ω) (tests only, DESIGN.md §3.9).

    Static sums row-by-row in m with (P, P) BLAS products, and the dynamic sum as a batched
    (F, P, L)·(P, L)ᵀ complex product — the loop structure of §3.3 before the performance work.
    Uses the same mode sets and port factors as ``cav``.
    """
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    X, Y = cav.X, cav.Y
    m = np.arange(cav.M + 1, dtype=float)
    n = np.arange(cav.N + 1, dtype=float)
    kx2 = (m * math.pi / cav.a) ** 2
    ky2 = (n * math.pi / cav.b) ** 2
    ks2 = cav.k_split ** 2
    P = cav.P
    S0 = np.zeros((P, P))
    S1 = np.zeros((P, P))
    dyn_m: list[np.ndarray] = []
    dyn_n: list[np.ndarray] = []
    for mi in range(cav.M + 1):
        kmn2 = kx2[mi] + ky2
        is_static = kmn2 > ks2
        dyn_idx = np.nonzero(~is_static)[0]
        if dyn_idx.size:
            dyn_m.append(np.full(dyn_idx.size, mi))
            dyn_n.append(dyn_idx)
        if np.any(is_static):
            safe = np.where(is_static, kmn2, 1.0)
            w0 = np.where(is_static, 1.0 / safe, 0.0)
            w1 = w0 * w0
            xx = np.outer(X[:, mi], X[:, mi])
            S0 += xx * ((Y * w0) @ Y.T)
            S1 += xx * ((Y * w1) @ Y.T)
    mm = np.concatenate(dyn_m)
    nn = np.concatenate(dyn_n)
    U = X[:, mm] * Y[:, nn]
    kappa = kx2[mm] + ky2[nn]
    k2 = wavenumber_sq(f, cav.pair)
    zp = plane_series_impedance(f, cav.pair)
    pref = zp / (cav.a * cav.b)
    F = f.size
    L = mm.size
    out = np.empty((F, P, P), dtype=complex)
    chunk = max(1, min(F, _DYN_CHUNK_BYTES // max(1, 16 * P * max(L, P))))
    for start in range(0, F, chunk):
        stop = min(F, start + chunk)
        g = 1.0 / (kappa[None, :] - k2[start:stop, None])
        dyn = (U[None, :, :] * g[:, None, :]) @ U.T
        dyn += S0[None, :, :]
        dyn += k2[start:stop, None, None] * S1[None, :, :]
        out[start:stop] = pref[start:stop, None, None] * dyn
    return out


# =============================================================================================
# Cavity Z-matrix cache (§3.9)
# =============================================================================================
def cavity_cache_key(a_m: float, b_m: float, pair: PlanePair, port_xy_m: np.ndarray,
                     port_widths_m: np.ndarray, f_eval_hz: np.ndarray,
                     settings: ModeSettings) -> str:
    """Digest of everything the cavity Z-matrix depends on (§3.9).

    Plane size, the full plane pair (layers, thicknesses, σ, Dk/Df, d, εr_eff, tanδ_eff), port
    coordinates and widths, the evaluation frequencies and the mode settings. Floats enter with
    their exact binary value (``repr`` round-trips; arrays by their bytes).
    """
    h = hashlib.sha256()
    h.update(repr((float(a_m), float(b_m), pair, settings)).encode())
    for arr in (port_xy_m, port_widths_m, f_eval_hz):
        arr = np.ascontiguousarray(np.asarray(arr, dtype=float))
        h.update(repr(arr.shape).encode())
        h.update(arr.tobytes())
    return h.hexdigest()


class CavityCache:
    """Bounded LRU cache of cavity Z-matrices (F, P, P) with mode-count metadata (§3.9).

    Thread-safe. Entries are stored read-only. Bounded by ``max_entries`` and ``max_bytes``;
    ``max_entries = 0`` disables caching. An entry larger than ``max_bytes`` is not stored.
    """

    def __init__(self, max_entries: int = 32, max_bytes: int = 512 * 1024 * 1024):
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._data: OrderedDict[str, tuple[np.ndarray, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> tuple[np.ndarray, dict] | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return item

    def put(self, key: str, z: np.ndarray, meta: dict) -> None:
        if self.max_entries <= 0 or z.nbytes > self.max_bytes:
            return
        z = np.asarray(z)
        z.setflags(write=False)
        with self._lock:
            self._data[key] = (z, dict(meta))
            self._data.move_to_end(key)
            while self._data and (len(self._data) > self.max_entries
                                  or self.nbytes > self.max_bytes):
                self._data.popitem(last=False)

    @property
    def nbytes(self) -> int:
        return sum(z.nbytes for z, _ in self._data.values())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
