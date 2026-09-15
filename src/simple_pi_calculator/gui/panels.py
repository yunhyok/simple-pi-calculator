"""Input panels of the left tab widget (DESIGN.md §5.5)."""

from __future__ import annotations

import os
from typing import Any, Callable, Sequence

import numpy as np
from PySide6.QtCore import QEvent, QObject, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_pi_calculator.constants import (
    DEFAULT_DISTANCE_SEED,
    DEFAULT_DISTANCE_SIGMA_MM,
    DISTANCE_SEED_MAX,
    DISTANCE_SIGMA_MAX_MM,
    DISTANCE_SIGMA_MIN_MM,
    F_START_MIN_HZ,
    F_STOP_MAX_HZ,
    MAX_N_PADS,
    MAX_WORKERS,
    N_POINTS_MAX,
    N_POINTS_MIN,
    S2P_MODES,
    VIA_MODELS,
)
from simple_pi_calculator.core.stackup import is_metal_conductivity
from simple_pi_calculator.core.units import format_frequency, parse_frequency
from simple_pi_calculator.gui.delegates import (
    CheckBoxDelegate,
    ComboDelegate,
    DoubleSpinDelegate,
    FileBrowseDelegate,
    SpinDelegate,
)
from simple_pi_calculator.gui.models import (
    DecapFilterProxy,
    DecapTableModel,
    PwrTableModel,
    StackupTableModel,
)
from simple_pi_calculator.gui.placement_preview import PlacementPreview

ALL_PWRS = "All PWRs"


def _tool_button(text: str, tip: str, parent: QWidget) -> QToolButton:
    btn = QToolButton(parent)
    btn.setText(text)
    btn.setToolTip(tip)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    return btn


def _setup_table(view: QTableView) -> None:
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                         | QAbstractItemView.EditTrigger.EditKeyPressed
                         | QAbstractItemView.EditTrigger.AnyKeyPressed)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    view.verticalHeader().setDefaultSectionSize(22)


class _FillColumn(QObject):
    """Keeps ``column`` as wide as the viewport space left by ``primary`` columns (min width).

    Unlike ``QHeaderView.Stretch`` this still works when further (derived) columns follow and
    the table scrolls horizontally: the primary columns always fit the visible width.
    """

    def __init__(self, view: QTableView, column: int, primary: Sequence[int],
                 minimum: int = 120):
        super().__init__(view)
        self.view, self.column, self.primary, self.minimum = view, column, list(primary), minimum
        view.viewport().installEventFilter(self)
        view.horizontalHeader().sectionResized.connect(self._on_section_resized)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self.apply)
        return False

    def _on_section_resized(self, index: int, _old: int, _new: int) -> None:
        if index != self.column and index in self.primary:
            QTimer.singleShot(0, self.apply)

    def apply(self) -> None:
        header = self.view.horizontalHeader()
        used = sum(header.sectionSize(c) for c in self.primary if c != self.column)
        width = max(self.minimum, self.view.viewport().width() - used - 1)
        if header.sectionSize(self.column) != width:
            header.resizeSection(self.column, width)


def size_columns(view: QTableView, fill: int, fit: Sequence[int],
                 primary: Sequence[int] | None = None, minimum: int = 120) -> None:
    """Column sizing: ``fit`` columns ResizeToContents; ``fill`` takes the viewport width left by
    the ``primary`` columns (default: all); other columns interactive; last section not
    stretched."""
    header = view.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(28)
    n = view.model().columnCount()
    for col in range(n):
        mode = (QHeaderView.ResizeMode.ResizeToContents if col in fit
                else QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(col, mode)
    view._fill_column = _FillColumn(view, fill, primary if primary is not None else range(n),
                                    minimum)


def selected_source_rows(view: QTableView) -> list[int]:
    """Selected rows mapped to the source model (handles proxies)."""
    model = view.model()
    rows = set()
    for idx in view.selectionModel().selectedRows() if view.selectionModel() else []:
        src = model.mapToSource(idx) if hasattr(model, "mapToSource") else idx
        rows.add(src.row())
    if not rows and view.currentIndex().isValid():
        idx = view.currentIndex()
        src = model.mapToSource(idx) if hasattr(model, "mapToSource") else idx
        rows.add(src.row())
    return sorted(rows)


# =============================================================================================
# Stack-up
# =============================================================================================
class StackupPreview(QWidget):
    """Cross-section painting: metal = copper, dielectric = light green, PWR/GND highlighted."""

    def __init__(self, model: StackupTableModel, parent: QWidget | None = None):
        super().__init__(parent)
        self._model = model
        self.pair: tuple[int, int] | None = None
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(360, 200)

    def set_pair(self, pair: tuple[int, int] | None) -> None:
        self.pair = pair
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#ffffff"))
            rows = sorted(self._model.rows, key=lambda r: r.number)
            if not rows:
                painter.setPen(QColor("#666666"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                                 "No stack-up loaded — use Import… or Add Layer.")
                return
            # visual thickness: metal fixed, dielectric proportional with a minimum
            metal_px, min_diel_px = 6.0, 8.0
            is_metal = [is_metal_conductivity(r.conductivity_s_per_m) for r in rows]
            diel_total = sum(max(r.thickness_mm, 0.0) for r, m in zip(rows, is_metal) if not m)
            n_metal = sum(is_metal)
            n_diel = len(rows) - n_metal
            avail = max(self.height() - 8 - metal_px * n_metal - min_diel_px * n_diel, 0.0)
            y = 4.0
            left, width = 60.0, max(self.width() - 190.0, 60.0)
            pwr, gnd = self.pair if self.pair else (None, None)
            font = painter.font()
            font.setPointSizeF(7.5)
            painter.setFont(font)
            last_label = -1e9
            for r, metal in zip(rows, is_metal):
                if metal:
                    px = metal_px
                else:
                    share = max(r.thickness_mm, 0.0) / diel_total if diel_total else 0.0
                    px = min_diel_px + avail * share
                rect = QRectF(left, y, width, px)
                color = QColor("#c87533") if metal else QColor("#cdebc4")
                if r.number == pwr:
                    color = QColor("#d62728")
                elif r.number == gnd:
                    color = QColor("#1f3d99")
                painter.fillRect(rect, QBrush(color))
                painter.setPen(QPen(QColor("#909090"), 0.5))
                painter.drawRect(rect)
                centre = y + px / 2
                if centre - last_label >= 11 or r.number in (pwr, gnd):
                    last_label = centre
                    painter.setPen(QColor("#202020"))
                    painter.drawText(QRectF(4, centre - 7, left - 10, 14),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                     str(r.number))
                    tag = r.name
                    if r.number == pwr:
                        tag += "  (PWR)"
                    elif r.number == gnd:
                        tag += "  (GND)"
                    painter.drawText(QRectF(left + width + 6, centre - 7, 180, 14),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                     tag)
                y += px
        finally:
            painter.end()


class StackupPanel(QWidget):
    importRequested = Signal()
    reimportRequested = Signal()

    def __init__(self, model: StackupTableModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(self)
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("(no Excel file)")
        self.import_button = QPushButton("Import…", self)
        self.reimport_button = QPushButton("Re-import", self)
        self.add_button = _tool_button("Add Layer", "Append a layer row", self)
        self.remove_button = _tool_button("Remove", "Remove the selected layers", self)
        row.addWidget(QLabel("Excel:", self))
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.import_button)
        row.addWidget(self.reimport_button)
        layout.addLayout(row)
        tools = QHBoxLayout()
        tools.addWidget(self.add_button)
        tools.addWidget(self.remove_button)
        tools.addStretch(1)
        tools.addWidget(QLabel("Type is detected from σ: σ > 0 → Metal, empty/0 → Dielectric",
                               self))
        layout.addLayout(tools)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.table = QTableView(splitter)
        _setup_table(self.table)
        self.table.setModel(model)
        self.table.setItemDelegateForColumn(StackupTableModel.COL_NUMBER,
                                            SpinDelegate(self.table, 1, 999))
        size_columns(self.table, StackupTableModel.COL_NAME,
                     [c for c in range(model.columnCount()) if c != StackupTableModel.COL_NAME],
                     minimum=80)
        self.preview = StackupPreview(model, splitter)
        splitter.addWidget(self.table)
        splitter.addWidget(self.preview)
        splitter.setSizes([320, 180])
        layout.addWidget(splitter, 1)
        self.import_button.clicked.connect(self.importRequested)
        self.reimport_button.clicked.connect(self.reimportRequested)
        self.add_button.clicked.connect(lambda: self.model.insert_row())
        self.remove_button.clicked.connect(
            lambda: self.model.remove_rows(selected_source_rows(self.table)))
        model.modelReset.connect(self.preview.update)
        model.dataChanged.connect(lambda *_: self.preview.update())
        model.rowsInserted.connect(lambda *_: self.preview.update())
        model.rowsRemoved.connect(lambda *_: self.preview.update())

    def set_source_path(self, path: str | None) -> None:
        self.path_edit.setText(path or "")
        self.path_edit.setToolTip(path or "")
        self.reimport_button.setEnabled(bool(path))


# =============================================================================================
# Vias
# =============================================================================================
def _dspin(parent: QWidget, lo: float, hi: float, decimals: int, step: float,
           suffix: str = "") -> QDoubleSpinBox:
    spin = QDoubleSpinBox(parent)
    spin.setRange(lo, hi)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setKeyboardTracking(False)
    if suffix:
        spin.setSuffix(suffix)
    return spin


class ViaPanel(QWidget):
    """Via settings form with a collapsible Advanced group (§5.5 tab 2)."""

    edited = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._project: Any = None
        outer = QVBoxLayout(self)
        form = self.form = QFormLayout()
        self.drill = _dspin(self, 0.01, 5.0, 3, 0.01, " mm")
        self.antipad = _dspin(self, 0.01, 10.0, 3, 0.01, " mm")
        self.pitch = _dspin(self, 0.02, 20.0, 3, 0.05, " mm")
        self.pitch.setToolTip("PWR–GND via centre spacing. It sets the via-pair loop inductance "
                              "and, for several pairs, the via-cluster size (port width).")
        self.vias_per_pad = QSpinBox(self)
        self.vias_per_pad.setRange(1, 32)
        self.vias_per_pad.setKeyboardTracking(False)
        self.vias_per_pad.setToolTip(
            "Number of parallel vias on EACH of the two pads of one decap (default 1; 2 and 4 "
            "are common).\nA decap via set is then n PWR vias + n GND vias = n PWR/GND via "
            "pairs in parallel:\nZ_via,dec = Z_viapair / n, and the cavity port of the set "
            "widens to the via cluster (GMD rule).")
        self.pad_vias = QSpinBox(self)
        self.pad_vias.setRange(1, 400)
        self.pad_vias.setKeyboardTracking(False)
        self.pad_vias.setToolTip(
            "PAD vias = PWR/GND via pairs per observation pad (the IC pads where |Z| is "
            "computed).\nEvery one of the PWR net's PADs ('# PADs' column of the PWR list) has "
            "its own via set:\nZ_via,pad = Z_viapair / count per pad, so the total PAD via count "
            "is # PADs × count.")
        form.addRow("Drill diameter", self.drill)
        form.addRow("Anti-pad diameter", self.antipad)
        form.addRow("Via pitch (PWR–GND via centre spacing)", self.pitch)
        form.addRow("Vias per decap pad", self.vias_per_pad)
        form.addRow("PAD vias (per observation pad)", self.pad_vias)
        form.addRow("", QLabel("Decaps and PAD are mounted on the Top side", self))
        outer.addLayout(form)

        derived = QGroupBox("Derived for the selected PWR", self)
        dform = QFormLayout(derived)
        self.h_near_label = QLabel("—", derived)
        self.l_loop_label = QLabel("—", derived)
        self.port_width_label = QLabel("—", derived)
        dform.addRow("h_near", self.h_near_label)
        dform.addRow("L_loop", self.l_loop_label)
        dform.addRow("PAD / decap port width", self.port_width_label)
        outer.addWidget(derived)

        self.advanced = QGroupBox("Advanced", self)
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        self._adv_body = QWidget(self.advanced)
        aform = QFormLayout(self._adv_body)
        self.via_model = QComboBox(self._adv_body)
        for key, label in (("pair", "Via pair (image partial inductance)"),
                           ("goldfarb_pucel", "Goldfarb–Pucel"), ("coax", "Coaxial (legacy)")):
            if key in VIA_MODELS:
                self.via_model.addItem(label, key)
        self.plating = _dspin(self._adv_body, 0.001, 1.0, 4, 0.005, " mm")
        self.conductivity = _dspin(self._adv_body, 1.0e3, 1.0e9, 0, 1.0e6, " S/m")
        self.mounting = _dspin(self._adv_body, 0.0, 100.0, 4, 0.05, " nH")
        self.mounting.setToolTip("Mounting inductance per capacitor")
        self.s2p_mode = QComboBox(self._adv_body)
        for mode in S2P_MODES:
            self.s2p_mode.addItem(mode, mode)
        self.search_dir = QLineEdit(self._adv_body)
        self.search_dir.setPlaceholderText("(none)")
        browse = QToolButton(self._adv_body)
        browse.setText("…")
        browse.clicked.connect(self._browse_search_dir)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search_dir, 1)
        search_row.addWidget(browse)
        aform.addRow("Via model", self.via_model)
        aform.addRow("Plating thickness", self.plating)
        aform.addRow("Via conductivity", self.conductivity)
        aform.addRow("Mounting inductance per capacitor", self.mounting)
        aform.addRow("S2P default mode", self.s2p_mode)
        aform.addRow("Model search folder", search_row)
        self.workers = QSpinBox(self._adv_body)
        self.workers.setRange(0, MAX_WORKERS)
        self.workers.setSpecialValueText(f"Auto ({os.cpu_count() or 1})")
        self.workers.setKeyboardTracking(False)
        self.workers.setToolTip("Worker threads used by the computation (0 = Auto = number of "
                                "logical CPUs). Does not change the results.")
        aform.addRow("Worker threads", self.workers)
        adv_layout = QVBoxLayout(self.advanced)
        adv_layout.addWidget(self._adv_body)
        self._adv_body.setVisible(False)
        self.advanced.toggled.connect(self._adv_body.setVisible)
        outer.addWidget(self.advanced)
        outer.addStretch(1)

        for spin in (self.drill, self.antipad, self.pitch, self.plating, self.conductivity,
                     self.mounting):
            spin.valueChanged.connect(self._write)
        for spin in (self.vias_per_pad, self.pad_vias, self.workers):
            spin.valueChanged.connect(self._write)
        self.via_model.currentIndexChanged.connect(self._write)
        self.s2p_mode.currentIndexChanged.connect(self._write)
        self.search_dir.editingFinished.connect(self._write)

    def _widgets(self) -> list[QWidget]:
        return [self.drill, self.antipad, self.pitch, self.vias_per_pad, self.pad_vias,
                self.via_model, self.plating, self.conductivity, self.mounting, self.s2p_mode,
                self.search_dir, self.workers]

    def load(self, project: Any) -> None:
        self._project = project
        blockers = [QSignalBlocker(w) for w in self._widgets()]
        try:
            v, a = project.vias, project.advanced
            self.drill.setValue(v.drill_diameter_mm)
            self.antipad.setValue(v.antipad_diameter_mm)
            self.pitch.setValue(v.via_pitch_mm)
            self.vias_per_pad.setValue(int(v.vias_per_pad))
            self.pad_vias.setValue(int(v.pad_via_count))
            i = self.via_model.findData(a.via_model)
            self.via_model.setCurrentIndex(max(i, 0))
            self.plating.setValue(a.plating_thickness_mm)
            self.conductivity.setValue(a.via_conductivity_s_per_m)
            self.mounting.setValue(a.mounting_inductance_nh)
            j = self.s2p_mode.findData(a.s2p_default_mode)
            self.s2p_mode.setCurrentIndex(max(j, 0))
            self.search_dir.setText(a.model_search_dir or "")
            self.workers.setValue(int(getattr(a, "workers", 0) or 0))
        finally:
            del blockers

    def _write(self, *_args) -> None:
        if self._project is None:
            return
        v, a = self._project.vias, self._project.advanced
        v.drill_diameter_mm = float(self.drill.value())
        v.antipad_diameter_mm = float(self.antipad.value())
        v.via_pitch_mm = float(self.pitch.value())
        v.vias_per_pad = int(self.vias_per_pad.value())
        v.pad_via_count = int(self.pad_vias.value())
        a.via_model = str(self.via_model.currentData() or "pair")
        a.plating_thickness_mm = float(self.plating.value())
        a.via_conductivity_s_per_m = float(self.conductivity.value())
        a.mounting_inductance_nh = float(self.mounting.value())
        a.s2p_default_mode = str(self.s2p_mode.currentData() or "series")
        a.model_search_dir = self.search_dir.text().strip() or None
        a.workers = int(self.workers.value())
        self.edited.emit()

    def _browse_search_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Model search folder",
                                                  self.search_dir.text() or os.getcwd())
        if folder:
            self.search_dir.setText(os.path.normpath(folder))
            self._write()

    def set_derived(self, h_near_mm: float | None, l_loop_nh: float | None,
                    w_pad_mm: float | None, w_dec_mm: float | None) -> None:
        self.h_near_label.setText("—" if h_near_mm is None else f"{h_near_mm:.4f} mm")
        self.l_loop_label.setText("—" if l_loop_nh is None else f"{l_loop_nh:.4f} nH")
        if w_pad_mm is None or w_dec_mm is None:
            self.port_width_label.setText("—")
        else:
            self.port_width_label.setText(f"{w_pad_mm:.5f} mm / {w_dec_mm:.5f} mm")


# =============================================================================================
# PWR nets
# =============================================================================================
class PwrPanel(QWidget):
    importRequested = Signal()
    selectedPwrChanged = Signal(object)  # str | None

    def __init__(self, model: PwrTableModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.import_button = _tool_button("Import…", "Import a PWR list from Excel", self)
        self.add_button = _tool_button("Add", "Add a PWR net", self)
        self.remove_button = _tool_button("Remove", "Remove the selected PWR nets", self)
        self.duplicate_button = _tool_button("Duplicate", "Duplicate the selected PWR net", self)
        for b in (self.import_button, self.add_button, self.remove_button,
                  self.duplicate_button):
            tools.addWidget(b)
        tools.addStretch(1)
        self.source_label = QLabel("", self)
        tools.addWidget(self.source_label)
        layout.addLayout(tools)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.table = QTableView(splitter)
        _setup_table(self.table)
        self.table.setModel(model)
        self.table.setItemDelegateForColumn(PwrTableModel.COL_ENABLED,
                                            CheckBoxDelegate(self.table))
        self.table.setItemDelegateForColumn(PwrTableModel.COL_LAYER,
                                            SpinDelegate(self.table, 1, 999))
        self.table.setItemDelegateForColumn(PwrTableModel.COL_GND,
                                            SpinDelegate(self.table, 1, 999))
        self.table.setItemDelegateForColumn(PwrTableModel.COL_WIDTH,
                                            DoubleSpinDelegate(self.table, 0.0, 10000.0, 3, 1.0))
        self.table.setItemDelegateForColumn(PwrTableModel.COL_NPADS,
                                            SpinDelegate(self.table, 1, MAX_N_PADS))
        size_columns(self.table, PwrTableModel.COL_NAME,
                     [c for c in range(model.columnCount()) if c != PwrTableModel.COL_NAME],
                     minimum=80)
        self.preview = PlacementPreview(splitter)
        splitter.addWidget(self.table)
        splitter.addWidget(self.preview)
        splitter.setSizes([260, 240])
        layout.addWidget(splitter, 1)
        self.import_button.clicked.connect(self.importRequested)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(
            lambda: self.model.remove_rows(selected_source_rows(self.table)))
        self.duplicate_button.clicked.connect(self._duplicate)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._emit_selected())
        model.modelReset.connect(self._emit_selected)
        model.rowsRemoved.connect(lambda *_: self._emit_selected())

    def _add(self) -> None:
        row = self.model.insert_row()
        self.table.selectRow(row)

    def _duplicate(self) -> None:
        rows = selected_source_rows(self.table)
        if rows:
            row = self.model.insert_row(template=self.model.rows[rows[0]])
            self.table.selectRow(row)

    def selected_pwr(self) -> str | None:
        idx = self.table.currentIndex()
        if idx.isValid() and 0 <= idx.row() < len(self.model.rows):
            return self.model.rows[idx.row()].name
        return None

    def select_pwr(self, name: str | None) -> None:
        for i, r in enumerate(self.model.rows):
            if r.name == name:
                self.table.selectRow(i)
                return

    def _emit_selected(self) -> None:
        self.selectedPwrChanged.emit(self.selected_pwr())


# =============================================================================================
# Decaps
# =============================================================================================
class DecapModelPreviewDialog(QDialog):
    """Small dialog plotting |Z_decap| of the selected row (§5.5)."""

    def __init__(self, parent: QWidget | None, title: str, f_hz: np.ndarray, z: np.ndarray):
        super().__init__(parent)
        import pyqtgraph as pg
        self.setWindowTitle(f"Decap model — {title}")
        self.resize(560, 380)
        layout = QVBoxLayout(self)
        plot = pg.PlotWidget(self)
        plot.setLogMode(x=True, y=True)
        plot.showGrid(x=True, y=True, alpha=0.3)
        plot.setLabel("bottom", "Frequency (Hz)")
        plot.setLabel("left", "|Z| (Ω)")
        plot.getAxis("left").enableAutoSIPrefix(False)
        plot.getAxis("bottom").enableAutoSIPrefix(False)
        plot.plot(f_hz, np.maximum(np.abs(z), 1e-15), pen=pg.mkPen("#1f77b4", width=2))
        layout.addWidget(plot)
        self.plot = plot


class DecapPanel(QWidget):
    importRequested = Signal()
    filterChanged = Signal(object)  # str | None
    #: the global distance distribution (mode, σ or seed) was changed by the user (§2.5.5)
    edited = Signal()

    def __init__(self, model: DecapTableModel, pwr_names: Callable[[], list[str]],
                 start_dir: Callable[[], str], parent: QWidget | None = None):
        super().__init__(parent)
        self.model = model
        self._pwr_names = pwr_names
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("PWR:", self))
        self.filter_combo = QComboBox(self)
        self.filter_combo.setMinimumWidth(160)
        top.addWidget(self.filter_combo)
        self.import_button = _tool_button("Import…", "Import a decap list from Excel", self)
        self.add_button = _tool_button("Add", "Add a decap row (PWR = filter)", self)
        self.remove_button = _tool_button("Remove", "Remove the selected decap rows", self)
        self.duplicate_button = _tool_button("Duplicate", "Duplicate the selected row", self)
        self.preview_button = _tool_button("Preview model", "Plot |Z| of the selected model",
                                           self)
        for b in (self.import_button, self.add_button, self.remove_button,
                  self.duplicate_button, self.preview_button):
            top.addWidget(b)
        top.addStretch(1)
        layout.addLayout(top)

        # global distance distribution (§2.5.5, §5.5): applies to every decap row
        self._project: Any = None
        dist = QHBoxLayout()
        dist.addWidget(QLabel("Distance:", self))
        self.distance_mode = QComboBox(self)
        self.distance_mode.addItem("Fixed", "fixed")
        self.distance_mode.addItem("Normal (±1σ)", "normal")
        self.distance_mode.setToolTip(
            "Distance distribution of the capacitors of every decap row.\n"
            "Fixed: every via set at exactly 'Distance to PAD' (0.2.0 behaviour).\n"
            "Normal (±1σ): each via set at D + σ·z, z drawn from a standard normal distribution "
            "truncated to ±1,\nso all distances lie in [D − σ, D + σ]; reproducible for a given "
            "seed. A Dummy Cap via set\n(two capacitors) gets one sample.")
        dist.addWidget(self.distance_mode)
        self.sigma_label = QLabel("σ (mm)", self)
        dist.addWidget(self.sigma_label)
        self.sigma = _dspin(self, DISTANCE_SIGMA_MIN_MM, DISTANCE_SIGMA_MAX_MM, 3, 0.05)
        self.sigma.setValue(DEFAULT_DISTANCE_SIGMA_MM)
        self.sigma.setToolTip("Standard deviation σ of the distance distribution (absolute, mm). "
                              "Samples are truncated to ±1σ.")
        dist.addWidget(self.sigma)
        self.seed_label = QLabel("Seed", self)
        dist.addWidget(self.seed_label)
        self.seed = QSpinBox(self)
        self.seed.setRange(0, DISTANCE_SEED_MAX)
        self.seed.setValue(DEFAULT_DISTANCE_SEED)
        self.seed.setKeyboardTracking(False)
        self.seed.setMinimumWidth(110)
        self.seed.setToolTip("Random seed: the same seed gives the same sampled distances "
                             "(stored in the project).")
        dist.addWidget(self.seed)
        self.new_seed_button = _tool_button("New seed", "Draw a new random seed", self)
        dist.addWidget(self.new_seed_button)
        dist.addStretch(1)
        layout.addLayout(dist)
        self.distance_mode.currentIndexChanged.connect(self._write_distance)
        self.sigma.valueChanged.connect(self._write_distance)
        self.seed.valueChanged.connect(self._write_distance)
        self.new_seed_button.clicked.connect(self.new_seed)
        self._update_distance_enabled()

        self.proxy = DecapFilterProxy(self)
        self.proxy.setSourceModel(model)
        self.table = QTableView(self)
        _setup_table(self.table)
        self.table.setModel(self.proxy)
        for col in (DecapTableModel.COL_ENABLED, DecapTableModel.COL_DUMMY):
            self.table.setItemDelegateForColumn(col, CheckBoxDelegate(self.table))
        self.table.setItemDelegateForColumn(DecapTableModel.COL_PWR,
                                            ComboDelegate(self.table, pwr_names, editable=True))
        self.table.setItemDelegateForColumn(DecapTableModel.COL_FILE,
                                            FileBrowseDelegate(self.table, start_dir))
        self.table.setItemDelegateForColumn(DecapTableModel.COL_MODE,
                                            ComboDelegate(self.table,
                                                          lambda: ["", *S2P_MODES]))
        self.table.setItemDelegateForColumn(DecapTableModel.COL_COUNT,
                                            SpinDelegate(self.table, 1, 100000))
        self.table.setItemDelegateForColumn(DecapTableModel.COL_DIST,
                                            DoubleSpinDelegate(self.table, 0.0, 10000.0, 3, 0.5))
        primary = list(range(DecapTableModel.COL_MODE + 1))
        size_columns(self.table, DecapTableModel.COL_FILE,
                     [c for c in range(model.columnCount()) if c != DecapTableModel.COL_FILE],
                     primary=primary, minimum=110)
        layout.addWidget(self.table, 1)
        self.last_preview: DecapModelPreviewDialog | None = None

        self.import_button.clicked.connect(self.importRequested)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(
            lambda: self.model.remove_rows(selected_source_rows(self.table)))
        self.duplicate_button.clicked.connect(self._duplicate)
        self.preview_button.clicked.connect(self.preview_selected_model)
        self.filter_combo.currentIndexChanged.connect(self._on_filter)
        self.refresh_filter_items()

    # -- distance distribution (§2.5.5) -------------------------------------------------------------
    def load(self, project: Any) -> None:
        """Show the project's distance distribution (no ``edited`` signal)."""
        self._project = project
        d = project.distance
        with QSignalBlocker(self.distance_mode), QSignalBlocker(self.sigma), \
                QSignalBlocker(self.seed):
            i = self.distance_mode.findData(d.mode)
            self.distance_mode.setCurrentIndex(max(i, 0))
            self.sigma.setValue(float(d.sigma_mm))
            self.seed.setValue(int(d.seed))
        self._update_distance_enabled()

    def _update_distance_enabled(self) -> None:
        normal = self.distance_mode.currentData() == "normal"
        for w in (self.sigma_label, self.sigma, self.seed_label, self.seed,
                  self.new_seed_button):
            w.setEnabled(normal)

    def _write_distance(self, *_args) -> None:
        self._update_distance_enabled()
        if self._project is None:
            return
        d = self._project.distance
        new = (str(self.distance_mode.currentData() or "fixed"), float(self.sigma.value()),
               int(self.seed.value()))
        if (d.mode, float(d.sigma_mm), int(d.seed)) == new:
            return
        d.mode, d.sigma_mm, d.seed = new
        self.edited.emit()

    def new_seed(self) -> int:
        """Set a new random seed (different from the current one) and return it."""
        import secrets
        current = int(self.seed.value())
        value = current
        while value == current:
            value = secrets.randbelow(DISTANCE_SEED_MAX + 1)
        self.seed.setValue(value)
        return value

    # -- filter -----------------------------------------------------------------------------------
    def refresh_filter_items(self) -> None:
        current = self.proxy.pwr_filter
        with QSignalBlocker(self.filter_combo):
            self.filter_combo.clear()
            self.filter_combo.addItem(ALL_PWRS, None)
            for name in self._pwr_names():
                self.filter_combo.addItem(name, name)
            i = self.filter_combo.findData(current) if current is not None else 0
            if i < 0:
                i = 0
            self.filter_combo.setCurrentIndex(i)
        self.proxy.set_pwr_filter(self.filter_combo.currentData())

    def set_filter(self, name: str | None) -> None:
        i = self.filter_combo.findData(name) if name is not None else 0
        self.filter_combo.setCurrentIndex(max(i, 0))
        self._on_filter()

    def current_filter(self) -> str | None:
        return self.proxy.pwr_filter

    def _on_filter(self, *_args) -> None:
        name = self.filter_combo.currentData()
        if name != self.proxy.pwr_filter:
            self.proxy.set_pwr_filter(name)
            self.filterChanged.emit(name)

    # -- rows -------------------------------------------------------------------------------------
    def _add(self) -> None:
        self.model.insert_row(pwr_name=self.proxy.pwr_filter)
        last = self.proxy.rowCount() - 1
        if last >= 0:
            self.table.selectRow(last)

    def _duplicate(self) -> None:
        rows = selected_source_rows(self.table)
        if rows:
            self.model.insert_row(template=self.model.rows[rows[0]])

    def preview_selected_model(self) -> DecapModelPreviewDialog | None:
        rows = selected_source_rows(self.table)
        if not rows:
            return None
        row = self.model.rows[rows[0]]
        path = self.model.resolved_path(row)
        from PySide6.QtWidgets import QMessageBox

        from simple_pi_calculator.core.decap_model import DecapModelCache
        from simple_pi_calculator.errors import InputError, IssueCollector
        if path is None:
            QMessageBox.warning(self, "Preview model", f"Model file not found: {row.model_file}")
            return None
        try:
            model = DecapModelCache().get(path, row.subckt,
                                          row.s2p_mode or self.model._project.advanced
                                          .s2p_default_mode, IssueCollector())
            f = np.logspace(3, 10, 500)
            z = np.asarray(model.impedance(f), dtype=complex)
        except (InputError, Exception) as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Preview model", f"Cannot load the model:\n{exc}")
            return None
        dlg = DecapModelPreviewDialog(self, os.path.basename(path), f, z)
        dlg.show()
        self.last_preview = dlg
        return dlg


# =============================================================================================
# Sweep
# =============================================================================================
class SweepPanel(QWidget):
    edited = Signal()
    planeOnlyToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._project: Any = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.f_start = QLineEdit(self)
        self.f_stop = QLineEdit(self)
        tip = ("Engineering notation: k/K = 1e3, M or meg = 1e6, G = 1e9, optional 'Hz' "
               "(lower-case m is rejected), e.g. 100k, 2.5MHz, 1G")
        self.f_start.setToolTip(tip)
        self.f_stop.setToolTip(tip)
        self.points = QSpinBox(self)
        self.points.setRange(N_POINTS_MIN, N_POINTS_MAX)
        self.points.setKeyboardTracking(False)
        self.plane_only = QCheckBox("Show plane-only curve (no decaps)", self)
        self.status = QLabel("", self)
        self.status.setStyleSheet("color: #b00020;")
        form.addRow("f start", self.f_start)
        form.addRow("f stop", self.f_stop)
        form.addRow("Points (log spaced)", self.points)
        form.addRow("", self.plane_only)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.f_start.editingFinished.connect(self._write)
        self.f_stop.editingFinished.connect(self._write)
        self.points.valueChanged.connect(self._write)
        self.plane_only.toggled.connect(self._on_plane_only)

    def load(self, project: Any) -> None:
        self._project = project
        with QSignalBlocker(self.f_start), QSignalBlocker(self.f_stop), \
                QSignalBlocker(self.points), QSignalBlocker(self.plane_only):
            self.f_start.setText(_freq_text(project.sweep.f_start_hz))
            self.f_stop.setText(_freq_text(project.sweep.f_stop_hz))
            self.points.setValue(int(project.sweep.n_points))
            self.plane_only.setChecked(bool(project.sweep.show_plane_only))
        self._set_valid(self.f_start, True)
        self._set_valid(self.f_stop, True)
        self.status.setText("")

    @staticmethod
    def _set_valid(edit: QLineEdit, ok: bool) -> None:
        edit.setStyleSheet("" if ok else "QLineEdit { background-color: #f8d0d0; }")

    def _write(self, *_args) -> None:
        if self._project is None:
            return
        sw = self._project.sweep
        messages = []
        changed = False
        for edit, attr in ((self.f_start, "f_start_hz"), (self.f_stop, "f_stop_hz")):
            try:
                value = parse_frequency(edit.text())
                if not (F_START_MIN_HZ <= value <= F_STOP_MAX_HZ):
                    raise ValueError("out of range")
            except ValueError:
                self._set_valid(edit, False)
                messages.append(f"Invalid frequency '{edit.text()}' (allowed "
                                f"{format_frequency(F_START_MIN_HZ)} … "
                                f"{format_frequency(F_STOP_MAX_HZ)}).")
                continue
            self._set_valid(edit, True)
            if getattr(sw, attr) != value:
                setattr(sw, attr, value)
                changed = True
        if sw.f_start_hz >= sw.f_stop_hz:
            messages.append("f start must be below f stop (E_SWEEP_RANGE).")
        if sw.n_points != int(self.points.value()):
            sw.n_points = int(self.points.value())
            changed = True
        self.status.setText("\n".join(messages))
        if changed:
            self.edited.emit()

    def _on_plane_only(self, checked: bool) -> None:
        if self._project is not None:
            self._project.sweep.show_plane_only = bool(checked)
        self.planeOnlyToggled.emit(bool(checked))
        self.edited.emit()


def _freq_text(f_hz: float) -> str:
    for scale, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if f_hz >= scale:
            return f"{f_hz / scale:.6g}{suffix}"
    return f"{f_hz:.6g}"


__all__ = ["StackupPanel", "StackupPreview", "ViaPanel", "PwrPanel", "DecapPanel",
           "SweepPanel", "DecapModelPreviewDialog", "ALL_PWRS", "selected_source_rows"]
