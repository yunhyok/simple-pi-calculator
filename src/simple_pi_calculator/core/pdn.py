"""PDN impedance of one PWR net at the PAD: port loads and Schur reduction (DESIGN.md §2.8, §3.6,
Appendix B)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from simple_pi_calculator.constants import RCOND_MIN, SCHUR_CHUNK_BYTES
from simple_pi_calculator.core.cavity import (CancelledError, CavityCache, CavityModel,
                                              ModeSettings, cavity_cache_key, cluster_port_width)
from simple_pi_calculator.core.decap_model import DecapModel, evaluate_impedance
from simple_pi_calculator.core.parallel import (CHUNK_TARGET_BYTES, blas_limited, plan_chunks,
                                                resolve_workers, run_chunks)
from simple_pi_calculator.core.placement import DecapGroupGeom, Placement, place_ports
from simple_pi_calculator.core.stackup import Stackup, derive_plane_pair
from simple_pi_calculator.core.via import (ViaSettings, _pair_impedance_geom, loop_inductance,
                                           validate_via_settings, via_geometry)
from simple_pi_calculator.errors import InputError, Issue, IssueCollector

__all__ = [
    "CancelledError",
    "PwrSpec",
    "DecapGroup",
    "PwrResult",
    "SingularReductionError",
    "compute_pwr",
    "port_loads",
    "reduce_ports",
    "evaluation_frequencies",
]


@dataclass(frozen=True)
class PwrSpec:
    name: str
    pwr_layer: int
    gnd_layer: int
    width_m: float  # height is derived (§2.5.1)


@dataclass(frozen=True)
class DecapGroup:
    pwr_name: str
    model: DecapModel
    count: int
    distance_m: float
    dummy: bool = False


@dataclass
class PwrResult:
    name: str
    f_hz: np.ndarray  # plot grid (F,)
    z_pad: np.ndarray  # (F,) complex
    z_plane_only: np.ndarray | None
    marker_f_hz: np.ndarray  # (≤3,)
    marker_z: np.ndarray  # complex, exact
    placement: Placement
    info: dict[str, float | int | str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    marker_z_plane_only: np.ndarray | None = None  # extension: exact plane-only marker values


class SingularReductionError(ArithmeticError):
    """Port reduction is singular / ill-conditioned (→ ``E_SINGULAR``)."""

    code = "E_SINGULAR"

    def __init__(self, message: str, index: int, rcond: float):
        super().__init__(message)
        self.index = index
        self.rcond = rcond


# =============================================================================================
# Loads and reduction
# =============================================================================================
def port_loads(f_hz: np.ndarray, placement: Placement, groups: Sequence[DecapGroup],
               z_decap: Sequence[np.ndarray], z_via_dec: np.ndarray,
               mounting_inductance_h: float) -> np.ndarray:
    """(F, P-1) complex: Z_L,p = (Z_decap,k + jωL_mount)/c_p + Z_via,dec (§2.6.5)."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    jwl = 1.0j * 2.0 * math.pi * f * float(mounting_inductance_h)
    k = placement.group_index
    caps = placement.caps_per_port.astype(float)
    if k.size == 0:
        return np.zeros((f.size, 0), dtype=complex)
    zcap = np.stack([np.asarray(z, dtype=complex) + jwl for z in z_decap], axis=1)  # (F, G)
    zv = np.broadcast_to(np.asarray(z_via_dec, dtype=complex), f.shape)
    return zcap[:, k] / caps[None, :] + zv[:, None]


def _reduce_chunk(z_cav: np.ndarray, z_load: np.ndarray, s: int, e: int, check: bool
                  ) -> tuple[np.ndarray, np.ndarray, SingularReductionError | None]:
    """Schur reduction of frequencies s:e (§3.6); singular pivots are returned, not raised."""
    K = z_cav.shape[1] - 1
    diag = np.arange(K)
    a = z_cav[s:e, 1:, 1:].copy()
    a[:, diag, diag] += z_load[s:e]
    rhs = z_cav[s:e, 1:, 0:1]
    try:
        u = np.linalg.solve(a, rhs)[..., 0]
    except np.linalg.LinAlgError:
        bad = s
        for i in range(s, e):
            try:
                np.linalg.solve(a[i - s], rhs[i - s])
            except np.linalg.LinAlgError:
                bad = i
                break
        return (np.empty(0, dtype=complex), np.empty(0),
                SingularReductionError("singular port-reduction matrix", bad, 0.0))
    z_red = z_cav[s:e, 0, 0] - np.sum(z_cav[s:e, 0, 1:] * u, axis=-1)
    if check:
        with np.errstate(all="ignore"):
            if np.all(np.isfinite(a)):
                sv = np.linalg.svd(a, compute_uv=False)
                rc = sv[:, -1] / sv[:, 0]
                rc = np.where(np.isfinite(rc), rc, 0.0)
            else:
                rc = np.zeros(e - s)
    else:
        rc = np.ones(e - s)
    return z_red, rc, None


def _reduce(z_cav: np.ndarray, z_load: np.ndarray, check: bool = True,
            cancel: Callable[[], bool] | None = None, workers: int = 1,
            progress: Callable[[float], None] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Z_red and per-frequency rcond (1.0 when there are no decap ports).

    Frequency chunks run on ``workers`` threads (§3.9); results and error reporting (first
    offending frequency) are independent of ``workers``.
    """
    z_cav = np.asarray(z_cav, dtype=complex)
    z_load = np.asarray(z_load, dtype=complex)
    F = z_cav.shape[0]
    K = z_cav.shape[1] - 1
    z00 = z_cav[:, 0, 0].copy()
    if K == 0:
        return z00, np.ones(F)
    # working set ≈ 3 (K×K) complex copies per frequency (chunks sized by memory, not flops:
    # very small chunks lose the parallel gain to GIL hand-offs between the numpy calls)
    per_f = 48.0 * K * K
    chunks = plan_chunks(F, per_f, workers,
                         target_bytes=min(SCHUR_CHUNK_BYTES, CHUNK_TARGET_BYTES))

    def done(i: int, n: int) -> None:
        if progress is not None:
            progress(i / n)

    parts = run_chunks(lambda sl: _reduce_chunk(z_cav, z_load, sl.start, sl.stop, check),
                       chunks, workers, cancel=cancel, on_done=done,
                       cancelled_exc=CancelledError)
    errors = [err for _, _, err in parts if err is not None]
    if errors:
        raise min(errors, key=lambda err: err.index)
    z_red = np.concatenate([zr for zr, _, _ in parts])
    rcond = np.concatenate([rc for _, rc, _ in parts])
    if check:
        nonfinite = ~np.isfinite(z_red)
        bad = nonfinite | (rcond < RCOND_MIN)
        if np.any(bad):
            i = int(np.argmax(bad))
            raise SingularReductionError(
                "non-finite reduced impedance" if nonfinite[i] else
                f"ill-conditioned port-reduction matrix (rcond = {rcond[i]:.3g})", i,
                float(rcond[i]))
    return z_red, rcond


def reduce_ports(z_cav: np.ndarray, z_load: np.ndarray) -> np.ndarray:
    """z_cav (F,P,P), z_load (F,P-1) → (F,) Z_red at port 0 (§2.8, §3.6).

    Raises :class:`SingularReductionError` for exactly singular, non-finite or rcond < 1e-14 cases.
    """
    return _reduce(z_cav, z_load)[0]


# =============================================================================================
# Frequencies
# =============================================================================================
def evaluation_frequencies(f_grid_hz: np.ndarray, marker_f_hz: Sequence[float]
                           ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Union of grid and in-range markers (§3.1).

    Returns ``(f_eval, grid_idx, marker_f, marker_idx)`` with ``f_eval[grid_idx] == grid`` and
    ``f_eval[marker_idx] == marker_f``. Markers within rel 1e-12 of a grid point reuse it.
    """
    grid = np.atleast_1d(np.asarray(f_grid_hz, dtype=float))
    lo, hi = float(grid.min()), float(grid.max())
    markers = np.array(sorted(float(m) for m in marker_f_hz
                              if lo * (1 - 1e-12) <= float(m) <= hi * (1 + 1e-12)), dtype=float)
    extra = []
    for m in markers:
        if not np.any(np.abs(grid - m) <= 1e-12 * m):
            extra.append(m)
    f_eval = np.unique(np.concatenate([grid, np.asarray(extra, dtype=float)]))
    grid_idx = np.searchsorted(f_eval, grid)
    marker_idx = np.array([int(np.argmin(np.abs(f_eval - m))) for m in markers], dtype=int)
    return f_eval, grid_idx, markers, marker_idx


# =============================================================================================
# compute_pwr (Appendix B)
# =============================================================================================
def compute_pwr(stackup: Stackup, pwr: PwrSpec, groups: Sequence[DecapGroup],
                vias: ViaSettings, f_grid_hz: np.ndarray, marker_f_hz: Sequence[float],
                want_plane_only: bool, issues: IssueCollector,
                progress: Callable[[float], None] | None = None,
                cancel: Callable[[], bool] | None = None,
                settings: ModeSettings = ModeSettings(),
                workers: int | None = 1,
                cavity_cache: CavityCache | None = None) -> PwrResult:
    """Z at the PAD of one PWR net (§2.8). Errors are added to ``issues`` and raised as
    :class:`InputError`; cancellation raises :class:`CancelledError`.

    ``workers`` threads evaluate frequency chunks (0 / None = auto, §3.9); ``cavity_cache``
    memoises the cavity Z-matrix across calls. Neither changes the result.
    """
    with blas_limited(1):
        return _compute_pwr(stackup, pwr, groups, vias, f_grid_hz, marker_f_hz, want_plane_only,
                            issues, progress, cancel, settings, resolve_workers(workers),
                            cavity_cache)


#: progress fractions of the stages of :func:`compute_pwr` (start of each stage)
STAGE_DECAPS, STAGE_STATIC, STAGE_DYNAMIC, STAGE_REDUCE = 0.0, 0.05, 0.20, 0.55


def _compute_pwr(stackup: Stackup, pwr: PwrSpec, groups: Sequence[DecapGroup],
                 vias: ViaSettings, f_grid_hz: np.ndarray, marker_f_hz: Sequence[float],
                 want_plane_only: bool, issues: IssueCollector,
                 progress: Callable[[float], None] | None,
                 cancel: Callable[[], bool] | None,
                 settings: ModeSettings, workers: int,
                 cavity_cache: CavityCache | None) -> PwrResult:
    source = f"PWR:{pwr.name}"
    n_before = len(issues.issues)
    last = [0.0]

    def report(frac: float) -> None:
        if progress is not None:
            frac = min(1.0, max(last[0], frac))
            last[0] = frac
            progress(frac)

    def check_cancel() -> None:
        if cancel is not None and cancel():
            raise CancelledError("computation cancelled")

    check_cancel()
    # 1. plane pair, via settings, port widths
    pair = derive_plane_pair(stackup, pwr.pwr_layer, pwr.gnd_layer, issues, source)
    probe = IssueCollector()
    validate_via_settings(vias, probe, source)
    if probe.has_errors():
        issues.extend(probe.errors)
        raise InputError(probe.errors)
    w_pad = cluster_port_width(vias.n_pad, vias.drill_diameter_m, vias.via_pitch_m)
    w_dec = cluster_port_width(vias.n_pair_dec, vias.drill_diameter_m, vias.via_pitch_m)

    f_eval, grid_idx, marker_f, marker_idx = evaluation_frequencies(f_grid_hz, marker_f_hz)
    grid = f_eval[grid_idx]

    # 2. decap models (distinct models evaluated once; memoised per model and sweep, §3.9)
    groups = list(groups)
    z_cache: dict[int, np.ndarray] = {}
    z_decap: list[np.ndarray] = []
    for gi, g in enumerate(groups):
        check_cancel()
        key = id(g.model)
        if key not in z_cache:
            z = np.asarray(evaluate_impedance(g.model, f_eval, issues), dtype=complex)
            if z.shape != f_eval.shape or not np.all(np.isfinite(z)):
                err = issues.error("E_SINGULAR", f"Decap model {getattr(g.model, 'label', '?')} "
                                   "returned non-finite impedance values.", source)
                raise InputError([err])
            z_cache[key] = z
        z_decap.append(z_cache[key])
        report(STAGE_STATIC * (gi + 1) / len(groups))
    report(STAGE_STATIC)

    # 3. placement
    if not groups:
        issues.warning("W_PWR_NO_DECAPS", f"PWR {pwr.name} has no enabled decap rows: square "
                       "plane H = W, plane-only result.", source)
    geoms = [DecapGroupGeom(count=int(g.count), distance_m=float(g.distance_m), dummy=bool(g.dummy))
             for g in groups]
    placement = place_ports(pwr.width_m, geoms, w_dec, w_pad, issues, source)
    a, b = placement.width_m, placement.height_m
    c_plane = pair.plane_capacitance(a, b)

    # 4. cavity (Z-matrix cache keyed by geometry, plane pair, ports, sweep and mode settings)
    ckey = None
    cached = None
    if cavity_cache is not None:
        ckey = cavity_cache_key(a, b, pair, placement.xy_m, placement.port_widths_m, f_eval,
                                settings)
        cached = cavity_cache.get(ckey)
    if cached is not None:
        z_cav, meta = cached
        check_cancel()
    else:
        cav = CavityModel(a, b, pair, placement.xy_m, placement.port_widths_m, f_eval, settings,
                          progress=lambda x: report(STAGE_STATIC
                                                    + (STAGE_DYNAMIC - STAGE_STATIC) * x),
                          cancel=cancel)
        z_cav = cav.z_matrix(f_eval, workers=workers,
                             progress=lambda x: report(STAGE_DYNAMIC
                                                       + (STAGE_REDUCE - STAGE_DYNAMIC) * x),
                             cancel=cancel)
        meta = {"M": cav.M, "N": cav.N, "capped": cav.capped, "n_dynamic": cav.n_dynamic}
        if cavity_cache is not None and ckey is not None:
            cavity_cache.put(ckey, z_cav, meta)
    report(STAGE_REDUCE)
    if meta["capped"]:
        issues.warning("W_MODES_CAPPED", f"Mode count capped at {settings.max_modes_per_axis} per "
                       f"axis (M = {meta['M']}, N = {meta['N']}); spreading inductance slightly "
                       "under-resolved.", source)

    # 5. vias
    geom = via_geometry(stackup, pwr.pwr_layer, pwr.gnd_layer, issues, source)
    l_loop = loop_inductance(geom, vias)
    z_pair = _pair_impedance_geom(f_eval, geom, vias)
    z_via_dec = z_pair / vias.n_pair_dec
    z_via_pad = z_pair / vias.n_pad

    # 6. loads, 7. reduction
    check_cancel()
    z_load = port_loads(f_eval, placement, groups, z_decap, z_via_dec, vias.mounting_inductance_h)
    try:
        z_red, rcond = _reduce(z_cav, z_load, check=True, cancel=cancel, workers=workers,
                               progress=lambda x: report(STAGE_REDUCE
                                                         + (1.0 - STAGE_REDUCE) * 0.999 * x))
    except SingularReductionError as exc:
        f_bad = f_eval[min(exc.index, f_eval.size - 1)]
        err = issues.error(
            "E_SINGULAR",
            f"PWR {pwr.name}: {exc} at f = {f_bad:.6g} Hz. Check W_PORT_OVERLAP warnings and "
            "zero-length vias combined with ideal-short decap models.", source)
        raise InputError([err]) from None
    z_pad = z_red + z_via_pad
    if not np.all(np.isfinite(z_pad)):
        err = issues.error("E_SINGULAR", f"PWR {pwr.name}: non-finite Z at the PAD.", source)
        raise InputError([err])
    z_plane = z_cav[:, 0, 0] + z_via_pad if want_plane_only else None
    report(1.0)

    info: dict[str, float | int | str] = {
        "C_plane": c_plane,
        "er_eff": pair.er_eff,
        "tand_eff": pair.tand_eff,
        "d_m": pair.d_m,
        "W_m": a,
        "H_m": b,
        "D_ref_m": placement.d_ref_m,
        "M": meta["M"],
        "N": meta["N"],
        "n_dynamic": meta["n_dynamic"],
        "P": placement.n_ports,
        "h_near_m": geom.h_near_m,
        "h_r_m": geom.h_r_m,
        "L_loop": l_loop,
        "w_pad_m": w_pad,
        "w_dec_m": w_dec,
        "min_rcond": float(np.min(rcond)) if rcond.size else 1.0,
    }
    return PwrResult(
        name=pwr.name,
        f_hz=grid,
        z_pad=z_pad[grid_idx],
        z_plane_only=None if z_plane is None else z_plane[grid_idx],
        marker_f_hz=marker_f,
        marker_z=z_pad[marker_idx] if marker_idx.size else np.zeros(0, dtype=complex),
        placement=placement,
        info=info,
        issues=list(issues.issues[n_before:]),
        marker_z_plane_only=(None if z_plane is None else
                             (z_plane[marker_idx] if marker_idx.size
                              else np.zeros(0, dtype=complex))),
    )
