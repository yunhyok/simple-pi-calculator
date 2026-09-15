"""Decap distance distribution: fixed or truncated-normal sampled distances (DESIGN.md §2.5.5).

In ``fixed`` mode (the 0.2.0 behaviour) every via set (cavity port) of a decap row sits at exactly
the row's ``Distance to PAD`` D_k. In ``normal`` mode each port j of row k gets its own distance

    d_kj = D_k + σ · z_kj,    z_kj ~ N(0, 1) truncated to [−1, 1]

so that all samples lie in [D_k − σ, D_k + σ]. The standard truncated normal is sampled by inverse
transform: u ~ U[0, 1) from ``numpy.random.default_rng(seed)`` (PCG64, platform independent),
p = Φ(−1) + u·(Φ(1) − Φ(−1)), z = Φ⁻¹(p) [JKB94, ch. 13.10]. Φ uses ``math.erfc``; Φ⁻¹ is Acklam's
rational approximation refined by one Halley step (no scipy).

Sampling order is normative: one generator per computation, decap rows in table order, ports of a
row in port order (§2.5.3); a Dummy Cap row draws one sample per via set, not per capacitor. Only
the enabled rows draw samples, so disabling a row changes the samples of the rows after it.

Qt-free, numpy only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from simple_pi_calculator.constants import (DEFAULT_DISTANCE_MODE, DEFAULT_DISTANCE_SEED,
                                            DEFAULT_DISTANCE_SIGMA_MM, DISTANCE_MODES,
                                            DISTANCE_SEED_MAX)

__all__ = [
    "DistanceDistribution",
    "norm_cdf",
    "norm_ppf",
    "truncated_standard_normal",
    "sample_offsets",
    "row_port_counts",
    "sample_row_distances",
    "distance_summary",
]

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)
#: truncation of the standard normal (±1σ)
TRUNCATION: float = 1.0


@dataclass(frozen=True)
class DistanceDistribution:
    """Distance distribution settings, SI (§2.5.5)."""

    mode: str = DEFAULT_DISTANCE_MODE  # "fixed" | "normal"
    sigma_m: float = DEFAULT_DISTANCE_SIGMA_MM * 1e-3
    seed: int = DEFAULT_DISTANCE_SEED

    @property
    def is_fixed(self) -> bool:
        return self.mode != "normal"

    def validate(self) -> list[tuple[str, str]]:
        """``(code, message)`` pairs for invalid settings (checked only in ``normal`` mode for σ
        and seed)."""
        out: list[tuple[str, str]] = []
        if self.mode not in DISTANCE_MODES:
            out.append(("E_DIST_MODE", f"Distance distribution {self.mode!r} is not one of "
                        f"{', '.join(DISTANCE_MODES)}."))
            return out
        if self.is_fixed:
            return out
        s = self.sigma_m
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) \
                or not s > 0.0:
            out.append(("E_DIST_SIGMA", f"Distance σ must be a finite number > 0 (got {s!r} m)."))
        seed = self.seed
        if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)) \
                or not 0 <= int(seed) <= DISTANCE_SEED_MAX:
            out.append(("E_DIST_SEED", f"Distance seed must be an integer 0 … {DISTANCE_SEED_MAX} "
                        f"(got {seed!r})."))
        return out


# =============================================================================================
# Standard normal CDF / quantile (no scipy)
# =============================================================================================
_erfc = np.frompyfunc(math.erfc, 1, 1)


def norm_cdf(x: np.ndarray | float) -> np.ndarray:
    """Φ(x) = ½·erfc(−x/√2) (element-wise, ``math.erfc``)."""
    arr = np.asarray(x, dtype=float)
    return 0.5 * np.asarray(_erfc(-arr / _SQRT2), dtype=float)


# Acklam's coefficients (lower/upper tail: c, d; central region: a, b)
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_P_LOW = 0.02425


def norm_ppf(p: np.ndarray | float) -> np.ndarray:
    """Φ⁻¹(p) for 0 < p < 1: Acklam's rational approximation (rel. error < 1.15e-9) refined by
    one Halley step on Φ(x) − p (accuracy then limited by ``math.erfc``, ≈ 1e-15)."""
    q = np.atleast_1d(np.asarray(p, dtype=float))
    if np.any(~(q > 0.0) | ~(q < 1.0)):
        raise ValueError("norm_ppf requires 0 < p < 1")
    x = np.empty_like(q)
    lo = q < _P_LOW
    hi = q > 1.0 - _P_LOW
    mid = ~(lo | hi)
    if np.any(mid):
        r = q[mid] - 0.5
        s = r * r
        num = (((((_A[0] * s + _A[1]) * s + _A[2]) * s + _A[3]) * s + _A[4]) * s + _A[5]) * r
        den = ((((_B[0] * s + _B[1]) * s + _B[2]) * s + _B[3]) * s + _B[4]) * s + 1.0
        x[mid] = num / den
    for mask, sign in ((lo, 1.0), (hi, -1.0)):
        if np.any(mask):
            t = np.sqrt(-2.0 * np.log(q[mask] if sign > 0 else 1.0 - q[mask]))
            num = ((((_C[0] * t + _C[1]) * t + _C[2]) * t + _C[3]) * t + _C[4]) * t + _C[5]
            den = (((_D[0] * t + _D[1]) * t + _D[2]) * t + _D[3]) * t + 1.0
            x[mask] = sign * num / den
    # one Halley step. In the upper tail Φ(x) rounds to 1 (review v0.3: errors up to 1e-8 for
    # x > 4), so there the step is taken on the mirrored lower tail Φ(−x) − (1 − q), where 1 − q
    # is exact (Sterbenz) and erfc keeps full relative precision.
    t = np.where(hi, -x, x)
    r = np.where(hi, 1.0 - q, q)
    e = norm_cdf(t) - r
    u = e * _SQRT2PI * np.exp(0.5 * t * t)
    t = t - u / (1.0 + 0.5 * t * u)
    x = np.where(hi, -t, t)
    return x if np.ndim(p) else x.reshape(())


_PHI_LO = float(norm_cdf(-TRUNCATION))
_PHI_HI = float(norm_cdf(TRUNCATION))


def truncated_standard_normal(rng: np.random.Generator, n: int) -> np.ndarray:
    """n samples of N(0, 1) truncated to [−1, 1] by inverse transform (§2.5.5)."""
    n = int(n)
    if n <= 0:
        return np.zeros(0)
    u = rng.random(n)
    z = norm_ppf(_PHI_LO + u * (_PHI_HI - _PHI_LO))
    return np.clip(z, -TRUNCATION, TRUNCATION)


# =============================================================================================
# Sampling in the normative order
# =============================================================================================
def sample_offsets(port_counts: Sequence[int], seed: int) -> list[np.ndarray]:
    """Standard offsets z_kj for rows with ``port_counts[k]`` ports: one generator
    ``default_rng(seed)``, rows in the given order, ports in port order."""
    rng = np.random.default_rng(int(seed))
    return [truncated_standard_normal(rng, max(0, int(n))) for n in port_counts]


def row_port_counts(counts: Sequence[int], dummies: Sequence[bool]) -> list[int]:
    """P_k per row (§2.5.2): N_k, or ceil(N_k/2) via sets for a Dummy Cap row; < 1 → 0."""
    out = []
    for n, dummy in zip(counts, dummies):
        n = int(n)
        out.append(0 if n < 1 else (int(math.ceil(n / 2)) if dummy else n))
    return out


def sample_row_distances(rows: Sequence[tuple[int, float, bool]],
                         distribution: DistanceDistribution | None
                         ) -> list[tuple[float, ...] | None]:
    """Per-port distances [m] of the rows ``(count, distance_m, dummy)`` given in sampling order.

    ``None`` entries for ``fixed`` mode (or no distribution): every port of the row sits at the
    row distance.
    """
    if distribution is None or distribution.is_fixed:
        return [None] * len(rows)
    counts = row_port_counts([r[0] for r in rows], [bool(r[2]) for r in rows])
    offsets = sample_offsets(counts, distribution.seed)
    sigma = float(distribution.sigma_m)
    return [tuple(float(d) + sigma * z for z in zs.tolist()) for (_, d, _), zs
            in zip(rows, offsets)]


def distance_summary(distances_m: Sequence[float]) -> tuple[float, float, float] | None:
    """(min, mean, max) [m] or ``None`` for no ports."""
    arr = np.asarray(list(distances_m), dtype=float)
    if arr.size == 0:
        return None
    return float(arr.min()), float(arr.mean()), float(arr.max())
