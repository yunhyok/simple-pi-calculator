"""Bridge between the GUI and the computation core (DESIGN.md §5.2, §5.4, §2.4.5, §2.5).

The GUI never imports ``core.engine`` / ``core.placement`` / ``core.via`` at module import time:
they are resolved lazily through :class:`EngineBridge`, which

* converts a :class:`~simple_pi_calculator.io.project_io.Project` to ``ProjectInputs``,
* runs ``validate_inputs`` and ``compute_project`` (§5.2),
* provides the derived geometry used by the PWR table and the placement preview.

Geometry uses ``core.placement`` / ``core.cavity`` when they are importable; otherwise the same
normative formulas of §2.4.5 and §2.5 are evaluated here (they are closed-form and cheap), so the
preview works independently of the engine. Tests replace the bridge with a fake engine by
passing another object with the same methods to ``MainWindow``.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from simple_pi_calculator.constants import (
    PAD_MARGIN_FACTOR,
    PLANE_HEIGHT_FACTOR,
    SQUARE_GMD_FACTOR,
    X_MARGIN_FACTOR,
)
from simple_pi_calculator.core.types import DecapRow, PwrRow
from simple_pi_calculator.core.units import MM
from simple_pi_calculator.errors import InputError, Issue, IssueCollector, Severity


def _optional_module(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


# =============================================================================================
# Geometry (§2.4.5, §2.5)
# =============================================================================================
def cluster_via_positions(n: int, pitch_m: float) -> np.ndarray:
    """(n,2) centred square-grid positions, row-major, cols = ceil(√n) (§2.4.5)."""
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows = int(math.ceil(n / cols))
    idx = np.arange(n)
    xy = np.column_stack([(idx % cols).astype(float), (idx // cols).astype(float)]) * pitch_m
    xy[:, 0] -= (cols - 1) * pitch_m / 2.0
    xy[:, 1] -= (rows - 1) * pitch_m / 2.0
    return xy


def cluster_port_width(n_vias: int, drill_diameter_m: float, via_pitch_m: float) -> float:
    """Via-cluster port width w_p = g_p / 0.44705 with grid pitch √2·s_v (§2.4.5)."""
    mod = _optional_module("simple_pi_calculator.core.cavity")
    if mod is not None and hasattr(mod, "cluster_port_width"):
        return float(mod.cluster_port_width(n_vias, drill_diameter_m, via_pitch_m))
    n = max(1, int(n_vias))
    r0 = drill_diameter_m / 2.0
    if n == 1:
        return r0 / SQUARE_GMD_FACTOR
    xy = cluster_via_positions(n, math.sqrt(2.0) * via_pitch_m)
    diff = xy[:, None, :] - xy[None, :, :]
    dist = np.hypot(diff[..., 0], diff[..., 1])
    np.fill_diagonal(dist, r0)
    ln_g = float(np.log(dist).sum()) / (n * n)
    return math.exp(ln_g) / SQUARE_GMD_FACTOR


def plane_height(width_m: float, distances_m: Sequence[float]) -> tuple[float, float]:
    """(H, D_ref) per §2.5.1."""
    if distances_m:
        d_ref = max(distances_m)
    else:
        d_ref = width_m / PLANE_HEIGHT_FACTOR
    return PLANE_HEIGHT_FACTOR * d_ref, d_ref


def ports_for_row(count: int, dummy: bool) -> int:
    return int(math.ceil(count / 2)) if dummy else int(count)


def caps_per_port_for_row(count: int, dummy: bool) -> list[int]:
    """c_{k,j} per §2.6.5: dummy rows carry 2 caps per port, an odd count leaves one single."""
    if not dummy:
        return [1] * int(count)
    ports = ports_for_row(count, dummy)
    caps = [2] * ports
    if count % 2 == 1 and ports:
        caps[-1] = 1
    return caps


@dataclass
class PreviewPlacement:
    """Placement data needed by the preview (subset of ``core.placement.Placement``)."""

    width_m: float
    height_m: float
    d_ref_m: float
    xy_m: np.ndarray            # (P,2), row 0 = PAD
    port_widths_m: np.ndarray   # (P,)
    caps_per_port: np.ndarray   # (P-1,)
    group_index: np.ndarray     # (P-1,)
    issues: list[Issue] = field(default_factory=list)

    @property
    def n_decap_ports(self) -> int:
        return int(len(self.xy_m) - 1)


def _local_place_ports(width_m: float, groups: Sequence[tuple[int, float, bool]],
                       w_dec: float, w_pad: float, issues: IssueCollector,
                       source: str) -> PreviewPlacement:
    """§2.5.1–§2.5.3 port placement (used when ``core.placement`` is unavailable)."""
    distances = [d for _, d, _ in groups]
    height, d_ref = plane_height(width_m, distances)
    xy: list[tuple[float, float]] = [(width_m / 2.0, PAD_MARGIN_FACTOR * d_ref)]
    widths = [w_pad]
    caps: list[int] = []
    gidx: list[int] = []
    m_x = w_dec / 2.0 + X_MARGIN_FACTOR * width_m
    l_x = width_m - 2.0 * m_x
    if groups and l_x < w_dec:
        issues.error("E_PWR_WIDTH_TOO_SMALL", "Plane width is too small for the decap ports.",
                     source)
    for k, (count, dist, dummy) in enumerate(groups):
        if dist <= 0:
            issues.error("E_DECAP_DISTANCE", "Distance to PAD must be > 0.", source)
            continue
        p_k = ports_for_row(count, dummy)
        if p_k <= 0:
            continue
        span = max(l_x, 0.0)
        if p_k and span / p_k >= w_dec:
            n_row = p_k
        else:
            n_row = max(1, int(math.floor(span / w_dec))) if w_dec > 0 else p_k
        r_k = int(math.ceil(p_k / n_row))
        y_k = PAD_MARGIN_FACTOR * d_ref + dist
        cpp = caps_per_port_for_row(count, dummy)
        j = 0
        for r in range(r_k):
            n_r = min(n_row, p_k - r * n_row)
            y_r = y_k + (r - (r_k - 1) / 2.0) * w_dec
            for i in range(n_r):
                x = m_x + (i + 0.5) * span / n_r
                y = min(max(y_r, w_dec / 2.0), height - w_dec / 2.0)
                xy.append((x, y))
                widths.append(w_dec)
                caps.append(cpp[j])
                gidx.append(k)
                j += 1
    return PreviewPlacement(width_m, height, d_ref, np.asarray(xy, dtype=float),
                            np.asarray(widths, dtype=float), np.asarray(caps, dtype=int),
                            np.asarray(gidx, dtype=int), list(issues.issues))


# =============================================================================================
# Bridge
# =============================================================================================
class EngineUnavailableError(RuntimeError):
    """The computation engine modules cannot be imported."""


class EngineBridge:
    """Default bridge to ``core.engine`` (§5.2). All methods are safe to call from the GUI thread;
    :meth:`compute` is called from the worker thread."""

    ENGINE_MODULE = "simple_pi_calculator.core.engine"

    def __init__(self) -> None:
        self._import_error: str | None = None
        self._cavity_cache: Any = None  # core.cavity.CavityCache, created on first compute (§3.9)

    # -- engine -----------------------------------------------------------------------------------
    def _engine(self) -> Any:
        try:
            return importlib.import_module(self.ENGINE_MODULE)
        except Exception as exc:  # noqa: BLE001 - any import failure means "unavailable"
            self._import_error = f"{type(exc).__name__}: {exc}"
            raise EngineUnavailableError(
                "The computation engine could not be loaded "
                f"({self._import_error}).") from exc

    def available(self) -> bool:
        try:
            self._engine()
        except EngineUnavailableError:
            return False
        return True

    @property
    def cancelled_exceptions(self) -> tuple[type[BaseException], ...]:
        try:
            eng = self._engine()
        except EngineUnavailableError:
            return ()
        exc = getattr(eng, "CancelledError", None)
        return (exc,) if isinstance(exc, type) else ()

    def make_inputs(self, project: Any, project_path: str | None) -> Any:
        """``ProjectInputs`` (deep copy of the project state, §5.4)."""
        self._engine()
        from simple_pi_calculator.io.project_io import to_inputs
        return to_inputs(project, project_path)

    def validate(self, inputs: Any) -> list[Issue]:
        return list(self._engine().validate_inputs(inputs))

    def compute(self, inputs: Any, progress: Callable[[float, str], None] | None,
                cancel: Callable[[], bool] | None) -> tuple[list[Any], list[Issue]]:
        eng = self._engine()
        if self._cavity_cache is None and hasattr(eng, "CavityCache"):
            self._cavity_cache = eng.CavityCache()
        kwargs: dict[str, Any] = {}
        if self._cavity_cache is not None:
            # cavity Z-matrices survive between runs: changing only decap models, via model or
            # mounting inductance reuses them (§3.9); ``inputs.workers`` sets the thread count
            kwargs["cavity_cache"] = self._cavity_cache
        results, issues = eng.compute_project(inputs, progress=progress, cancel=cancel, **kwargs)
        return list(results), list(issues)

    # -- geometry ---------------------------------------------------------------------------------
    @staticmethod
    def port_widths_m(project: Any) -> tuple[float, float]:
        """(w_pad, w_dec) in metres (§2.4.5)."""
        v = project.vias
        drill = v.drill_diameter_mm * MM
        pitch = v.via_pitch_mm * MM
        w_pad = cluster_port_width(max(1, int(v.pad_via_count)), drill, pitch)
        w_dec = cluster_port_width(max(1, int(v.vias_per_pad)), drill, pitch)
        return w_pad, w_dec

    @staticmethod
    def enabled_groups(project: Any, pwr_name: str) -> list[DecapRow]:
        return [r for r in project.decap_rows if r.enabled and r.pwr_name == pwr_name]

    def placement(self, project: Any, pwr: PwrRow) -> PreviewPlacement | None:
        """Derived placement of one PWR, or ``None`` if the geometry is invalid."""
        width_m = pwr.width_mm * MM
        if not (width_m > 0):
            return None
        rows = [r for r in self.enabled_groups(project, pwr.name)
                if r.count >= 1 and r.distance_mm > 0]
        try:
            w_pad, w_dec = self.port_widths_m(project)
        except (ValueError, ZeroDivisionError, OverflowError):
            return None
        issues = IssueCollector()
        source = f"PWR:{pwr.name}"
        mod = _optional_module("simple_pi_calculator.core.placement")
        if mod is not None and hasattr(mod, "place_ports") and hasattr(mod, "DecapGroupGeom"):
            try:
                groups = [mod.DecapGroupGeom(count=int(r.count), distance_m=r.distance_mm * MM,
                                             dummy=bool(r.dummy)) for r in rows]
                pl = mod.place_ports(width_m, groups, w_dec, w_pad, issues, source)
                return PreviewPlacement(
                    float(pl.width_m), float(pl.height_m), float(pl.d_ref_m),
                    np.asarray(pl.xy_m, dtype=float),
                    np.asarray(getattr(pl, "port_widths_m",
                                       [w_pad] + [w_dec] * (len(pl.xy_m) - 1)), dtype=float),
                    np.asarray(pl.caps_per_port, dtype=int),
                    np.asarray(pl.group_index, dtype=int), list(issues.issues))
            except InputError:
                return None
            except Exception:  # noqa: BLE001 - fall back to the local formulas
                issues = IssueCollector()
        groups_t = [(int(r.count), r.distance_mm * MM, bool(r.dummy)) for r in rows]
        pl = _local_place_ports(width_m, groups_t, w_dec, w_pad, issues, source)
        if any(i.severity is Severity.ERROR for i in pl.issues):
            return None
        return pl

    @staticmethod
    def plane_pair(project: Any, pwr: PwrRow) -> tuple[Any | None, list[Issue]]:
        """``core.stackup.PlanePair`` of a PWR row (or ``None``) and the issues found."""
        from simple_pi_calculator.core.stackup import derive_plane_pair
        issues = IssueCollector()
        try:
            stackup = project.stackup()
            pair = derive_plane_pair(stackup, int(pwr.pwr_layer), int(pwr.gnd_layer), issues,
                                     f"PWR:{pwr.name}")
        except InputError:
            return None, issues.issues
        except Exception as exc:  # noqa: BLE001 - malformed stack-up while editing
            issues.error("E_PWR_LAYER_NOT_FOUND", str(exc), f"PWR:{pwr.name}")
            return None, issues.issues
        return pair, issues.issues

    @staticmethod
    def via_summary(project: Any, pwr: PwrRow) -> dict[str, float]:
        """h_near (mm) and, when ``core.via`` is available, L_loop (nH) of a PWR (§2.6.1)."""
        out: dict[str, float] = {}
        try:
            stackup = project.stackup()
            nearer = min(int(pwr.pwr_layer), int(pwr.gnd_layer))
            out["h_near_mm"] = stackup.z_top(nearer) / MM
        except Exception:  # noqa: BLE001
            return out
        via = _optional_module("simple_pi_calculator.core.via")
        if via is not None:
            try:
                v, a = project.vias, project.advanced
                vs = via.ViaSettings(
                    drill_diameter_m=v.drill_diameter_mm * MM,
                    antipad_diameter_m=v.antipad_diameter_mm * MM,
                    via_pitch_m=v.via_pitch_mm * MM, vias_per_pad=int(v.vias_per_pad),
                    pad_via_count=int(v.pad_via_count), model=a.via_model,
                    plating_thickness_m=a.plating_thickness_mm * MM,
                    conductivity=a.via_conductivity_s_per_m)
                geom = via.via_geometry(stackup, int(pwr.pwr_layer), int(pwr.gnd_layer),
                                        IssueCollector())
                out["h_near_mm"] = geom.h_near_m / MM
                out["l_loop_nh"] = via.loop_inductance(geom, vs) / 1e-9
            except Exception:  # noqa: BLE001 - derived label only
                pass
        return out


__all__ = [
    "EngineBridge",
    "EngineUnavailableError",
    "PreviewPlacement",
    "cluster_port_width",
    "cluster_via_positions",
    "plane_height",
    "ports_for_row",
    "caps_per_port_for_row",
]
