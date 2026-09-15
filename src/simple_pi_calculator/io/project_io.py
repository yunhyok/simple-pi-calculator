"""Project files (``*.spical.json``) and the Qt-free auto-save store (DESIGN.md §4.7, §5.3, §5.8).

In memory a :class:`Project` holds mm-valued inputs and **absolute** paths wherever they could be
resolved. Named project files store paths relative to the project folder (same drive), the
auto-save stores absolute paths; both use forward slashes.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from simple_pi_calculator import __version__
from simple_pi_calculator.constants import (
    APP_NAME,
    APPDATA_ENV_VAR,
    DEFAULT_ANTIPAD_DIAMETER_MM,
    DEFAULT_DRILL_DIAMETER_MM,
    DEFAULT_F_START_HZ,
    DEFAULT_F_STOP_HZ,
    DEFAULT_MOUNTING_INDUCTANCE_NH,
    DEFAULT_N_POINTS,
    DEFAULT_PAD_VIA_COUNT,
    DEFAULT_PLATING_THICKNESS_MM,
    DEFAULT_S2P_MODE,
    DEFAULT_VIA_CONDUCTIVITY_S_PER_M,
    DEFAULT_VIA_MODEL,
    DEFAULT_VIA_PITCH_MM,
    DEFAULT_VIAS_PER_DECAP,
    DEFAULT_Z_UNIT,
    MAX_RECENT_FILES,
    PROJECT_FORMAT,
    PROJECT_SUFFIX,
    QUARANTINE_KEEP,
    S2P_MODES,
    VIA_MODELS,
    Z_UNITS,
)
from simple_pi_calculator.core.stackup import Stackup
from simple_pi_calculator.core.types import DecapRow, LayerRow, PwrRow, stackup_from_rows
from simple_pi_calculator.core.units import MM, NH
from simple_pi_calculator.errors import (
    Issue,
    IssueCollector,
    ProjectFormatError,
    ProjectTooNewError,
)
from simple_pi_calculator.io import migrations


# =============================================================================================
# Data classes (§4.7, §5.3)
# =============================================================================================
@dataclass
class ViaInputs:
    """``vias`` block (§4.7), mm-valued."""

    drill_diameter_mm: float = DEFAULT_DRILL_DIAMETER_MM
    antipad_diameter_mm: float = DEFAULT_ANTIPAD_DIAMETER_MM
    via_pitch_mm: float = DEFAULT_VIA_PITCH_MM
    vias_per_decap: int = DEFAULT_VIAS_PER_DECAP
    pad_via_count: int = DEFAULT_PAD_VIA_COUNT


@dataclass
class AdvancedSettings:
    """``advanced`` block (§4.7)."""

    via_model: str = DEFAULT_VIA_MODEL
    plating_thickness_mm: float = DEFAULT_PLATING_THICKNESS_MM
    via_conductivity_s_per_m: float = DEFAULT_VIA_CONDUCTIVITY_S_PER_M
    mounting_inductance_nh: float = DEFAULT_MOUNTING_INDUCTANCE_NH
    s2p_default_mode: str = DEFAULT_S2P_MODE
    model_search_dir: str | None = None


@dataclass
class SweepSettings:
    """``sweep`` block (§4.7, §3.1)."""

    f_start_hz: float = DEFAULT_F_START_HZ
    f_stop_hz: float = DEFAULT_F_STOP_HZ
    n_points: int = DEFAULT_N_POINTS
    show_plane_only: bool = False


@dataclass
class DisplaySettings:
    """``display`` block (§4.7)."""

    z_unit: str = DEFAULT_Z_UNIT


@dataclass
class Project:
    """mm-valued, JSON-mirroring project inputs with defaults (§4.7, §5.3).

    ``migrated_from`` / ``loaded_from`` are runtime bookkeeping (not serialised, not compared):
    the original schema version of a migrated file and the path it was loaded from (§5.8.4).
    """

    stackup_source_path: str | None = None
    layers: list[LayerRow] = field(default_factory=list)
    vias: ViaInputs = field(default_factory=ViaInputs)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)
    pwr_source_path: str | None = None
    pwr_rows: list[PwrRow] = field(default_factory=list)
    decap_source_path: str | None = None
    decap_rows: list[DecapRow] = field(default_factory=list)
    sweep: SweepSettings = field(default_factory=SweepSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    migrated_from: int | None = field(default=None, compare=False, repr=False)
    loaded_from: str | None = field(default=None, compare=False, repr=False)

    def stackup(self) -> Stackup:
        """SI stack-up built from ``layers``."""
        return stackup_from_rows(self.layers)

    def has_table_rows(self) -> bool:
        return bool(self.layers or self.pwr_rows or self.decap_rows)


@dataclass
class WindowState:
    """``session.window`` (§4.7)."""

    geometry_b64: str = ""
    state_b64: str = ""
    splitter_sizes: list[int] = field(default_factory=list)
    input_tab: int = 0
    result_tab: str | None = None
    decap_filter: str | None = None
    message_dock_visible: bool = True


@dataclass
class PlotView:
    """``session.plots[<PWR>]`` (§4.7). Ranges are log10 values."""

    auto_range: bool = True
    x_range_log10: tuple[float, float] | None = None
    y_range_log10: tuple[float, float] | None = None


@dataclass
class Session:
    """``session`` block of the auto-save file (§4.7, §5.8)."""

    project_path: str | None = None
    modified: bool = False
    recent_files: list[str] = field(default_factory=list)
    window: WindowState = field(default_factory=WindowState)
    plots: dict[str, PlotView] = field(default_factory=dict)
    had_results: bool = False
    saved_utc: str | None = None


# =============================================================================================
# Path helpers (§4.7)
# =============================================================================================
def _forward(path: str) -> str:
    return path.replace("\\", "/")


def _path_out(path: str | None, anchor_dir: str | None) -> str | None:
    """Serialise a path: relative to ``anchor_dir`` when on the same drive, forward slashes."""
    if path is None:
        return None
    if anchor_dir and os.path.isabs(path):
        a_drive = os.path.splitdrive(os.path.abspath(anchor_dir))[0].lower()
        p_drive = os.path.splitdrive(path)[0].lower()
        if a_drive == p_drive:
            try:
                return _forward(os.path.relpath(path, os.path.abspath(anchor_dir)))
            except ValueError:  # pragma: no cover - different mount (Windows)
                pass
    return _forward(path)


def _path_in(path: str | None, anchor_dir: str | None) -> str | None:
    """Deserialise a path: relative paths are joined with ``anchor_dir`` when given."""
    if path is None:
        return None
    text = os.path.expanduser(path)
    if os.path.isabs(text):
        return os.path.normpath(text)
    if anchor_dir:
        return os.path.normpath(os.path.join(os.path.abspath(anchor_dir), text))
    return text


def _model_path_in(path: str, anchor_dir: str | None, excel_dir: str | None,
                   search_dir: str | None) -> str:
    """Relative ``model_file``: first existing of (Excel folder, project folder, search dir) per
    §4.4, else joined with the project folder (or left relative without an anchor)."""
    text = os.path.expanduser(path)
    if os.path.isabs(text):
        return os.path.normpath(text)
    for folder in (excel_dir, os.path.abspath(anchor_dir) if anchor_dir else None, search_dir):
        if folder:
            candidate = os.path.normpath(os.path.join(folder, text))
            if os.path.isfile(candidate):
                return candidate
    return _path_in(text, anchor_dir) or text


def ensure_project_suffix(name: str) -> str:
    """Save-As suffix rule (§5.8.5): append ``.spical.json`` unless present (case-insensitive),
    replacing a bare ``.json`` suffix."""
    lower = name.lower()
    if lower.endswith(PROJECT_SUFFIX):
        return name
    if lower.endswith(".json"):
        return name[: -len(".json")] + PROJECT_SUFFIX
    return name + PROJECT_SUFFIX


def project_stem(path: str) -> str:
    """File name without ``.spical.json`` / ``.json`` (window title, backups)."""
    base = os.path.basename(path)
    lower = base.lower()
    if lower.endswith(PROJECT_SUFFIX):
        return base[: -len(PROJECT_SUFFIX)]
    if lower.endswith(".json"):
        return base[: -len(".json")]
    return base


# =============================================================================================
# Serialisation
# =============================================================================================
def _layer_to_dict(row: LayerRow) -> dict[str, Any]:
    return {"number": row.number, "name": row.name, "thickness_mm": float(row.thickness_mm),
            "conductivity_s_per_m": None if row.conductivity_s_per_m is None
            else float(row.conductivity_s_per_m),
            "dk": None if row.dk is None else float(row.dk),
            "df": None if row.df is None else float(row.df)}


def _session_to_dict(session: Session) -> dict[str, Any]:
    w = session.window
    return {
        "project_path": None if session.project_path is None else _forward(session.project_path),
        "modified": bool(session.modified),
        "recent_files": [_forward(p) for p in session.recent_files[:MAX_RECENT_FILES]],
        "window": {"geometry_b64": w.geometry_b64, "state_b64": w.state_b64,
                   "splitter_sizes": [int(s) for s in w.splitter_sizes],
                   "input_tab": int(w.input_tab), "result_tab": w.result_tab,
                   "decap_filter": w.decap_filter,
                   "message_dock_visible": bool(w.message_dock_visible)},
        "plots": {name: {"auto_range": bool(v.auto_range),
                         "x_range_log10": None if v.x_range_log10 is None
                         else [float(v.x_range_log10[0]), float(v.x_range_log10[1])],
                         "y_range_log10": None if v.y_range_log10 is None
                         else [float(v.y_range_log10[0]), float(v.y_range_log10[1])]}
                  for name, v in session.plots.items()},
        "had_results": bool(session.had_results),
        "saved_utc": session.saved_utc,
    }


def project_to_dict(project: Project, anchor_dir: str | None,
                    session: Session | None) -> dict[str, Any]:
    """Build the JSON document of §4.7 (keys in normative order).

    ``anchor_dir`` = folder of the named project file (relative paths) or ``None`` (auto-save:
    absolute paths). ``session`` is appended only when given.
    """
    a = project.advanced
    v = project.vias
    doc: dict[str, Any] = {
        "format": PROJECT_FORMAT,
        "schema_version": migrations.CURRENT_SCHEMA_VERSION,
        "app_version": __version__,
        "stackup": {"source_path": _path_out(project.stackup_source_path, anchor_dir),
                    "layers": [_layer_to_dict(r) for r in project.layers]},
        "vias": {"drill_diameter_mm": float(v.drill_diameter_mm),
                 "antipad_diameter_mm": float(v.antipad_diameter_mm),
                 "via_pitch_mm": float(v.via_pitch_mm),
                 "vias_per_decap": int(v.vias_per_decap),
                 "pad_via_count": int(v.pad_via_count)},
        "advanced": {"via_model": a.via_model,
                     "plating_thickness_mm": float(a.plating_thickness_mm),
                     "via_conductivity_s_per_m": float(a.via_conductivity_s_per_m),
                     "mounting_inductance_nh": float(a.mounting_inductance_nh),
                     "s2p_default_mode": a.s2p_default_mode,
                     "model_search_dir": _path_out(a.model_search_dir, anchor_dir)},
        "pwr": {"source_path": _path_out(project.pwr_source_path, anchor_dir),
                "rows": [{"name": r.name, "pwr_layer": int(r.pwr_layer),
                          "gnd_layer": int(r.gnd_layer), "width_mm": float(r.width_mm),
                          "enabled": bool(r.enabled)} for r in project.pwr_rows]},
        "decaps": {"source_path": _path_out(project.decap_source_path, anchor_dir),
                   "rows": [{"pwr_name": r.pwr_name,
                             "model_file": _path_out(r.model_file, anchor_dir),
                             "count": int(r.count), "distance_mm": float(r.distance_mm),
                             "dummy": bool(r.dummy), "subckt": r.subckt,
                             "s2p_mode": r.s2p_mode, "enabled": bool(r.enabled)}
                            for r in project.decap_rows]},
        "sweep": {"f_start_hz": float(project.sweep.f_start_hz),
                  "f_stop_hz": float(project.sweep.f_stop_hz),
                  "n_points": int(project.sweep.n_points),
                  "show_plane_only": bool(project.sweep.show_plane_only)},
        "display": {"z_unit": project.display.z_unit},
    }
    if session is not None:
        doc["session"] = _session_to_dict(session)
    return doc


# =============================================================================================
# Deserialisation with structural type checks (§4.7, §5.8.3)
# =============================================================================================
_MISSING = object()

_TOP_KEYS = {"format", "schema_version", "app_version", "stackup", "vias", "advanced", "pwr",
             "decaps", "sweep", "display", "session"}
_SECTION_KEYS = {
    "stackup": {"source_path", "layers"},
    "vias": {"drill_diameter_mm", "antipad_diameter_mm", "via_pitch_mm", "vias_per_decap",
             "pad_via_count"},
    "advanced": {"via_model", "plating_thickness_mm", "via_conductivity_s_per_m",
                 "mounting_inductance_nh", "s2p_default_mode", "model_search_dir"},
    "pwr": {"source_path", "rows"},
    "decaps": {"source_path", "rows"},
    "sweep": {"f_start_hz", "f_stop_hz", "n_points", "show_plane_only"},
    "display": {"z_unit"},
}
_LAYER_KEYS = {"number", "name", "thickness_mm", "conductivity_s_per_m", "dk", "df"}
_PWR_ROW_KEYS = {"name", "pwr_layer", "gnd_layer", "width_mm", "enabled"}
_DECAP_ROW_KEYS = {"pwr_name", "model_file", "count", "distance_mm", "dummy", "subckt",
                   "s2p_mode", "enabled"}


class _Reader:
    """Typed accessors raising :class:`ProjectFormatError` on structural type errors."""

    def __init__(self, issues: IssueCollector):
        self.issues = issues

    def unknown(self, obj: dict, known: set[str], where: str) -> None:
        for key in obj:
            if key not in known:
                self.issues.warning("W_PROJECT_UNKNOWN_KEY",
                                    f"Unknown key '{where}{key}' ignored.", None, f"{where}{key}")

    @staticmethod
    def section(doc: dict, key: str) -> dict:
        value = doc.get(key, _MISSING)
        if value is _MISSING or value is None:
            return {}
        if not isinstance(value, dict):
            raise ProjectFormatError(f"'{key}' must be an object.")
        return value

    @staticmethod
    def _get(obj: dict, key: str, where: str, default: Any) -> Any:
        value = obj.get(key, _MISSING)
        if value is _MISSING:
            if default is _MISSING:
                raise ProjectFormatError(f"Required key '{where}{key}' is missing.")
            return default
        return value

    def number(self, obj: dict, key: str, where: str, default: Any = _MISSING,
               nullable: bool = False) -> float | None:
        value = self._get(obj, key, where, default)
        if value is None and (nullable or default is None):
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProjectFormatError(f"'{where}{key}' must be a number.")
        return float(value)

    def integer(self, obj: dict, key: str, where: str, default: Any = _MISSING) -> int:
        value = self._get(obj, key, where, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (
                isinstance(value, float) and not value.is_integer()):
            raise ProjectFormatError(f"'{where}{key}' must be an integer.")
        return int(value)

    def string(self, obj: dict, key: str, where: str, default: Any = _MISSING,
               nullable: bool = False) -> str | None:
        value = self._get(obj, key, where, default)
        if value is None and (nullable or default is None):
            return None
        if not isinstance(value, str):
            raise ProjectFormatError(f"'{where}{key}' must be a string.")
        return value

    def boolean(self, obj: dict, key: str, where: str, default: Any = _MISSING) -> bool:
        value = self._get(obj, key, where, default)
        if not isinstance(value, bool):
            raise ProjectFormatError(f"'{where}{key}' must be true or false.")
        return value

    def rows(self, obj: dict, key: str, where: str) -> list[dict]:
        value = obj.get(key, _MISSING)
        if value is _MISSING or value is None:
            return []
        if not isinstance(value, list):
            raise ProjectFormatError(f"'{where}{key}' must be a list.")
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise ProjectFormatError(f"'{where}{key}[{idx}]' must be an object.")
        return value

    def enum(self, obj: dict, key: str, where: str, allowed: tuple[str, ...], default: str | None,
             nullable: bool = False) -> str | None:
        value = self.string(obj, key, where, default, nullable=nullable)
        if value is None:
            return None
        if value not in allowed:
            self.issues.warning("W_PROJECT_VALUE",
                                f"'{where}{key}' = '{value}' is not one of {', '.join(allowed)}; "
                                f"{'default' if default is not None else 'none'} used.", None,
                                f"{where}{key}")
            return default
        return value


def _session_from_dict(raw: Any) -> Session:
    """Lenient parse of ``session`` (unknown keys and wrong types are silently defaulted)."""
    session = Session()
    if not isinstance(raw, dict):
        return session

    def get(obj: dict, key: str, types: tuple[type, ...], default: Any) -> Any:
        value = obj.get(key, default)
        if value is None:
            return default if default is not None else None
        if isinstance(value, bool) and bool not in types:
            return default
        return value if isinstance(value, types) else default

    session.project_path = get(raw, "project_path", (str,), None)
    session.modified = get(raw, "modified", (bool,), False)
    recent = raw.get("recent_files")
    if isinstance(recent, list):
        session.recent_files = [p for p in recent if isinstance(p, str)][:MAX_RECENT_FILES]
    window = raw.get("window")
    if isinstance(window, dict):
        w = WindowState()
        w.geometry_b64 = get(window, "geometry_b64", (str,), "")
        w.state_b64 = get(window, "state_b64", (str,), "")
        sizes = window.get("splitter_sizes")
        if isinstance(sizes, list):
            w.splitter_sizes = [int(s) for s in sizes
                                if isinstance(s, (int, float)) and not isinstance(s, bool)]
        w.input_tab = int(get(window, "input_tab", (int,), 0))
        w.result_tab = get(window, "result_tab", (str,), None)
        w.decap_filter = get(window, "decap_filter", (str,), None)
        w.message_dock_visible = get(window, "message_dock_visible", (bool,), True)
        session.window = w
    plots = raw.get("plots")
    if isinstance(plots, dict):
        for name, view in plots.items():
            if not isinstance(view, dict):
                continue
            pv = PlotView(auto_range=get(view, "auto_range", (bool,), True))
            for attr in ("x_range_log10", "y_range_log10"):
                rng = view.get(attr)
                if (isinstance(rng, list) and len(rng) == 2 and all(
                        isinstance(x, (int, float)) and not isinstance(x, bool) for x in rng)):
                    setattr(pv, attr, (float(rng[0]), float(rng[1])))
            session.plots[str(name)] = pv
    session.had_results = get(raw, "had_results", (bool,), False)
    session.saved_utc = get(raw, "saved_utc", (str,), None)
    return session


def check_document_header(doc: Any) -> int:
    """Top-level checks shared by named files and the auto-save: object, ``format``,
    ``schema_version``. Returns the schema version; raises :class:`ProjectFormatError`."""
    if not isinstance(doc, dict):
        raise ProjectFormatError("The project file does not contain a JSON object.")
    if doc.get("format") != PROJECT_FORMAT:
        raise ProjectFormatError(f"Not a Simple PI Calculator project (format must be "
                                 f"'{PROJECT_FORMAT}').")
    return migrations.schema_version_of(doc)


def project_from_dict(doc: dict, anchor_dir: str | None,
                      issues: IssueCollector) -> tuple[Project, Session | None]:
    """Parse a project document (§4.7) after running migrations (§5.8.4).

    Raises :class:`ProjectFormatError` (structure) or :class:`ProjectTooNewError`. Semantic
    problems (missing files, bad layer references, out-of-range values) are **not** checked here.
    """
    version = check_document_header(doc)
    doc = migrations.migrate(doc, issues)
    rd = _Reader(issues)
    rd.unknown(doc, _TOP_KEYS, "")

    project = Project()
    if version < migrations.CURRENT_SCHEMA_VERSION:
        project.migrated_from = version

    # stackup
    st = rd.section(doc, "stackup")
    rd.unknown(st, _SECTION_KEYS["stackup"], "stackup.")
    project.stackup_source_path = _path_in(rd.string(st, "source_path", "stackup.", None),
                                           anchor_dir)
    for idx, row in enumerate(rd.rows(st, "layers", "stackup.")):
        where = f"stackup.layers[{idx}]."
        rd.unknown(row, _LAYER_KEYS, where)
        project.layers.append(LayerRow(
            number=rd.integer(row, "number", where),
            name=rd.string(row, "name", where, "", nullable=True) or "",
            thickness_mm=rd.number(row, "thickness_mm", where),
            conductivity_s_per_m=rd.number(row, "conductivity_s_per_m", where, None),
            dk=rd.number(row, "dk", where, None),
            df=rd.number(row, "df", where, None)))

    # vias
    vs = rd.section(doc, "vias")
    rd.unknown(vs, _SECTION_KEYS["vias"], "vias.")
    project.vias = ViaInputs(
        drill_diameter_mm=rd.number(vs, "drill_diameter_mm", "vias.", DEFAULT_DRILL_DIAMETER_MM),
        antipad_diameter_mm=rd.number(vs, "antipad_diameter_mm", "vias.",
                                      DEFAULT_ANTIPAD_DIAMETER_MM),
        via_pitch_mm=rd.number(vs, "via_pitch_mm", "vias.", DEFAULT_VIA_PITCH_MM),
        vias_per_decap=rd.integer(vs, "vias_per_decap", "vias.", DEFAULT_VIAS_PER_DECAP),
        pad_via_count=rd.integer(vs, "pad_via_count", "vias.", DEFAULT_PAD_VIA_COUNT))

    # advanced
    ad = rd.section(doc, "advanced")
    rd.unknown(ad, _SECTION_KEYS["advanced"], "advanced.")
    search_dir = _path_in(rd.string(ad, "model_search_dir", "advanced.", None), anchor_dir)
    project.advanced = AdvancedSettings(
        via_model=rd.enum(ad, "via_model", "advanced.", VIA_MODELS, DEFAULT_VIA_MODEL) or
        DEFAULT_VIA_MODEL,
        plating_thickness_mm=rd.number(ad, "plating_thickness_mm", "advanced.",
                                       DEFAULT_PLATING_THICKNESS_MM),
        via_conductivity_s_per_m=rd.number(ad, "via_conductivity_s_per_m", "advanced.",
                                           DEFAULT_VIA_CONDUCTIVITY_S_PER_M),
        mounting_inductance_nh=rd.number(ad, "mounting_inductance_nh", "advanced.",
                                         DEFAULT_MOUNTING_INDUCTANCE_NH),
        s2p_default_mode=rd.enum(ad, "s2p_default_mode", "advanced.", S2P_MODES,
                                 DEFAULT_S2P_MODE) or DEFAULT_S2P_MODE,
        model_search_dir=search_dir)

    # pwr
    pw = rd.section(doc, "pwr")
    rd.unknown(pw, _SECTION_KEYS["pwr"], "pwr.")
    project.pwr_source_path = _path_in(rd.string(pw, "source_path", "pwr.", None), anchor_dir)
    for idx, row in enumerate(rd.rows(pw, "rows", "pwr.")):
        where = f"pwr.rows[{idx}]."
        rd.unknown(row, _PWR_ROW_KEYS, where)
        project.pwr_rows.append(PwrRow(
            name=rd.string(row, "name", where),
            pwr_layer=rd.integer(row, "pwr_layer", where),
            gnd_layer=rd.integer(row, "gnd_layer", where),
            width_mm=rd.number(row, "width_mm", where),
            enabled=rd.boolean(row, "enabled", where, True)))

    # decaps
    dc = rd.section(doc, "decaps")
    rd.unknown(dc, _SECTION_KEYS["decaps"], "decaps.")
    project.decap_source_path = _path_in(rd.string(dc, "source_path", "decaps.", None),
                                         anchor_dir)
    excel_dir = os.path.dirname(project.decap_source_path) if (
        project.decap_source_path and os.path.isabs(project.decap_source_path)) else None
    for idx, row in enumerate(rd.rows(dc, "rows", "decaps.")):
        where = f"decaps.rows[{idx}]."
        rd.unknown(row, _DECAP_ROW_KEYS, where)
        model_file = rd.string(row, "model_file", where)
        project.decap_rows.append(DecapRow(
            pwr_name=rd.string(row, "pwr_name", where),
            model_file=_model_path_in(model_file, anchor_dir, excel_dir, search_dir),
            count=rd.integer(row, "count", where),
            distance_mm=rd.number(row, "distance_mm", where),
            dummy=rd.boolean(row, "dummy", where, False),
            subckt=rd.string(row, "subckt", where, None),
            s2p_mode=rd.enum(row, "s2p_mode", where, S2P_MODES, None, nullable=True),
            enabled=rd.boolean(row, "enabled", where, True)))

    # sweep / display
    sw = rd.section(doc, "sweep")
    rd.unknown(sw, _SECTION_KEYS["sweep"], "sweep.")
    project.sweep = SweepSettings(
        f_start_hz=rd.number(sw, "f_start_hz", "sweep.", DEFAULT_F_START_HZ),
        f_stop_hz=rd.number(sw, "f_stop_hz", "sweep.", DEFAULT_F_STOP_HZ),
        n_points=rd.integer(sw, "n_points", "sweep.", DEFAULT_N_POINTS),
        show_plane_only=rd.boolean(sw, "show_plane_only", "sweep.", False))
    ds = rd.section(doc, "display")
    rd.unknown(ds, _SECTION_KEYS["display"], "display.")
    project.display = DisplaySettings(
        z_unit=rd.enum(ds, "z_unit", "display.", Z_UNITS, DEFAULT_Z_UNIT) or DEFAULT_Z_UNIT)

    session = _session_from_dict(doc["session"]) if "session" in doc else None
    return project, session


# =============================================================================================
# Named project files (§5.8.4, §5.8.5)
# =============================================================================================
def _read_json(path: str) -> Any:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        # utf-8-sig: tolerate a BOM added by Windows editors (Notepad) when a user edits the file
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectFormatError(f"The file is not valid UTF-8 JSON: {exc}", path) from exc


def load_project(path: str | os.PathLike[str]) -> tuple[Project, list[Issue]]:
    """Load a named project (§5.8.4). A ``session`` block is ignored with an info.

    Raises :class:`ProjectFormatError` (``E_PROJECT_FORMAT``), :class:`ProjectTooNewError`
    (``E_PROJECT_NEWER``) or ``OSError``.
    """
    path_str = os.path.abspath(os.fspath(path))
    doc = _read_json(path_str)
    issues = IssueCollector()
    try:
        project, session = project_from_dict(doc, os.path.dirname(path_str), issues)
    except ProjectTooNewError as exc:
        raise ProjectTooNewError(exc.schema_version, source=path_str) from exc
    except ProjectFormatError as exc:
        raise ProjectFormatError(str(exc), path_str) from exc
    if session is not None:
        issues.info("I_PROJECT_SESSION_IGNORED",
                    "The project file contains a 'session' block (auto-save only); ignored.",
                    path_str)
    project.loaded_from = path_str
    if project.migrated_from is not None:
        for i, issue in enumerate(issues.issues):
            if issue.code == "I_PROJECT_MIGRATED" and issue.source is None:
                issues.issues[i] = Issue(issue.code, issue.severity, issue.message, path_str)
    return project, issues.issues


def migration_backup_path(original_path: str, schema_version: int) -> str:
    """``<name>.schema<n>.bak.spical.json`` next to the original file (§5.8.4)."""
    folder = os.path.dirname(os.path.abspath(original_path))
    return os.path.join(folder, f"{project_stem(original_path)}.schema{schema_version}"
                                f".bak{PROJECT_SUFFIX}")


def save_project(project: Project, path: str | os.PathLike[str]) -> None:
    """Save a named project: relative paths, no ``session``, atomic write (§5.8.5).

    On the first save of a migrated file the original is copied to
    ``<name>.schema<n>.bak.spical.json`` next to it (§5.8.4).
    """
    path_str = os.path.abspath(os.fspath(path))
    if project.migrated_from is not None and project.loaded_from and os.path.isfile(
            project.loaded_from):
        backup = migration_backup_path(project.loaded_from, project.migrated_from)
        if not os.path.exists(backup):
            shutil.copy2(project.loaded_from, backup)
    doc = project_to_dict(project, os.path.dirname(path_str), None)
    write_json_atomic(path_str, doc)
    project.migrated_from = None
    project.loaded_from = path_str


def dumps_document(doc: dict) -> str:
    """UTF-8 JSON text, 2-space indent (§4.7)."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write_json_atomic(path: str, doc: dict, backup_path: str | None = None) -> bool:
    """Atomic JSON write (§5.8.2): ``<path>.tmp`` + fsync, rotate the old file to
    ``backup_path`` (if given and the file exists), then ``os.replace``. Returns ``True``.

    Raises ``OSError`` on I/O failure (the temporary file is removed).
    """
    data = dumps_document(doc).encode("utf-8")
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if backup_path is not None and os.path.exists(path):
            os.replace(path, backup_path)
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:  # pragma: no cover
            pass
        raise
    return True


# =============================================================================================
# Conversion to engine inputs (§5.2, §5.3)
# =============================================================================================
def to_inputs(project: Project, project_path: str | None) -> Any:
    """SI-converted :class:`simple_pi_calculator.core.engine.ProjectInputs` (§5.2).

    Only enabled PWR rows become ``PwrSpec``; all decap rows are passed (each carries ``enabled``).
    Engine modules are imported lazily.
    """
    from simple_pi_calculator.core.engine import ProjectInputs
    from simple_pi_calculator.core.pdn import PwrSpec
    from simple_pi_calculator.core.via import ViaSettings

    v = project.vias
    a = project.advanced
    vias = ViaSettings(
        drill_diameter_m=v.drill_diameter_mm * MM,
        antipad_diameter_m=v.antipad_diameter_mm * MM,
        via_pitch_m=v.via_pitch_mm * MM,
        vias_per_decap=int(v.vias_per_decap),
        pad_via_count=int(v.pad_via_count),
        model=a.via_model,  # type: ignore[arg-type]
        plating_thickness_m=a.plating_thickness_mm * MM,
        conductivity=a.via_conductivity_s_per_m,
        mounting_inductance_h=a.mounting_inductance_nh * NH,
    )
    pwrs = [PwrSpec(name=r.name, pwr_layer=r.pwr_layer, gnd_layer=r.gnd_layer,
                    width_m=r.width_mm * MM) for r in project.pwr_rows if r.enabled]
    return ProjectInputs(
        stackup=project.stackup(),
        vias=vias,
        pwrs=pwrs,
        decap_rows=[copy.copy(r) for r in project.decap_rows],
        f_start_hz=float(project.sweep.f_start_hz),
        f_stop_hz=float(project.sweep.f_stop_hz),
        n_points=int(project.sweep.n_points),
        show_plane_only=bool(project.sweep.show_plane_only),
        project_dir=os.path.dirname(os.path.abspath(project_path)) if project_path else None,
        model_search_dir=a.model_search_dir,
        s2p_default_mode=a.s2p_default_mode,
        decap_source_dir=(os.path.dirname(project.decap_source_path)
                          if project.decap_source_path and os.path.isabs(project.decap_source_path)
                          else None),
    )


# =============================================================================================
# Auto-save (§5.8)
# =============================================================================================
def resolve_appdata_dir(platform_default: str | None = None) -> str:
    """Auto-save directory (§5.8.1): ``$SPICAL_APPDATA_DIR`` if set, else ``platform_default``
    (the GUI passes ``QStandardPaths.AppDataLocation``), else a Qt-free platform fallback."""
    override = os.environ.get(APPDATA_ENV_VAR)
    if override:
        return override
    if platform_default:
        return platform_default
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


@dataclass
class AutosaveLoadResult:
    """Result of :meth:`AutosaveStore.load` (§5.3)."""

    project: Project
    session: Session
    source: Literal["primary", "backup", "defaults"]
    issues: list[Issue]


_QUARANTINE_RE = re.compile(r"^autosave\.corrupt-(\d{8}-\d{6})(?:-(\d+))?\.spical\.json$")


def _canonical(doc: dict) -> str:
    stripped = copy.deepcopy(doc)
    session = stripped.get("session")
    if isinstance(session, dict):
        session.pop("saved_utc", None)
    return json.dumps(stripped, sort_keys=True, ensure_ascii=False)


class AutosaveStore:
    """Qt-free auto-save file management (§5.8.2, §5.8.3)."""

    FILE = "autosave.spical.json"
    BACKUP = "autosave.bak.spical.json"

    def __init__(self, directory: str):
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)
        self._last_canonical: str | None = None

    @property
    def path(self) -> str:
        return os.path.join(self.directory, self.FILE)

    @property
    def backup_path(self) -> str:
        return os.path.join(self.directory, self.BACKUP)

    # -- writing --------------------------------------------------------------------------------
    def save(self, project: Project, session: Session) -> bool:
        """Write the auto-save (§5.8.2). Returns ``False`` if skipped because the content
        (excluding ``session.saved_utc``) equals the last written content.

        Sets ``session.saved_utc`` when a write happens. ``OSError`` propagates to the caller.
        """
        doc = project_to_dict(project, None, session)
        canonical = _canonical(doc)
        if self._last_canonical is None and os.path.isfile(self.path):
            try:
                self._last_canonical = _canonical(_read_json(self.path))
            except (OSError, ProjectFormatError, TypeError):
                self._last_canonical = None
        if canonical == self._last_canonical:
            return False
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc["session"]["saved_utc"] = stamp
        write_json_atomic(self.path, doc, self.backup_path)
        session.saved_utc = stamp
        self._last_canonical = canonical
        return True

    # -- quarantine -----------------------------------------------------------------------------
    def quarantine(self, path: str, tag: str) -> str:
        """Rename ``path`` to ``autosave.<tag>-YYYYMMDD-HHMMSS[-N].spical.json``; returns the new
        path. For ``tag == "corrupt"`` only the newest ``QUARANTINE_KEEP`` files are kept."""
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        target = os.path.join(self.directory, f"autosave.{tag}-{stamp}{PROJECT_SUFFIX}")
        counter = 1
        while os.path.exists(target):
            target = os.path.join(self.directory,
                                  f"autosave.{tag}-{stamp}-{counter}{PROJECT_SUFFIX}")
            counter += 1
        os.replace(path, target)
        if tag == "corrupt":
            self._prune_quarantine()
        return target

    def quarantined_files(self) -> list[str]:
        """Corrupt quarantine files, oldest first."""
        entries = []
        for name in os.listdir(self.directory):
            m = _QUARANTINE_RE.match(name)
            if m:
                entries.append(((m.group(1), int(m.group(2) or 0)), name))
        entries.sort()
        return [os.path.join(self.directory, name) for _, name in entries]

    def _prune_quarantine(self) -> None:
        files = self.quarantined_files()
        for old in files[: max(len(files) - QUARANTINE_KEEP, 0)]:
            try:
                os.remove(old)
            except OSError:  # pragma: no cover
                pass

    # -- loading --------------------------------------------------------------------------------
    def _try_load(self, path: str) -> tuple[str, Any]:
        """→ ("missing" | "corrupt" | "newer" | "ok", payload)."""
        if not os.path.exists(path):
            return "missing", None
        try:
            doc = _read_json(path)
            version = check_document_header(doc)
        except (OSError, ProjectFormatError):
            return "corrupt", None
        if version > migrations.CURRENT_SCHEMA_VERSION:
            return "newer", version
        collector = IssueCollector()
        try:
            project, session = project_from_dict(doc, None, collector)
        except ProjectTooNewError as exc:
            return "newer", exc.schema_version
        except (ProjectFormatError, TypeError, ValueError, AttributeError):
            return "corrupt", None
        return "ok", (project, session or Session(), collector.issues)

    @staticmethod
    def _mtime_text(path: str) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
        except OSError:  # pragma: no cover
            return "unknown time"

    def load(self) -> AutosaveLoadResult:
        """Restore the auto-save with the recovery rules of §5.8.3 (never raises for bad files)."""
        issues = IssueCollector()
        quarantined: list[str] = []

        state, payload = self._try_load(self.path)
        if state == "ok":
            project, session, extra = payload
            issues.extend(extra)
            return AutosaveLoadResult(project, session, "primary", issues.issues)
        if state == "corrupt":
            quarantined.append(self.quarantine(self.path, "corrupt"))
        elif state == "newer":
            kept = self.quarantine(self.path, f"v{payload}-newer")
            issues.warning("W_AUTOSAVE_NEWER",
                           f"The auto-save was written by a newer version (schema {payload}); "
                           f"kept as {kept}.", kept)

        b_state, b_payload = self._try_load(self.backup_path)
        if b_state == "ok":
            project, session, extra = b_payload
            when = self._mtime_text(self.backup_path)
            if state == "corrupt":
                issues.warning("W_AUTOSAVE_RECOVERED_BACKUP",
                               "The last auto-save was damaged; restored the previous auto-save "
                               f"from {when}.", self.backup_path)
            elif state == "missing":
                issues.warning("W_AUTOSAVE_RECOVERED_BACKUP",
                               "The last auto-save was missing; restored the previous auto-save "
                               f"from {when}.", self.backup_path)
            issues.extend(extra)
            return AutosaveLoadResult(project, session, "backup", issues.issues)
        if b_state == "corrupt":
            quarantined.append(self.quarantine(self.backup_path, "corrupt"))
        elif b_state == "newer":
            kept = self.quarantine(self.backup_path, f"v{b_payload}-newer")
            issues.warning("W_AUTOSAVE_NEWER",
                           f"The auto-save backup was written by a newer version (schema "
                           f"{b_payload}); kept as {kept}.", kept)

        if quarantined:
            existing = [p for p in quarantined if os.path.exists(p)]
            issues.warning("W_AUTOSAVE_CORRUPT",
                           "The auto-save was damaged and could not be restored; starting with "
                           f"defaults. Damaged file(s) kept as: {', '.join(existing)}.",
                           self.directory)
        return AutosaveLoadResult(Project(), Session(), "defaults", issues.issues)


__all__ = [
    "Project",
    "ViaInputs",
    "AdvancedSettings",
    "SweepSettings",
    "DisplaySettings",
    "WindowState",
    "PlotView",
    "Session",
    "ProjectFormatError",
    "ProjectTooNewError",
    "project_to_dict",
    "project_from_dict",
    "check_document_header",
    "load_project",
    "save_project",
    "write_json_atomic",
    "dumps_document",
    "ensure_project_suffix",
    "project_stem",
    "migration_backup_path",
    "to_inputs",
    "resolve_appdata_dir",
    "AutosaveLoadResult",
    "AutosaveStore",
]
