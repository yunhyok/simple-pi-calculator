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
    "combine_pads",
    "evaluation_frequencies",
]


@dataclass(frozen=True)
class PwrSpec:
    name: str
    pwr_layer: int
    gnd_layer: int
    width_m: float  # height is derived (§2.5.1)
    n_pads: int = 1  # N_pad observation pads of the net (§2.5.1, §2.8)


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

    @property
    def n_pads(self) -> int:
        """N_pad observation pads (§2.8)."""
        return int(getattr(self.placement, "n_pads", 1))


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
    """(F, P-N_pad) complex: Z_L,p = (Z_decap,k + jωL_mount)/c_p + Z_via,dec (§2.6.5)."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    jwl = 1.0j * 2.0 * math.pi * f * float(mounting_inductance_h)
    k = placement.group_index
    caps = placement.caps_per_port.astype(float)
    if k.size == 0:
        return np.zeros((f.size, 0), dtype=complex)
    zcap = np.stack([np.asarray(z, dtype=complex) + jwl for z in z_decap], axis=1)  # (F, G)
    zv = np.broadcast_to(np.asarray(z_via_dec, dtype=complex), f.shape)
    return zcap[:, k] / caps[None, :] + zv[:, None]


#: number of fixed random probe vectors solved together with the Schur right-hand side (§3.6)
RCOND_PROBES = 8
#: safety factor γ: frequencies whose estimate is below γ·RCOND_MIN get an exact SVD (§3.6)
RCOND_SCREEN_FACTOR = 1.0e4
#: the frequencies with the smallest estimates that always get an exact SVD (min_rcond, §3.6)
RCOND_EXACT_WORST = 8

_probe_cache: dict[int, np.ndarray] = {}


def _probe_vectors(K: int) -> np.ndarray:
    """(K, k) fixed complex Gaussian unit vectors (deterministic seed, independent of workers)."""
    v = _probe_cache.get(K)
    if v is None:
        rng = np.random.default_rng(0x5EED + K)
        k = min(RCOND_PROBES, K)
        v = rng.standard_normal((K, k)) + 1j * rng.standard_normal((K, k))
        v /= np.linalg.norm(v, axis=0)
        v.setflags(write=False)
        _probe_cache[K] = v
    return v


def _rcond_svd(a: np.ndarray) -> np.ndarray:
    """Exact 2-norm reciprocal condition numbers σ_min/σ_max of a batch (0 if non-finite)."""
    with np.errstate(all="ignore"):
        finite = np.all(np.isfinite(a), axis=(-2, -1))
        rc = np.zeros(a.shape[0])
        if np.any(finite):
            sv = np.linalg.svd(a[finite], compute_uv=False)
            r = sv[:, -1] / sv[:, 0]
            rc[finite] = np.where(np.isfinite(r), r, 0.0)
    return rc


def _solve_probed(a: np.ndarray, rhs: np.ndarray, s: int, check: bool, exact: bool
                  ) -> tuple[np.ndarray | None, np.ndarray, SingularReductionError | None]:
    """Batched ``solve(a, rhs)`` (a (F,K,K), rhs (F,K,R)) with the rcond estimate of §3.6.

    Returns ``(x, rc, error)`` with x (F,K,R). With ``check`` and not ``exact``, ``rc`` is the
    cheap estimate: the probe vectors are solved in the same LAPACK call as the right-hand side
    (one factorisation), L = max_j ‖A⁻¹v_j‖ ≤ ‖A⁻¹‖₂ and rc_est = 1/(‖A‖_F·L). With ``exact`` the
    SVD rcond is returned (v0.1.0 behaviour, tests/reference). ``s`` is the frequency offset used
    for the index of a singular pivot.
    """
    n_f, K, R = rhs.shape
    probe = check and not exact
    if probe:
        v = _probe_vectors(K)
        b = np.empty((n_f, K, R + v.shape[1]), dtype=complex)
        b[:, :, :R] = rhs
        b[:, :, R:] = v[None, :, :]
    else:
        b = rhs
    try:
        with np.errstate(all="ignore"):
            x = np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        bad = s
        for i in range(n_f):
            try:
                np.linalg.solve(a[i], rhs[i])
            except np.linalg.LinAlgError:
                bad = s + i
                break
        return (None, np.empty(0),
                SingularReductionError("singular port-reduction matrix", bad, 0.0))
    if not check:
        rc = np.ones(n_f)
    elif exact:
        rc = _rcond_svd(a)
    else:
        with np.errstate(all="ignore"):
            norm_a = np.sqrt(np.sum(a.real ** 2 + a.imag ** 2, axis=(1, 2)))
            lower_inv = np.sqrt(np.sum(x[..., R:].real ** 2 + x[..., R:].imag ** 2,
                                       axis=1)).max(axis=1)
            rc = 1.0 / (norm_a * lower_inv)
            rc = np.where(np.isfinite(rc) & np.all(np.isfinite(x), axis=(1, 2)), rc, 0.0)
    return x[..., :R], rc, None


def _reduce_chunk(z_cav: np.ndarray, z_load: np.ndarray, s: int, e: int, check: bool,
                  exact: bool = False, n_pads: int = 1
                  ) -> tuple[np.ndarray, np.ndarray, SingularReductionError | None]:
    """Schur reduction of frequencies s:e onto the N_pad pad ports (§2.8, §3.6); singular pivots
    are returned, not raised.

    Returns ``(z_red, rc, error)``: z_red (e−s,) for N_pad = 1, else (e−s, N_pad, N_pad). ``rc`` as
    in :func:`_solve_probed`.
    """
    N = int(n_pads)
    K = z_cav.shape[1] - N
    diag = np.arange(K)
    a = z_cav[s:e, N:, N:].copy()
    a[:, diag, diag] += z_load[s:e]
    rhs = z_cav[s:e, N:, :N]
    u, rc, err = _solve_probed(a, rhs, s, check, exact)
    if err is not None:
        return np.empty(0, dtype=complex), np.empty(0), err
    if N == 1:
        z_red = z_cav[s:e, 0, 0] - np.sum(z_cav[s:e, 0, 1:] * u[..., 0], axis=-1)
    else:
        z_red = z_cav[s:e, :N, :N] - np.matmul(z_cav[s:e, :N, N:], u)
    return z_red, rc, None


def _pads_chunk(z_pp: np.ndarray, z_via_pad: np.ndarray, s: int, e: int, check: bool,
                exact: bool = False
                ) -> tuple[np.ndarray, np.ndarray, SingularReductionError | None]:
    """Z_PAD = 1 / (1ᵀ (Z_pp,red + diag(Z_via,pad))⁻¹ 1) for frequencies s:e (§2.8)."""
    N = z_pp.shape[1]
    diag = np.arange(N)
    a = z_pp[s:e].copy()
    a[:, diag, diag] += z_via_pad[s:e, None]
    ones = np.ones((e - s, N, 1), dtype=complex)
    y, rc, err = _solve_probed(a, ones, s, check, exact)
    if err is not None:
        return np.empty(0, dtype=complex), np.empty(0), err
    with np.errstate(all="ignore"):
        z = 1.0 / np.sum(y[..., 0], axis=-1)
    return z, rc, None


def _run_checked(chunk_fn: Callable[[slice], tuple[np.ndarray, np.ndarray,
                                                   SingularReductionError | None]],
                 matrices: Callable[[np.ndarray], np.ndarray], F: int, per_f: float,
                 check: bool, cancel: Callable[[], bool] | None, workers: int,
                 progress: Callable[[float], None] | None, exact_rcond: bool,
                 what: tuple[str, str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run a chunked, probed solve over F frequencies and apply the §3.6 rcond safeguards.

    ``chunk_fn(slice)`` returns ``(values, rc, error)``; ``matrices(idx)`` builds the system
    matrices at frequency indices ``idx`` for the exact SVD. Without ``exact_rcond`` the SVD is
    only computed where the probe estimate is below γ·RCOND_MIN (γ = 1e4) and at the
    RCOND_EXACT_WORST frequencies with the smallest estimates. The ``E_SINGULAR`` decision uses
    exact values only; the first offending frequency is independent of ``workers``.
    """
    # chunks sized by memory, not flops: very small chunks lose the parallel gain to GIL
    # hand-offs between the numpy calls
    chunks = plan_chunks(F, per_f, workers,
                         target_bytes=min(SCHUR_CHUNK_BYTES, CHUNK_TARGET_BYTES))

    def done(i: int, n: int) -> None:
        if progress is not None:
            progress(i / n)

    parts = run_chunks(chunk_fn, chunks, workers, cancel=cancel, on_done=done,
                       cancelled_exc=CancelledError)
    errors = [err for _, _, err in parts if err is not None]
    if errors:
        raise min(errors, key=lambda err: err.index)
    values = np.concatenate([v for v, _, _ in parts])
    rcond = np.concatenate([rc for _, rc, _ in parts])
    if not check or exact_rcond:
        exact = np.ones(F, dtype=bool)
    else:
        # exact SVD where the estimate cannot rule out rcond < RCOND_MIN, and at the worst few
        refine = rcond < RCOND_SCREEN_FACTOR * RCOND_MIN
        refine[np.argsort(rcond, kind="stable")[:RCOND_EXACT_WORST]] = True
        idx = np.nonzero(refine)[0]
        if cancel is not None and cancel():
            raise CancelledError("computation cancelled")
        exact_parts = run_chunks(
            lambda sl: _rcond_svd(matrices(idx[sl])),
            plan_chunks(idx.size, per_f, workers,
                        target_bytes=min(SCHUR_CHUNK_BYTES, CHUNK_TARGET_BYTES)),
            workers, cancel=cancel, cancelled_exc=CancelledError)
        rcond = rcond.copy()
        rcond[idx] = np.concatenate(exact_parts) if exact_parts else rcond[idx]
        exact = refine
    if check:
        nonfinite = ~np.all(np.isfinite(values.reshape(F, -1)), axis=1)
        bad = nonfinite | (exact & (rcond < RCOND_MIN))
        if np.any(bad):
            i = int(np.argmax(bad))
            raise SingularReductionError(
                f"non-finite {what[0]}" if nonfinite[i] else
                f"ill-conditioned {what[1]} matrix (rcond = {rcond[i]:.3g})", i,
                float(rcond[i]))
    return values, rcond, exact


def _reduce(z_cav: np.ndarray, z_load: np.ndarray, check: bool = True,
            cancel: Callable[[], bool] | None = None, workers: int = 1,
            progress: Callable[[float], None] | None = None,
            exact_rcond: bool = False, n_pads: int = 1
            ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z_red, per-frequency rcond and the mask of frequencies whose rcond is exact (§3.6).

    Eliminates the decap ports P ≥ N_pad of ``z_cav`` (F,P,P) loaded by ``z_load`` (F,P−N_pad).
    Z_red is (F,) for ``n_pads`` = 1 (the v0.1 code path) and (F, N_pad, N_pad) otherwise.
    Frequency chunks run on ``workers`` threads (§3.9); see :func:`_run_checked` for the rcond
    screening.
    """
    z_cav = np.asarray(z_cav, dtype=complex)
    z_load = np.asarray(z_load, dtype=complex)
    N = int(n_pads)
    F = z_cav.shape[0]
    K = z_cav.shape[1] - N
    if K == 0:
        z_pp = z_cav[:, 0, 0].copy() if N == 1 else z_cav[:, :N, :N].copy()
        return z_pp, np.ones(F), np.ones(F, dtype=bool)
    # working set ≈ 3 (K×K) complex copies per frequency (+ the K×N right-hand side)
    per_f = 48.0 * K * K + 32.0 * K * N + 16.0 * N * N
    diag = np.arange(K)
    return _run_checked(
        lambda sl: _reduce_chunk(z_cav, z_load, sl.start, sl.stop, check, exact_rcond, N),
        lambda idx: z_cav[idx, N:, N:] + _diag_batch(z_load[idx], K, diag),
        F, per_f, check, cancel, workers, progress, exact_rcond,
        ("reduced impedance", "port-reduction"))


def _combine_pads(z_pp: np.ndarray, z_via_pad: np.ndarray, check: bool = True,
                  cancel: Callable[[], bool] | None = None, workers: int = 1,
                  progress: Callable[[float], None] | None = None,
                  exact_rcond: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z_PAD = 1/(1ᵀ(Z_pp,red + diag(Z_via,pad))⁻¹ 1) (§2.8), rcond and exact mask (§3.6)."""
    z_pp = np.asarray(z_pp, dtype=complex)
    F, N = z_pp.shape[0], z_pp.shape[1]
    zv = np.broadcast_to(np.asarray(z_via_pad, dtype=complex), (F,))
    per_f = 64.0 * N * N
    diag = np.arange(N)
    return _run_checked(
        lambda sl: _pads_chunk(z_pp, zv, sl.start, sl.stop, check, exact_rcond),
        lambda idx: z_pp[idx] + _diag_batch(np.repeat(zv[idx, None], N, axis=1), N, diag),
        F, per_f, check, cancel, workers, progress, exact_rcond,
        ("PAD impedance", "PAD-combination"))


def _diag_batch(z_load: np.ndarray, K: int, diag: np.ndarray) -> np.ndarray:
    d = np.zeros((z_load.shape[0], K, K), dtype=complex)
    d[:, diag, diag] = z_load
    return d


def reduce_ports(z_cav: np.ndarray, z_load: np.ndarray, n_pads: int = 1) -> np.ndarray:
    """z_cav (F,P,P), z_load (F,P−N_pad) → Z_red at the pad ports (§2.8, §3.6).

    (F,) for ``n_pads`` = 1, else the (F, N_pad, N_pad) Schur-reduced matrix Z_pp,red.
    Raises :class:`SingularReductionError` for exactly singular, non-finite or rcond < 1e-14 cases.
    """
    return _reduce(z_cav, z_load, n_pads=n_pads)[0]


def combine_pads(z_pp_red: np.ndarray, z_via_pad: np.ndarray) -> np.ndarray:
    """(F, N, N) Z_pp,red and (F,) or scalar Z_via,pad → (F,) Z_PAD of the N pads driven in parallel
    from an ideal common node (§2.8). Raises :class:`SingularReductionError` like
    :func:`reduce_ports`."""
    return _combine_pads(z_pp_red, z_via_pad)[0]


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
    w_dec = cluster_port_width(vias.n_pair_dec, vias.drill_diameter_m, vias.via_pitch_m)  # n_pad

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
    placement = place_ports(pwr.width_m, geoms, w_dec, w_pad, issues, source,
                            n_pads=getattr(pwr, "n_pads", 1))
    n_pads = placement.n_pads
    a, b = placement.width_m, placement.height_m
    c_plane = pair.plane_capacitance(a, b)

    # 4. cavity (Z-matrix cache keyed by geometry, plane pair, ports, sweep and mode settings)
    ckey = None
    cached = None
    if cavity_cache is not None:
        ckey = cavity_cache_key(a, b, pair, placement.xy_m, placement.port_widths_m, f_eval,
                                settings, n_pads=n_pads)
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
    # reduction stage split: decap elimination, then (N_pad > 1) the parallel pad combination
    red_end = 0.999 if n_pads == 1 else 0.9
    try:
        z_red, rcond, rcond_exact = _reduce(z_cav, z_load, check=True, cancel=cancel,
                                            workers=workers, n_pads=n_pads,
                                            progress=lambda x: report(
                                                STAGE_REDUCE + (1.0 - STAGE_REDUCE) * red_end * x))
        rconds = [rcond[rcond_exact]]
        if n_pads == 1:
            z_pad = z_red + z_via_pad
        else:
            # §2.8: Z_PAD = 1 / (1ᵀ (Z_pp,red + diag(Z_via,pad))⁻¹ 1)
            z_pad, rc_p, ex_p = _combine_pads(
                z_red, z_via_pad, check=True, cancel=cancel, workers=workers,
                progress=lambda x: report(STAGE_REDUCE + (1.0 - STAGE_REDUCE)
                                          * (red_end + (0.999 - red_end) * x)))
            rconds.append(rc_p[ex_p])
    except SingularReductionError as exc:
        f_bad = f_eval[min(exc.index, f_eval.size - 1)]
        err = issues.error(
            "E_SINGULAR",
            f"PWR {pwr.name}: {exc} at f = {f_bad:.6g} Hz. Check W_PORT_OVERLAP warnings and "
            "zero-length vias combined with ideal-short decap models.", source)
        raise InputError([err]) from None
    if not np.all(np.isfinite(z_pad)):
        err = issues.error("E_SINGULAR", f"PWR {pwr.name}: non-finite Z at the PAD.", source)
        raise InputError([err])
    z_plane = None
    if want_plane_only:
        if n_pads == 1:
            z_plane = z_cav[:, 0, 0] + z_via_pad
        else:
            z_plane = _combine_pads(z_cav[:, :n_pads, :n_pads], z_via_pad, check=False,
                                    cancel=cancel, workers=workers)[0]
    report(1.0)
    all_rc = np.concatenate(rconds)

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
        "n_pads": n_pads,
        "h_near_m": geom.h_near_m,
        "h_r_m": geom.h_r_m,
        "L_loop": l_loop,
        "w_pad_m": w_pad,
        "w_dec_m": w_dec,
        "min_rcond": float(np.min(all_rc)) if all_rc.size else 1.0,
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
