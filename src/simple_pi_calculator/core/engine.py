"""Project-level computation: validation, per-PWR isolation, progress and cancel (DESIGN.md §5.2,
§5.4, Appendix B). Qt-free."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from simple_pi_calculator.constants import (F_START_MIN_HZ, F_STOP_MAX_HZ, F_STOP_WARN_HZ,
                                            MARKER_FREQUENCIES_HZ, N_POINTS_MAX, N_POINTS_MIN)
from simple_pi_calculator.core.cavity import CancelledError
from simple_pi_calculator.core.decap_model import DecapModelCache
from simple_pi_calculator.core.pdn import DecapGroup, PwrResult, PwrSpec, compute_pwr
from simple_pi_calculator.core.stackup import Stackup, check_pwr_layers
from simple_pi_calculator.core.types import DecapRow, resolve_model_path
from simple_pi_calculator.core.via import ViaSettings, validate_via_settings
from simple_pi_calculator.errors import InputError, Issue, IssueCollector

__all__ = [
    "ProjectInputs",
    "CancelledError",
    "validate_inputs",
    "compute_project",
    "frequency_grid",
    "enabled_rows_for_pwr",
    "default_model_cache",
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
    marker_f_hz: tuple[float, ...] = field(default=MARKER_FREQUENCIES_HZ)


_DEFAULT_CACHE = DecapModelCache()


def default_model_cache() -> DecapModelCache:
    """Module-level decap model cache shared by successive computations (keyed by file mtime)."""
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
        path = resolve_model_path(row.model_file, None, inputs.project_dir,
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
def compute_project(inputs: ProjectInputs,
                    progress: Callable[[float, str], None] | None = None,
                    cancel: Callable[[], bool] | None = None,
                    cache: DecapModelCache | None = None,
                    ) -> tuple[list[PwrResult], list[Issue]]:
    """Validates, computes every enabled PWR; per-PWR errors do not abort other PWRs.

    Global input errors (stack-up, sweep, vias) return ``([], issues)``. A failed PWR is omitted
    from the results; its errors carry ``source = "PWR:<name>"``. Raises :class:`CancelledError`.
    """
    cache = cache if cache is not None else _DEFAULT_CACHE
    issues = IssueCollector()
    _validate_global(inputs, issues)
    if issues.has_errors():
        return [], issues.issues

    def check_cancel() -> None:
        if cancel is not None and cancel():
            raise CancelledError("computation cancelled")

    grid = frequency_grid(inputs)
    results: list[PwrResult] = []
    n_pwr = max(1, len(inputs.pwrs))
    for ip, pwr in enumerate(inputs.pwrs):
        check_cancel()
        base = ip / n_pwr
        if progress is not None:
            progress(base, f"{pwr.name}: loading decap models")
        pwr_issues = IssueCollector()
        try:
            paths = _validate_pwr(inputs, pwr, pwr_issues)
            pwr_issues.raise_if_errors()
            groups = []
            for row, path in zip(enabled_rows_for_pwr(inputs.decap_rows, pwr.name), paths):
                mode = row.s2p_mode or inputs.s2p_default_mode
                model = cache.get(path, row.subckt, mode, pwr_issues)  # type: ignore[arg-type]
                groups.append(DecapGroup(pwr_name=pwr.name, model=model, count=int(row.count),
                                         distance_m=float(row.distance_m), dummy=bool(row.dummy)))

            def sub(frac: float, _base=base, _name=pwr.name) -> None:
                if progress is not None:
                    stage = ("decap models" if frac < 0.10 else "static mode sums" if frac < 0.70
                             else "dynamic mode sums" if frac < 0.95 else "port reduction")
                    progress(_base + frac / n_pwr, f"{_name}: {stage}")

            result = compute_pwr(inputs.stackup, pwr, groups, inputs.vias, grid,
                                 list(inputs.marker_f_hz), inputs.show_plane_only, pwr_issues,
                                 progress=sub, cancel=cancel)
            results.append(result)
        except CancelledError:
            raise
        except InputError as exc:
            known = {id(i) for i in pwr_issues.issues}
            for i in exc.issues:
                if id(i) not in known:
                    pwr_issues.issues.append(i)
        finally:
            for i in pwr_issues.issues:
                src = i.source or f"PWR:{pwr.name}"
                if i.source is None:
                    i = Issue(i.code, i.severity, i.message, src, i.location)
                issues.issues.append(i)
    if progress is not None:
        progress(1.0, "done")
    return results, issues.issues
