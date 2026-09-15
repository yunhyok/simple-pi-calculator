"""Table models for the input tabs (DESIGN.md §5.5).

The models edit the lists of the current :class:`~simple_pi_calculator.io.project_io.Project` in
place. Every user-originated change emits ``edited`` (used for the modified marker and auto-save);
refreshes of derived read-only columns only emit ``dataChanged``.
"""

from __future__ import annotations

import math
import os
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor

from simple_pi_calculator.constants import S2P_MODES
from simple_pi_calculator.core.stackup import is_metal_conductivity
from simple_pi_calculator.core.types import (
    DecapRow,
    LayerRow,
    PwrRow,
    decap_file_kind,
    ports_for_count,
    resolve_model_path,
)
from simple_pi_calculator.core.units import MM, PF, format_sig
from simple_pi_calculator.errors import IssueCollector, Severity

ERROR_BRUSH = QBrush(QColor("#f8d0d0"))
DERIVED_BRUSH = QBrush(QColor("#f2f2f2"))
METAL_BRUSH = QBrush(QColor("#f3dcc0"))
DIELECTRIC_BRUSH = QBrush(QColor("#e1f2dc"))

Index = QModelIndex | QPersistentModelIndex


def _fmt(value: float | None, decimals: int = 3) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    return f"{value:.{decimals}f}"


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if text in ("", "-", "n/a", "none"):
        return None
    return float(text)


class _BaseTableModel(QAbstractTableModel):
    """Common header handling and the ``edited`` signal."""

    edited = Signal()
    HEADERS: tuple[str, ...] = ()
    HEADER_TIPS: tuple[str, ...] = ()   #: full column names shown as header tooltips
    EDITABLE: frozenset[int] = frozenset()
    CHECK_COLUMNS: frozenset[int] = frozenset()
    DERIVED: frozenset[int] = frozenset()

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
            if orientation == Qt.Orientation.Vertical:
                return str(section + 1)
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            tip = self.HEADER_TIPS[section] if 0 <= section < len(self.HEADER_TIPS) else ""
            if section in self.DERIVED:
                tip = f"{tip} — derived (read-only)" if tip else "Derived (read-only)"
            return tip or None
        return None

    def flags(self, index: Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() in self.CHECK_COLUMNS:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        elif index.column() in self.EDITABLE:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def _emit_row(self, row: int) -> None:
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))


# =============================================================================================
# Stack-up
# =============================================================================================
class StackupTableModel(_BaseTableModel):
    """Layer table: Layer #, Name, Type (derived), thickness, σ, Dk, Df, z_top (derived)."""

    HEADERS = ("Layer #", "Name", "Type", "Thickness (mm)", "σ (S/m)", "Dk", "Df",
               "z_top (mm)")
    HEADER_TIPS = ("Layer number (1 = top)", "Layer name",
                   "Metal (σ > 0) or Dielectric (σ empty or 0)", "Thickness (mm)",
                   "Conductivity σ (S/m); empty for dielectric layers",
                   "Relative permittivity Dk", "Loss tangent Df",
                   "Depth of the layer top below the top surface (mm)")
    COL_NUMBER, COL_NAME, COL_TYPE, COL_THICK, COL_SIGMA, COL_DK, COL_DF, COL_ZTOP = range(8)
    EDITABLE = frozenset({0, 1, 3, 4, 5, 6})
    DERIVED = frozenset({2, 7})

    def __init__(self, rows: list[LayerRow] | None = None, parent=None):
        super().__init__(parent)
        self._rows: list[LayerRow] = rows if rows is not None else []
        self.highlight_layers: set[int] = set()

    @property
    def rows(self) -> list[LayerRow]:
        return self._rows

    def set_rows(self, rows: list[LayerRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def _z_top_mm(self, row: int) -> float | None:
        target = self._rows[row]
        ordered = sorted(self._rows, key=lambda r: r.number)
        z = 0.0
        for r in ordered:
            if r is target:
                return z
            z += max(r.thickness_mm, 0.0)
        return None

    def _cell_error(self, row: int, col: int) -> str | None:
        r = self._rows[row]
        metal = is_metal_conductivity(r.conductivity_s_per_m)
        if col == self.COL_NUMBER:
            if r.number < 1:
                return "Layer number must be ≥ 1 (E_STACK_LAYER_NUM)."
            if sum(1 for x in self._rows if x.number == r.number) > 1:
                return "Duplicate layer number (E_STACK_LAYER_DUP)."
        if col == self.COL_THICK and not (r.thickness_mm > 0):
            return "Thickness must be > 0 (E_STACK_THICKNESS)."
        if col == self.COL_DK and not metal and (r.dk is None or r.dk < 1):
            return "Dielectric Dk missing or < 1 (E_STACK_DK)."
        if col == self.COL_DF and r.df is not None and not (0 <= r.df <= 1):
            return "Df must be within 0…1 (E_STACK_DF)."
        return None

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        r = self._rows[index.row()]
        col = index.column()
        metal = is_metal_conductivity(r.conductivity_s_per_m)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            edit = role == Qt.ItemDataRole.EditRole
            if col == self.COL_NUMBER:
                return r.number
            if col == self.COL_NAME:
                return r.name
            if col == self.COL_TYPE:
                return "Metal" if metal else "Dielectric"
            if col == self.COL_THICK:
                return r.thickness_mm if edit else f"{r.thickness_mm:g}"
            if col == self.COL_SIGMA:
                if r.conductivity_s_per_m is None:
                    return "" if edit else ""
                return (f"{r.conductivity_s_per_m:g}" if edit
                        else f"{r.conductivity_s_per_m:.4g}")
            if col == self.COL_DK:
                return "" if r.dk is None else f"{r.dk:g}"
            if col == self.COL_DF:
                return "" if r.df is None else f"{r.df:g}"
            if col == self.COL_ZTOP:
                z = self._z_top_mm(index.row())
                return "" if z is None else f"{z:.4f}"
        if role == Qt.ItemDataRole.BackgroundRole:
            if self._cell_error(index.row(), col):
                return ERROR_BRUSH
            if col == self.COL_TYPE:
                return METAL_BRUSH if metal else DIELECTRIC_BRUSH
            if r.number in self.highlight_layers:
                return QBrush(QColor("#fff3b0"))
            if col in self.DERIVED:
                return DERIVED_BRUSH
        if role == Qt.ItemDataRole.ToolTipRole:
            err = self._cell_error(index.row(), col)
            if err:
                return err
            if col == self.COL_TYPE:
                return ("Metal: conductivity > 0" if metal
                        else "Dielectric: conductivity empty or 0")
        if role == Qt.ItemDataRole.TextAlignmentRole and col not in (self.COL_NAME,
                                                                      self.COL_TYPE):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        r = self._rows[index.row()]
        col = index.column()
        try:
            if col == self.COL_NUMBER:
                r.number = int(float(value))
            elif col == self.COL_NAME:
                r.name = str(value).strip()
            elif col == self.COL_THICK:
                r.thickness_mm = float(str(value).replace(",", "."))
            elif col == self.COL_SIGMA:
                r.conductivity_s_per_m = _parse_optional_float(value)
            elif col == self.COL_DK:
                r.dk = _parse_optional_float(value)
            elif col == self.COL_DF:
                r.df = _parse_optional_float(value)
            else:
                return False
        except (TypeError, ValueError):
            return False
        self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1,
                                                           self.columnCount() - 1))
        self.edited.emit()
        return True

    def insert_row(self, position: int | None = None) -> int:
        pos = len(self._rows) if position is None else max(0, min(position, len(self._rows)))
        number = max((r.number for r in self._rows), default=0) + 1
        self.beginInsertRows(QModelIndex(), pos, pos)
        self._rows.insert(pos, LayerRow(number=number, name=f"L{number}", thickness_mm=0.1,
                                        conductivity_s_per_m=None, dk=4.2, df=0.02))
        self.endInsertRows()
        self.edited.emit()
        return pos

    def remove_rows(self, rows: list[int]) -> None:
        valid = sorted({r for r in rows if 0 <= r < len(self._rows)}, reverse=True)
        if not valid:  # nothing selected: not an edit (no modified marker, no auto-save)
            return
        for row in valid:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._rows[row]
            self.endRemoveRows()
        self.edited.emit()


# =============================================================================================
# PWR list
# =============================================================================================
class PwrTableModel(_BaseTableModel):
    """PWR nets with derived geometry columns (§5.5 tab 3)."""

    HEADERS = ("On", "PWR Name", "PWR Layer", "GND Layer", "Width (mm)", "# PADs",
               "D_ref (mm)", "H (mm)", "Ports", "d (mm)", "εr_eff", "C_plane (pF)")
    HEADER_TIPS = ("Enabled", "PWR Name", "Layer Number (PWR plane layer)", "GND Layer Number",
                   "PWR Plane Width (mm)",
                   "Number of PADs: observation/contact pads of this net on the Top side (integer "
                   "≥ 1, default 1).\nThe pads form a row across the width at y = 0.2·D_ref; each "
                   "pad has its own via set of 'PAD vias' via pairs,\nand all pads are joined at "
                   "an ideal common node (|Z| of the pads in parallel).",
                   "Reference distance D_ref = max decap distance (mm)",
                   "Derived plane height H = 1.4 · D_ref (mm)", "Number of decap ports",
                   "PWR–GND plane separation d (mm)", "Effective relative permittivity",
                   "Plane capacitance of the synthetic W × H plane (pF)")
    (COL_ENABLED, COL_NAME, COL_LAYER, COL_GND, COL_WIDTH, COL_NPADS, COL_DREF, COL_HEIGHT,
     COL_PORTS, COL_D, COL_ER, COL_CPLANE) = range(12)
    EDITABLE = frozenset({1, 2, 3, 4, 5})
    CHECK_COLUMNS = frozenset({0})
    DERIVED = frozenset({6, 7, 8, 9, 10, 11})

    pwrRenamed = Signal(str, str)

    def __init__(self, project: Any, bridge: Any, parent=None):
        super().__init__(parent)
        self._project = project
        self._bridge = bridge
        self._derived: list[dict[str, Any]] = []
        self.refresh_derived(emit=False)

    @property
    def rows(self) -> list[PwrRow]:
        return self._project.pwr_rows

    def set_project(self, project: Any) -> None:
        self.beginResetModel()
        self._project = project
        self.refresh_derived(emit=False)
        self.endResetModel()

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def names(self) -> list[str]:
        return [r.name for r in self.rows]

    # -- derived ---------------------------------------------------------------------------------
    def _derive(self, row: PwrRow) -> dict[str, Any]:
        out: dict[str, Any] = {"errors": {}}
        errors: dict[int, str] = out["errors"]
        if not row.name.strip():
            errors[self.COL_NAME] = "PWR name must not be empty."
        elif sum(1 for r in self.rows if r.name == row.name) > 1:
            errors[self.COL_NAME] = "Duplicate PWR name (E_PWR_NAME_DUP)."
        if not (row.width_mm > 0):
            errors[self.COL_WIDTH] = "Width must be > 0 (E_PWR_DIM)."
        n_pads = getattr(row, "n_pads", 1)
        if isinstance(n_pads, bool) or not isinstance(n_pads, int) or n_pads < 1:
            errors[self.COL_NPADS] = "Number of PADs must be an integer ≥ 1 (E_PWR_NPADS)."
        pair, issues = self._bridge.plane_pair(self._project, row)
        for issue in issues:
            if issue.severity is not Severity.ERROR:
                continue
            col = self.COL_LAYER
            if "GND" in issue.message or "gnd" in issue.message.lower():
                col = self.COL_GND
            errors.setdefault(col, issue.message + f" ({issue.code})")
            if issue.code == "E_PWR_SAME_LAYER":
                errors.setdefault(self.COL_GND, issue.message + f" ({issue.code})")
        if pair is not None:
            out["d_mm"] = pair.d_m / MM
            out["er_eff"] = pair.er_eff
        placement = self._bridge.placement(self._project, row) if row.width_mm > 0 else None
        if placement is not None:
            out["d_ref_mm"] = placement.d_ref_m / MM
            out["height_mm"] = placement.height_m / MM
            out["ports"] = placement.n_decap_ports
            if pair is not None:
                try:
                    out["c_plane_pf"] = pair.plane_capacitance(placement.width_m,
                                                               placement.height_m) / PF
                except Exception:  # noqa: BLE001
                    pass
        return out

    def refresh_derived(self, emit: bool = True) -> None:
        self._derived = [self._derive(r) for r in self.rows]
        if emit and self.rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.rows) - 1, self.columnCount() - 1))

    def derived(self, row: int) -> dict[str, Any]:
        if 0 <= row < len(self._derived):
            return self._derived[row]
        return {"errors": {}}

    # -- Qt API ---------------------------------------------------------------------------------
    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        r = self.rows[index.row()]
        d = self.derived(index.row())
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == self.COL_ENABLED:
            return Qt.CheckState.Checked if r.enabled else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            edit = role == Qt.ItemDataRole.EditRole
            if col == self.COL_NAME:
                return r.name
            if col == self.COL_LAYER:
                return r.pwr_layer
            if col == self.COL_GND:
                return r.gnd_layer
            if col == self.COL_WIDTH:
                return r.width_mm if edit else f"{r.width_mm:g}"
            if col == self.COL_NPADS:
                return r.n_pads if edit else str(r.n_pads)
            key = {self.COL_DREF: "d_ref_mm", self.COL_HEIGHT: "height_mm",
                   self.COL_PORTS: "ports", self.COL_D: "d_mm", self.COL_ER: "er_eff",
                   self.COL_CPLANE: "c_plane_pf"}.get(col)
            if key is not None:
                value = d.get(key)
                if edit:
                    return value
                if value is None:
                    return "—"
                if key == "ports":
                    return str(value)
                if key == "er_eff":
                    return f"{value:.3f}"
                if key == "c_plane_pf":
                    return format_sig(value, 4)
                return f"{value:.3f}" if key == "d_mm" else f"{value:.2f}"
        if role == Qt.ItemDataRole.BackgroundRole:
            if col in d["errors"]:
                return ERROR_BRUSH
            if col in self.DERIVED:
                return DERIVED_BRUSH
        if role == Qt.ItemDataRole.ToolTipRole:
            if col in d["errors"]:
                return d["errors"][col]
            if col == self.COL_HEIGHT:
                return "Derived plane height H = 1.4 · D_ref (§2.5.1)"
            if col == self.COL_NPADS:
                return self.HEADER_TIPS[self.COL_NPADS]
        if role == Qt.ItemDataRole.TextAlignmentRole and col >= self.COL_LAYER:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == self.COL_ENABLED:
            state = value.value if hasattr(value, "value") else value
            r.enabled = int(state) == Qt.CheckState.Checked.value
        elif role == Qt.ItemDataRole.EditRole:
            try:
                if col == self.COL_NAME:
                    new = str(value).strip()
                    old = r.name
                    if new == old:
                        return False
                    r.name = new
                    if old:
                        self.pwrRenamed.emit(old, new)
                elif col == self.COL_LAYER:
                    r.pwr_layer = int(float(value))
                elif col == self.COL_GND:
                    r.gnd_layer = int(float(value))
                elif col == self.COL_WIDTH:
                    r.width_mm = float(str(value).replace(",", "."))
                elif col == self.COL_NPADS:
                    number = float(str(value).replace(",", "."))
                    if not number.is_integer():
                        return False
                    r.n_pads = int(number)
                else:
                    return False
            except (TypeError, ValueError):
                return False
        else:
            return False
        self.refresh_derived()
        self.edited.emit()
        return True

    def insert_row(self, template: PwrRow | None = None) -> int:
        pos = len(self.rows)
        existing = set(self.names())
        if template is None:
            n = pos + 1
            while f"PWR{n}" in existing:
                n += 1
            metals = [layer.number for layer in self._project.layers
                      if is_metal_conductivity(layer.conductivity_s_per_m)]
            pwr_layer = metals[1] if len(metals) > 1 else 1
            gnd_layer = metals[0] if metals else 2
            new = PwrRow(name=f"PWR{n}", pwr_layer=pwr_layer, gnd_layer=gnd_layer,
                         width_mm=30.0)
        else:
            name = f"{template.name}_copy"
            k = 2
            while name in existing:
                name = f"{template.name}_copy{k}"
                k += 1
            new = PwrRow(name=name, pwr_layer=template.pwr_layer, gnd_layer=template.gnd_layer,
                         width_mm=template.width_mm, enabled=template.enabled,
                         n_pads=template.n_pads)
        self.beginInsertRows(QModelIndex(), pos, pos)
        self.rows.append(new)
        self.endInsertRows()
        self.refresh_derived()
        self.edited.emit()
        return pos

    def remove_rows(self, rows: list[int]) -> None:
        valid = sorted({r for r in rows if 0 <= r < len(self.rows)}, reverse=True)
        if not valid:
            return
        for row in valid:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.rows[row]
            self.endRemoveRows()
        self.refresh_derived()
        self.edited.emit()


# =============================================================================================
# Decap list
# =============================================================================================
class DecapModelInfo:
    """Cached per-file derived values (C at 100 kHz, SRF) for the decap table."""

    F_C_HZ = 1.0e5

    def __init__(self) -> None:
        from simple_pi_calculator.core.decap_model import DecapModelCache
        self._cache = DecapModelCache()
        self._values: dict[tuple, dict[str, float | None]] = {}

    def values(self, path: str | None, subckt: str | None, mode: str | None,
               default_mode: str) -> dict[str, float | None]:
        if not path or not os.path.isfile(path):
            return {}
        try:
            st = os.stat(path)
        except OSError:
            return {}
        key = (os.path.normcase(path), st.st_mtime_ns, subckt or "", mode or default_mode)
        if key in self._values:
            return self._values[key]
        out: dict[str, float | None] = {"c_f": None, "srf_hz": None}
        try:
            model = self._cache.get(path, subckt, mode or default_mode, IssueCollector())
            f = np.concatenate([[self.F_C_HZ], np.logspace(3, 10, 701)])
            z = np.asarray(model.impedance(f), dtype=complex)
            if z[0].imag < 0:
                out["c_f"] = -1.0 / (2 * math.pi * self.F_C_HZ * z[0].imag)
            mag = np.abs(z[1:])
            i = int(np.argmin(mag))
            if 0 < i < len(mag) - 1:
                out["srf_hz"] = float(f[1:][i])
        except Exception:  # noqa: BLE001 - derived column only; errors surface at compute
            out = {"c_f": None, "srf_hz": None, "error": 1.0}
        self._values[key] = out
        return out


class DecapTableModel(_BaseTableModel):
    """Decap assignment rows (§5.5 tab 4)."""

    HEADERS = ("Enabled", "PWR Name", "Decap File Name", "# Decaps", "Dist. to PAD (mm)",
               "Dummy Cap", "Subckt", "S2P Mode", "Via sets", "C @100 kHz", "SRF (MHz)")
    HEADER_TIPS = ("Enabled", "PWR Name", "Decap File Name (.mod / .s2p model)",
                   "Number of Decaps", "Distance to PAD (mm)",
                   "Dummy Cap: half of the capacitors share the via set of a neighbour",
                   "SPICE subcircuit name (empty = first/only subcircuit)",
                   "S2P Mode: series or shunt (empty = project default)",
                   "Via sets (cavity ports) of the row", "Capacitance at 100 kHz",
                   "Self-resonance frequency (MHz)")
    (COL_ENABLED, COL_PWR, COL_FILE, COL_COUNT, COL_DIST, COL_DUMMY, COL_SUBCKT, COL_MODE,
     COL_PORTS, COL_C, COL_SRF) = range(11)
    EDITABLE = frozenset({COL_PWR, COL_FILE, COL_COUNT, COL_DIST, COL_SUBCKT, COL_MODE})
    CHECK_COLUMNS = frozenset({COL_ENABLED, COL_DUMMY})
    DERIVED = frozenset({COL_PORTS, COL_C, COL_SRF})
    FULL_PATH_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, project: Any, context: Callable[[], tuple[str | None, str | None]],
                 parent=None):
        """``context()`` returns (project folder, model search folder) for path resolution."""
        super().__init__(parent)
        self._project = project
        self._context = context
        self._info = DecapModelInfo()
        self.pwr_names: Callable[[], list[str]] = lambda: [r.name for r in
                                                           self._project.pwr_rows]

    @property
    def rows(self) -> list[DecapRow]:
        return self._project.decap_rows

    def set_project(self, project: Any) -> None:
        self.beginResetModel()
        self._project = project
        self.endResetModel()

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def resolved_path(self, row: DecapRow) -> str | None:
        project_dir, search_dir = self._context()
        src = self._project.decap_source_path
        excel_dir = os.path.dirname(src) if src and os.path.isabs(src) else None
        return resolve_model_path(row.model_file, excel_dir, project_dir, search_dir)

    def _errors(self, row: DecapRow) -> dict[int, str]:
        errors: dict[int, str] = {}
        if row.pwr_name not in self.pwr_names():
            errors[self.COL_PWR] = f"PWR '{row.pwr_name}' is not in the PWR list."
        if not row.model_file.strip():
            errors[self.COL_FILE] = "No decap model file."
        elif decap_file_kind(row.model_file) is None:
            errors[self.COL_FILE] = "Unsupported file type (E_DECAP_FILE_TYPE)."
        elif self.resolved_path(row) is None:
            errors[self.COL_FILE] = f"File not found: {row.model_file} (E_DECAP_FILE_NOT_FOUND)."
        if row.count < 1:
            errors[self.COL_COUNT] = "Number of decaps must be ≥ 1."
        if not (row.distance_mm > 0):
            errors[self.COL_DIST] = "Distance to PAD must be > 0 (E_DECAP_DISTANCE)."
        return errors

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole:
            if col == self.COL_ENABLED:
                return Qt.CheckState.Checked if r.enabled else Qt.CheckState.Unchecked
            if col == self.COL_DUMMY:
                return Qt.CheckState.Checked if r.dummy else Qt.CheckState.Unchecked
            return None
        if role == self.FULL_PATH_ROLE:
            return self.resolved_path(r) or r.model_file
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            edit = role == Qt.ItemDataRole.EditRole
            if col == self.COL_PWR:
                return r.pwr_name
            if col == self.COL_FILE:
                return r.model_file if edit else os.path.basename(r.model_file)
            if col == self.COL_SUBCKT:
                return r.subckt or ""
            if col == self.COL_MODE:
                if decap_file_kind(r.model_file) != "s2p" and not edit:
                    return ""
                return r.s2p_mode or ("" if edit else
                                      f"({self._project.advanced.s2p_default_mode})")
            if col == self.COL_COUNT:
                return r.count
            if col == self.COL_DIST:
                return r.distance_mm if edit else f"{r.distance_mm:g}"
            if col == self.COL_PORTS:
                return ports_for_count(r.count, r.dummy)
            if col in (self.COL_C, self.COL_SRF):
                vals = self._info.values(self.resolved_path(r), r.subckt, r.s2p_mode,
                                         self._project.advanced.s2p_default_mode)
                if col == self.COL_C:
                    c = vals.get("c_f")
                    return None if edit and c is None else (c if edit else _format_cap(c))
                srf = vals.get("srf_hz")
                if edit:
                    return None if srf is None else srf / 1e6
                return "—" if srf is None else format_sig(srf / 1e6, 4)
            if col in (self.COL_ENABLED, self.COL_DUMMY):
                return None
        if role == Qt.ItemDataRole.BackgroundRole:
            errors = self._errors(r)
            if col in errors:
                return ERROR_BRUSH
            if col in self.DERIVED:
                return DERIVED_BRUSH
        if role == Qt.ItemDataRole.ToolTipRole:
            errors = self._errors(r)
            if col in errors:
                return errors[col]
            if col == self.COL_FILE:
                return self.resolved_path(r) or r.model_file
            if col == self.COL_DUMMY:
                return ("Dummy Cap: half of the capacitors share the via set of a neighbour "
                        "(ceil(N/2) via sets).")
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (
                self.COL_COUNT, self.COL_DIST, self.COL_PORTS, self.COL_C, self.COL_SRF):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col in (self.COL_ENABLED, self.COL_DUMMY):
            state = value.value if hasattr(value, "value") else value
            checked = int(state) == Qt.CheckState.Checked.value
            if col == self.COL_ENABLED:
                r.enabled = checked
            else:
                r.dummy = checked
        elif role == Qt.ItemDataRole.EditRole:
            try:
                if col == self.COL_PWR:
                    r.pwr_name = str(value).strip()
                elif col == self.COL_FILE:
                    r.model_file = str(value).strip()
                elif col == self.COL_SUBCKT:
                    r.subckt = str(value).strip() or None
                elif col == self.COL_MODE:
                    text = str(value).strip().lower()
                    r.s2p_mode = text if text in S2P_MODES else None  # type: ignore[assignment]
                elif col == self.COL_COUNT:
                    r.count = int(float(value))
                elif col == self.COL_DIST:
                    r.distance_mm = float(str(value).replace(",", "."))
                else:
                    return False
            except (TypeError, ValueError):
                return False
        else:
            return False
        self._emit_row(index.row())
        self.edited.emit()
        return True

    def insert_row(self, pwr_name: str | None = None, template: DecapRow | None = None) -> int:
        pos = len(self.rows)
        if template is not None:
            new = DecapRow(pwr_name=template.pwr_name, model_file=template.model_file,
                           count=template.count, distance_mm=template.distance_mm,
                           dummy=template.dummy, subckt=template.subckt,
                           s2p_mode=template.s2p_mode, enabled=template.enabled)
        else:
            names = self.pwr_names()
            new = DecapRow(pwr_name=pwr_name or (names[0] if names else ""), model_file="",
                           count=1, distance_mm=5.0)
        self.beginInsertRows(QModelIndex(), pos, pos)
        self.rows.append(new)
        self.endInsertRows()
        self.edited.emit()
        return pos

    def remove_rows(self, rows: list[int]) -> None:
        valid = sorted({r for r in rows if 0 <= r < len(self.rows)}, reverse=True)
        if not valid:
            return
        for row in valid:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.rows[row]
            self.endRemoveRows()
        self.edited.emit()

    def rename_pwr(self, old: str, new: str) -> None:
        changed = False
        for r in self.rows:
            if r.pwr_name == old:
                r.pwr_name = new
                changed = True
        if changed and self.rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.rows) - 1, self.columnCount() - 1))

    def refresh(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.rows) - 1, self.columnCount() - 1))


def _format_cap(c: float | None) -> str:
    if c is None or not math.isfinite(c) or c <= 0:
        return "—"
    for scale, unit in ((1e-3, "mF"), (1e-6, "µF"), (1e-9, "nF"), (1e-12, "pF")):
        if c >= scale * 0.9995:
            return f"{format_sig(c / scale, 4)} {unit}"
    return f"{format_sig(c / 1e-15, 4)} fF"


class DecapFilterProxy(QSortFilterProxyModel):
    """Filters decap rows by PWR name (``None`` = all PWRs)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pwr: str | None = None

    @property
    def pwr_filter(self) -> str | None:
        return self._pwr

    def set_pwr_filter(self, name: str | None) -> None:
        if hasattr(self, "beginFilterChange"):  # Qt ≥ 6.9
            self.beginFilterChange()
            self._pwr = name
            self.endFilterChange()
        else:  # pragma: no cover - older Qt
            self._pwr = name
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: Index) -> bool:
        if self._pwr is None:
            return True
        model = self.sourceModel()
        if not isinstance(model, DecapTableModel) or not (0 <= source_row < len(model.rows)):
            return True
        return model.rows[source_row].pwr_name == self._pwr


__all__ = [
    "StackupTableModel",
    "PwrTableModel",
    "DecapTableModel",
    "DecapFilterProxy",
    "DecapModelInfo",
]
