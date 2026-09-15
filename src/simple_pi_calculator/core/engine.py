"""Project-level computation: validation, per-PWR isolation, progress and cancel (DESIGN.md §5.2,
§5.4, Appendix B). Qt-free."""

from __future__ import annotations

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from simple_pi_calculator.constants import (F_START_MIN_HZ, F_STOP_MAX_HZ, F_STOP_WARN_HZ,
                                            MARKER_FREQUENCIES_HZ, N_POINTS_MAX, N_POINTS_MIN)
from simple_pi_calculator.core.cavity import CancelledError, CavityCache
from simple_pi_calculator.core.decap_model import DecapModelCache
from simple_pi_calculator.core.parallel import blas_limited, resolve_workers
from simple_pi_calculator.core.pdn import (STAGE_DYNAMIC, STAGE_REDUCE, STAGE_STATIC, DecapGroup,
                                           PwrResult, PwrSpec, compute_pwr)
from simple_pi_calculator.core.stackup import Stackup, check_pwr_layers
from simple_pi_calculator.core.types import DecapRow, resolve_model_path
from simple_pi_calculator.core.via import ViaSettings, validate_via_settings
from simple_pi_calculator.errors import InputError, Issue, IssueCollector

log = logging.getLogger(__name__)

__all__ = [
    "ProjectInputs",
    "CancelledError",
    "validate_inputs",
    "compute_project",
    "frequency_grid",
    "enabled_rows_for_pwr",
    "default_model_cache",
    "default_cavity_cache",
    "CavityCache",
]


@dataclass
class ProjectInputs:
    """Pure-python mirror of the project JSON, SI-converted (§5.2)."""

    stackup: Stackup
    vias: ViaSettings
    pwrs: list[PwrSpec]
    decap_rows: list[DecapRow]
    f_start_hz: float = 1e5
    f_stop_hz: float = 1e9
    n_points: int = 400
    show_plane_only: bool = False
    project_dir: str | None = None
    model_search_dir: str | None = None
    s2p_default_mode: str = "series"
    #: folder of the decap-list Excel file: first relative candidate of §4.4 (the GUI table uses
    #: the same order, so a row shown as resolved must also resolve at compute time)
    decap_source_dir: str | None = None
    marker_f_hz: tuple[float, ...] = field(default=MARKER_FREQUENCIES_HZ)
    #: worker threads (``advanced.workers``); 0 = auto = os.cpu_count() (§3.9)
    workers: int = 0


_DEFAULT_CACHE = DecapModelCache()
_DEFAULT_CAVITY_CACHE = CavityCache()


def default_cavity_cache() -> CavityCache:
    """Module-level cavity Z-matrix cache shared by successive computations (§3.9)."""
    return _DEFAULT_CAVITY_CACHE


def default_model_cache() -> DecapModelCache:
    """Module-level decap model cache shared by successive computations (keyed by file content)."""
    return _DEFAULT_CACHE


def frequency_grid(inputs: ProjectInputs) -> np.ndarray:
    """f = geomspace(f_start, f_stop, n_points) (§3.1)."""
    return np.geomspace(float(inputs.f_start_hz), float(inputs.f_stop_hz), int(inputs.n_points))


def enabled_rows_for_pwr(rows: Sequence[DecapRow], pwr_name: str) -> list[DecapRow]:
    """Enabled decap rows of one PWR in table order (§2.5.1)."""
    return [r for r in rows if r.enabled and r.pwr_name == pwr_name]


# =============================================================================================
# Validation
# =============================================================================================
def _validate_global(inputs: ProjectInputs, issues: IssueCollector) -> None:
    inputs.stackup.validate(issues, "Stack-up")
    fs, fe, n = inputs.f_start_hz, inputs.f_stop_hz, inputs.n_points
    if not (math.isfinite(fs) and math.isfinite(fe)) or not (F_START_MIN_HZ <= fs < fe) \
            or fe > F_STOP_MAX_HZ:
        issues.error("E_SWEEP_RANGE",
                     f"Invalid sweep range {fs:g}–{fe:g} Hz (require 1 kHz ≤ f_start < f_stop ≤ "
                     "20 GHz).", "Sweep")
    elif fe > F_STOP_WARN_HZ:
        issues.warning("W_SWEEP_HIGH", f"f_stop = {fe:g} Hz exceeds 3 GHz: cavity/via model "
                       "validity degrades (d ≪ λ, no radiation, no via capacitance).", "Sweep")
    if int(n) != n or not (N_POINTS_MIN <= n <= N_POINTS_MAX):
        issues.error("E_SWEEP_RANGE", f"Number of points {n} outside [{N_POINTS_MIN}, "
                     f"{N_POINTS_MAX}].", "Sweep")
    validate_via_settings(inputs.vias, issues, "Vias")
    names: set[str] = set()
    for p in inputs.pwrs:
        if p.name in names:
            issues.error("E_PWR_NAME_DUP", f"Duplicate PWR name {p.name!r}.", f"PWR:{p.name}")
        names.add(p.name)


def _validate_pwr(inputs: ProjectInputs, pwr: PwrSpec, issues: IssueCollector) -> list[str]:
    """Per-PWR checks; returns resolved model paths for the enabled rows (None if unresolved)."""
    source = f"PWR:{pwr.name}"
    if not (pwr.width_m > 0.0):
        issues.error("E_PWR_DIM", f"PWR {pwr.name}: width must be > 0.", source)
    n_pads = getattr(pwr, "n_pads", 1)
    if isinstance(n_pads, bool) or not isinstance(n_pads, (int, float)) or int(n_pads) != n_pads \
            or n_pads < 1:
        issues.error("E_PWR_NPADS", f"PWR {pwr.name}: number of PADs must be an integer ≥ 1 "
                     f"(got {n_pads}).", source)
    if inputs.stackup.layers:
        check_pwr_layers(inputs.stackup, pwr.pwr_layer, pwr.gnd_layer, issues, source)
    paths = []
    for row in enabled_rows_for_pwr(inputs.decap_rows, pwr.name):
        if not (row.distance_m > 0.0):
            issues.error("E_DECAP_DISTANCE", f"Decap row {row.model_file!r}: distance to PAD must "
                         "be > 0.", source)
        if int(row.count) < 1:
            issues.error("E_DECAP_COUNT", f"Decap row {row.model_file!r}: count must be ≥ 1.",
                         source)
        path = resolve_model_path(row.model_file, inputs.decap_source_dir, inputs.project_dir,
                                  inputs.model_search_dir)
        if path is None:
            issues.error("E_DECAP_FILE_NOT_FOUND", f"Decap model file not found: "
                         f"{row.model_file!r}.", source)
        paths.append(path)
    return paths


def validate_inputs(inputs: ProjectInputs) -> list[Issue]:
    """Synchronous pre-compute validation (§5.4): global and per-PWR input checks.

    Cheap checks only (no model parsing, no geometry errors that need the placement); those are
    reported by :func:`compute_project` per PWR.
    """
    issues = IssueCollector()
    _validate_global(inputs, issues)
    known = {p.name for p in inputs.pwrs}
    for row in inputs.decap_rows:
        if row.enabled and row.pwr_name not in known:
            issues.warning("W_DECAP_PWR_UNKNOWN", f"Decap row {row.model_file!r} refers to PWR "
                           f"{row.pwr_name!r}, which is not an enabled PWR; row ignored.",
                           "Decaps")
    for p in inputs.pwrs:
        _validate_pwr(inputs, p, issues)
    return issues.issues


# =============================================================================================
# Computation
# =============================================================================================
def _net_task(inputs: ProjectInputs, pwr: PwrSpec, grid: np.ndarray, cache: DecapModelCache,
              cavity_cache: CavityCache | None, inner_workers: int,
              progress: Callable[[float], None], stage_text: Callable[[str], None],
              cancel: Callable[[], bool] | None) -> tuple[PwrResult | None, list[Issue]]:
    """Compute one PWR net with per-net error isolation (§5.2); raises CancelledError only."""
    pwr_issues = IssueCollector()
    result = None
    try:
        stage_text("loading decap models")
        paths = _validate_pwr(inputs, pwr, pwr_issues)
        pwr_issues.raise_if_errors()
        groups = []
        for row, path in zip(enabled_rows_for_pwr(inputs.decap_rows, pwr.name), paths):
            mode = row.s2p_mode or inputs.s2p_default_mode
            model = cache.get(path, row.subckt, mode, pwr_issues)  # type: ignore[arg-type]
            groups.append(DecapGroup(pwr_name=pwr.name, model=model, count=int(row.count),
                                     distance_m=float(row.distance_m), dummy=bool(row.dummy)))

        def sub(frac: float) -> None:
            stage_text("decap models" if frac < STAGE_STATIC else
                       "static mode sums" if frac < STAGE_DYNAMIC else
                       "dynamic mode sums" if frac < STAGE_REDUCE else "port reduction")
            progress(frac)

        result = compute_pwr(inputs.stackup, pwr, groups, inputs.vias, grid,
                             list(inputs.marker_f_hz), inputs.show_plane_only, pwr_issues,
                             progress=sub, cancel=cancel, workers=inner_workers,
                             cavity_cache=cavity_cache)
    except CancelledError:
        raise
    except InputError as exc:
        known = {id(i) for i in pwr_issues.issues}
        for i in exc.issues:
            if id(i) not in known:
                pwr_issues.issues.append(i)
    except Exception as exc:  # noqa: BLE001 - isolate unexpected failures per PWR (§5.2)
        log.exception("Computation of PWR %s failed", pwr.name)
        pwr_issues.error("E_PWR_INTERNAL", f"PWR {pwr.name}: unexpected error "
                         f"({type(exc).__name__}: {exc}); other PWRs are not affected. "
                         "See the log file for details.", f"PWR:{pwr.name}")
    if result is None:
        tag = f"PWR:{pwr.name}"
        errors = [i for i in pwr_issues.issues if i.severity.name == "ERROR"]
        if not any(i.source in (None, tag) for i in errors):
            # errors raised with a file as source (model parse/read errors, §4.5) must still be
            # attributable to the net: the GUI marks failed nets and exports skip them by
            # ``source == "PWR:<name>"`` (§5.2; review v0.2)
            first = errors[0] if errors else None
            pwr_issues.error("E_PWR_FAILED",
                             f"PWR {pwr.name} was not computed"
                             + (f": {first.code} in {first.source}." if first else "."),
                             tag, first.source if first else None)
    out = []
    for i in pwr_issues.issues:
        if i.source is None:
            i = Issue(i.code, i.severity, i.message, f"PWR:{pwr.name}", i.location)
        out.append(i)
    return result, out


def compute_project(inputs: ProjectInputs,
                    progress: Callable[[float, str], None] | None = None,
                    cancel: Callable[[], bool] | None = None,
                    cache: DecapModelCache | None = None,
                    workers: int | None = None,
                    cavity_cache: CavityCache | None | bool = True,
                    ) -> tuple[list[PwrResult], list[Issue]]:
    """Validates, computes every enabled PWR; per-PWR errors do not abort other PWRs.

    Global input errors (stack-up, sweep, vias) return ``([], issues)``. A failed PWR is omitted
    from the results; its errors carry ``source = "PWR:<name>"``. Raises :class:`CancelledError`.

    ``workers`` (None → ``inputs.workers``; 0 → auto = ``os.cpu_count()``) worker threads are
    shared between concurrently computed PWR nets and the frequency chunks inside each net
    (§3.9). ``cavity_cache``: ``True`` = module default cache, ``None``/``False`` = no caching, or
    a :class:`CavityCache`. Results, their order and the issue order do not depend on either.
    """
    cache = cache if cache is not None else _DEFAULT_CACHE
    if cavity_cache is True:
        cavity_cache = _DEFAULT_CAVITY_CACHE
    elif cavity_cache is False:
        cavity_cache = None
    n_workers = resolve_workers(inputs.workers if workers is None else workers)
    issues = IssueCollector()
    _validate_global(inputs, issues)
    if issues.has_errors():
        return [], issues.issues

    if cancel is not None and cancel():
        raise CancelledError("computation cancelled")

    grid = frequency_grid(inputs)
    pwrs = list(inputs.pwrs)
    n_pwr = max(1, len(pwrs))
    fracs = [0.0] * len(pwrs)
    texts = [""] * len(pwrs)
    lock = threading.Lock()
    emitted = [0.0]

    def make_progress(ip: int) -> tuple[Callable[[float], None], Callable[[str], None]]:
        def prog(frac: float) -> None:
            if progress is None:
                return
            with lock:
                fracs[ip] = max(fracs[ip], min(1.0, frac))
                total = min(1.0, max(emitted[0], sum(fracs) / n_pwr))
                emitted[0] = total
                progress(total, f"{pwrs[ip].name}: {texts[ip]}")

        def text(msg: str) -> None:
            if texts[ip] != msg:
                texts[ip] = msg
                prog(fracs[ip])
        return prog, text

    outer = min(n_workers, len(pwrs)) if pwrs else 1
    inner = max(1, n_workers // max(1, outer))
    outcomes: list[tuple[PwrResult | None, list[Issue]]] = []
    with blas_limited(1):
        if outer <= 1:
            for ip, pwr in enumerate(pwrs):
                if cancel is not None and cancel():
                    raise CancelledError("computation cancelled")
                prog, text = make_progress(ip)
                outcomes.append(_net_task(inputs, pwr, grid, cache, cavity_cache, inner,
                                          prog, text, cancel))
        else:
            stop = threading.Event()

            def poll() -> bool:
                if stop.is_set():
                    return True
                if cancel is not None and cancel():
                    stop.set()
                    return True
                return False

            def run(ip: int) -> tuple[PwrResult | None, list[Issue]]:
                if poll():
                    raise CancelledError("computation cancelled")
                prog, text = make_progress(ip)
                try:
                    return _net_task(inputs, pwrs[ip], grid, cache, cavity_cache, inner,
                                     prog, text, poll)
                except CancelledError:
                    stop.set()
                    raise

            with ThreadPoolExecutor(max_workers=outer, thread_name_prefix="spical-pwr") as pool:
                futures = [pool.submit(run, ip) for ip in range(len(pwrs))]
                cancelled = False
                for fut in futures:
                    try:
                        outcomes.append(fut.result())
                    except CancelledError:
                        cancelled = True
                if cancelled:
                    raise CancelledError("computation cancelled")
    results = [r for r, _ in outcomes if r is not None]
    for _, net_issues in outcomes:
        issues.issues.extend(net_issues)
    if progress is not None:
        progress(1.0, "done")
    return results, issues.issues
