"""Synthetic plane geometry and port placement (DESIGN.md §2.5, §2.6.5).

The plane is W × H with H = 1.4·D_ref; the N_pad observation pads form a row across the width at
y = 0.2·D_ref (N_pad = 1: the single PAD at (W/2, 0.2·D_ref)); each decap row is laid out across the
width at y = 0.2·D_ref + d_k. Both kinds of rows are split into several sub-rows when the ports
would crowd. Port order (normative): pads 0 … N_pad−1 (sub-rows ascending, left to right), then decap
rows in table order, sub-rows ascending, left to right.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from simple_pi_calculator.constants import (PAD_MARGIN_FACTOR, PLANE_HEIGHT_FACTOR,
                                            X_MARGIN_FACTOR)
from simple_pi_calculator.errors import InputError, IssueCollector

__all__ = [
    "DecapGroupGeom",
    "Placement",
    "plane_height",
    "ports_for_row",
    "caps_per_port_for_row",
    "place_ports",
]


@dataclass(frozen=True)
class DecapGroupGeom:
    """Geometry of one enabled decap row (§2.5.1)."""

    count: int  # N_k ≥ 1
    distance_m: float  # d_k > 0
    dummy: bool = False  # δ_k


@dataclass(frozen=True)
class Placement:
    """Derived plane size and port coordinates (§2.5)."""

    width_m: float  # W
    height_m: float  # H = 1.4·D_ref
    d_ref_m: float
    xy_m: np.ndarray  # (P,2), rows 0 … N_pad−1 = pads, then decap ports
    group_index: np.ndarray  # (P-N_pad,) int
    caps_per_port: np.ndarray  # (P-N_pad,) int ∈ {1,2}
    port_widths_m: np.ndarray  # (P,) w_pad × N_pad, then w_dec
    n_pads: int = 1  # N_pad ≥ 1 (§2.5.1)

    @property
    def n_ports(self) -> int:
        return int(self.xy_m.shape[0])

    @property
    def n_decap_ports(self) -> int:
        return int(self.xy_m.shape[0]) - int(self.n_pads)

    @property
    def pad_xy_m(self) -> tuple[float, float]:
        """Centre of the first pad (the single PAD for N_pad = 1)."""
        return float(self.xy_m[0, 0]), float(self.xy_m[0, 1])

    @property
    def pads_xy_m(self) -> np.ndarray:
        """(N_pad, 2) pad centres."""
        return self.xy_m[:int(self.n_pads)]


def plane_height(width_m: float, distances_m: Sequence[float]) -> tuple[float, float]:
    """Returns (H, D_ref) per §2.5.1 (no distances → D_ref = W/1.4, H = W)."""
    dists = [float(d) for d in distances_m]
    d_ref = max(dists) if dists else width_m / PLANE_HEIGHT_FACTOR
    return PLANE_HEIGHT_FACTOR * d_ref, d_ref


def ports_for_row(count: int, dummy: bool) -> int:
    """P_k = N_k, or ceil(N_k/2) for a Dummy Cap row (§2.5.2)."""
    return int(math.ceil(count / 2)) if dummy else int(count)


def caps_per_port_for_row(count: int, dummy: bool,
                          issues: IssueCollector | None = None,
                          source: str | None = None) -> list[int]:
    """c_{k,j} for the P_k ports of a row (§2.6.5); Σ c = N_k.

    With ``issues``, a dummy row of one capacitor emits ``I_DUMMY_SINGLE``.
    """
    n = int(count)
    if not dummy:
        return [1] * n
    if n == 1 and issues is not None:
        issues.info("I_DUMMY_SINGLE", "Dummy Cap row with a single capacitor: the flag has no "
                    "effect (one via set, one capacitor).", source)
    caps = [2] * (n // 2)
    if n % 2:
        caps.append(1)
    return caps


def place_ports(width_m: float, groups: Sequence[DecapGroupGeom],
                decap_port_width_m: float, pad_port_width_m: float,
                issues: IssueCollector, source: str, n_pads: int = 1) -> Placement:
    """Derive H, D_ref and all port coordinates (§2.5).

    ``n_pads`` = N_pad observation pads in a row at y = 0.2·D_ref (§2.5.1); N_pad = 1 gives the
    single PAD at (W/2, 0.2·D_ref).

    Errors (``E_DECAP_DISTANCE``, ``E_DREF_TOO_SMALL``, ``E_PWR_WIDTH_TOO_SMALL``,
    ``E_PWR_NPADS``) are added to ``issues`` and raised as :class:`InputError`. Warnings:
    ``W_DECAP_TOO_CLOSE``, ``W_DECAP_CLIPPED``, ``W_PAD_CLIPPED``, ``W_PORT_OVERLAP``; info
    ``I_DUMMY_SINGLE``.
    """
    W = float(width_m)
    w = float(decap_port_width_m)
    w_pad = float(pad_port_width_m)
    errors = []
    if isinstance(n_pads, bool) or int(n_pads) != n_pads or int(n_pads) < 1:
        errors.append(issues.error("E_PWR_NPADS", f"Number of PADs must be an integer ≥ 1 "
                                   f"(got {n_pads}).", source))
        raise InputError(errors)
    n_pad = int(n_pads)

    for k, g in enumerate(groups):
        if not (g.distance_m > 0.0) or not math.isfinite(g.distance_m):
            errors.append(issues.error("E_DECAP_DISTANCE",
                                       f"Decap row {k + 1}: distance to PAD must be > 0 "
                                       f"(got {g.distance_m * 1e3:g} mm).", source))
        if int(g.count) < 1:
            errors.append(issues.error("E_DECAP_COUNT",
                                       f"Decap row {k + 1}: number of decaps must be ≥ 1 "
                                       f"(got {g.count}).", source))
    if not (W > 0.0):
        errors.append(issues.error("E_PWR_DIM", f"Plane width must be > 0 (got {W * 1e3:g} mm).",
                                   source))
    if errors:
        raise InputError(errors)

    H, d_ref = plane_height(W, [g.distance_m for g in groups])
    y_pad = PAD_MARGIN_FACTOR * d_ref

    if y_pad < 0.5 * w_pad:
        errors.append(issues.error(
            "E_DREF_TOO_SMALL",
            f"The largest decap distance D_ref = {d_ref * 1e3:.4g} mm is too small: the PAD port "
            f"(width {w_pad * 1e3:.4g} mm) does not fit inside the plane (0.2·D_ref = "
            f"{y_pad * 1e3:.4g} mm < w_pad/2).", source))
    if w_pad > W:
        errors.append(issues.error(
            "E_PWR_WIDTH_TOO_SMALL",
            f"Plane width {W * 1e3:.4g} mm is smaller than the PAD port width "
            f"{w_pad * 1e3:.4g} mm.", source))

    m_x = 0.5 * w + X_MARGIN_FACTOR * W
    l_x = W - 2.0 * m_x
    if groups and l_x < w:
        errors.append(issues.error(
            "E_PWR_WIDTH_TOO_SMALL",
            f"Plane width {W * 1e3:.4g} mm is too small for the decap ports (usable span "
            f"{l_x * 1e3:.4g} mm < port width {w * 1e3:.4g} mm; W must be ≥ 2.5·w).", source))
    m_p = 0.5 * w_pad + X_MARGIN_FACTOR * W
    l_p = W - 2.0 * m_p
    if n_pad > 1 and l_p < w_pad:
        errors.append(issues.error(
            "E_PWR_WIDTH_TOO_SMALL",
            f"Plane width {W * 1e3:.4g} mm is too small for a row of {n_pad} PADs (usable span "
            f"{l_p * 1e3:.4g} mm < PAD port width {w_pad * 1e3:.4g} mm; W must be ≥ "
            "2.5·w_pad).", source))
    if errors:
        raise InputError(errors)

    xs: list[float] = []
    ys: list[float] = []
    # pad row (§2.5.1): N_pad = 1 → exactly (W/2, 0.2·D_ref); otherwise the decap-row rule with
    # the PAD port width w_pad
    if n_pad == 1:
        xs.append(0.5 * W)
        ys.append(y_pad)
    else:
        n_row = n_pad if l_p / n_pad >= w_pad else max(1, int(math.floor(l_p / w_pad)))
        n_sub = int(math.ceil(n_pad / n_row))
        clipped = 0
        for r in range(n_sub):
            n_r = min(n_row, n_pad - r * n_row)
            y_r = y_pad + (r - (n_sub - 1) / 2.0) * w_pad
            y_c = min(max(y_r, 0.5 * w_pad), H - 0.5 * w_pad)
            if y_c != y_r:
                clipped += n_r
            for i in range(n_r):
                xs.append(m_p + (i + 0.5) * l_p / n_r)
                ys.append(y_c)
        if clipped:
            issues.warning("W_PAD_CLIPPED",
                           f"{clipped} of {n_pad} PAD port(s) clipped to the plane edge (PAD "
                           f"sub-rows do not fit between y = 0 and H = {H * 1e3:.4g} mm).",
                           source)
    group_index: list[int] = []
    caps: list[int] = []
    for k, g in enumerate(groups):
        n_ports = ports_for_row(g.count, g.dummy)
        caps_k = caps_per_port_for_row(g.count, g.dummy, issues, source)
        if g.distance_m < 0.5 * (w + w_pad):
            issues.warning("W_DECAP_TOO_CLOSE",
                           f"Decap row {k + 1}: distance {g.distance_m * 1e3:.4g} mm is smaller "
                           f"than half the PAD + decap port widths; the decap port overlaps the "
                           "PAD port footprint.", source)
        y_k = y_pad + g.distance_m
        n_row = n_ports if l_x / n_ports >= w else max(1, int(math.floor(l_x / w)))
        n_sub = int(math.ceil(n_ports / n_row))
        clipped = 0
        j = 0
        for r in range(n_sub):
            n_r = min(n_row, n_ports - r * n_row)
            y_r = y_k + (r - (n_sub - 1) / 2.0) * w
            y_c = min(max(y_r, 0.5 * w), H - 0.5 * w)
            if y_c != y_r:
                clipped += n_r
            for i in range(n_r):
                xs.append(m_x + (i + 0.5) * l_x / n_r)
                ys.append(y_c)
                group_index.append(k)
                caps.append(caps_k[j])
                j += 1
        if clipped:
            issues.warning("W_DECAP_CLIPPED",
                           f"Decap row {k + 1}: {clipped} port(s) clipped to the plane edge "
                           f"(sub-rows do not fit inside H = {H * 1e3:.4g} mm).", source)

    xy = np.column_stack([np.asarray(xs), np.asarray(ys)])
    widths = np.concatenate([np.full(n_pad, w_pad), np.full(len(xs) - n_pad, w)])

    # Overlapping square footprints (§2.5.3)
    if xy.shape[0] > 1:
        half = 0.5 * (widths[:, None] + widths[None, :])
        dx = np.abs(xy[:, None, 0] - xy[None, :, 0])
        dy = np.abs(xy[:, None, 1] - xy[None, :, 1])
        overlap = np.triu((dx < half) & (dy < half), k=1)
        n_over = int(np.count_nonzero(overlap))
        if n_over:
            i0, j0 = (int(v[0]) for v in np.nonzero(overlap))
            issues.warning("W_PORT_OVERLAP",
                           f"{n_over} pair(s) of ports have overlapping footprints (e.g. ports "
                           f"{i0} and {j0}); check rows with almost equal distances.", source)

    return Placement(width_m=W, height_m=H, d_ref_m=d_ref, xy_m=xy,
                     group_index=np.asarray(group_index, dtype=int),
                     caps_per_port=np.asarray(caps, dtype=int),
                     port_widths_m=widths, n_pads=n_pad)
