"""Main window (DESIGN.md §5.4, §5.5, §5.8)."""

from __future__ import annotations

import logging
import math
import os
import sys
from typing import Any, Sequence

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QGuiApplication, \
    QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QMenu,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_pi_calculator import __version__
from simple_pi_calculator.constants import (
    APP_DISPLAY_NAME,
    MARKER_FREQUENCIES_HZ,
    MAX_RECENT_FILES,
)
from simple_pi_calculator.core.types import rows_from_stackup
from simple_pi_calculator.core.units import MM, format_frequency, format_sig, z_label, z_scale
from simple_pi_calculator.errors import InputError, Issue, IssueCollector, Severity
from simple_pi_calculator.gui.engine_bridge import EngineBridge, EngineUnavailableError
from simple_pi_calculator.gui.help_window import HELP_PAGES, HelpWindow, help_dir
from simple_pi_calculator.gui.message_dock import MessageDock
from simple_pi_calculator.gui.models import DecapTableModel, PwrTableModel, StackupTableModel
from simple_pi_calculator.gui.panels import DecapPanel, PwrPanel, StackupPanel, SweepPanel, \
    ViaPanel
from simple_pi_calculator.gui.persistence import (
    AutosaveManager,
    app_settings,
    appdata_dir,
    b64_from_qbytearray,
    ensure_dir,
    qbytearray_from_b64,
)
from simple_pi_calculator.gui.export_dialogs import (
    CsvExportDialog,
    ExportPlotsDialog,
    PlotImagesOptions,
    TouchstoneExportDialog,
)
from simple_pi_calculator.gui.plot_widget import (
    RESET_VIEW_SHORTCUT,
    ImpedancePlot,
    series_color,
)
from simple_pi_calculator.io.export import TouchstoneOptions, safe_file_name
from simple_pi_calculator.gui.worker import ComputeWorker
from simple_pi_calculator.io.project_io import (
    AutosaveStore,
    PlotView,
    Project,
    ProjectFormatError,
    ProjectTooNewError,
    Session,
    WindowState,
    ensure_project_suffix,
    load_project,
    project_stem,
    save_project,
)

log = logging.getLogger(__name__)

PROJECT_FILTER = "Simple PI Calculator project (*.spical.json);;JSON files (*.json);;All files (*)"
EXCEL_FILTER = "Excel workbooks (*.xlsx *.xlsm);;All files (*)"
OVERVIEW_TAB = "All PWRs"

TAB_STACKUP, TAB_VIAS, TAB_PWR, TAB_DECAPS, TAB_SWEEP = range(5)


def examples_dir() -> str | None:
    """Folder with the bundled examples (source tree, PyInstaller bundle or next to the exe)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.normpath(os.path.join(here, "..", "..", "..", "examples"))]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "examples"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "examples"))
    for c in candidates:
        if os.path.isfile(os.path.join(c, "example_project.spical.json")):
            return c
    return None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def tab_for_issue(issue: Issue) -> int | None:
    code = issue.code
    for prefix, tab in (("E_STACK", TAB_STACKUP), ("W_STACK", TAB_STACKUP),
                        ("E_VIA", TAB_VIAS), ("W_VIA", TAB_VIAS),
                        ("E_PWR", TAB_PWR), ("W_PWR", TAB_PWR), ("E_DREF", TAB_PWR),
                        ("E_DECAP", TAB_DECAPS), ("W_DECAP", TAB_DECAPS),
                        ("W_PORT", TAB_PWR), ("E_SPICE", TAB_DECAPS), ("W_SPICE", TAB_DECAPS),
                        ("E_S2P", TAB_DECAPS), ("W_S2P", TAB_DECAPS), ("I_DUMMY", TAB_DECAPS),
                        ("E_SWEEP", TAB_SWEEP), ("W_SWEEP", TAB_SWEEP)):
        if code.startswith(prefix):
            return tab
    return None


class OverviewPanel(QWidget):
    """Combined plot (one curve per PWR) with per-curve visibility check boxes."""

    def __init__(self, plot: ImpedancePlot, parent: QWidget | None = None):
        super().__init__(parent)
        self.plot = plot
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.checks_row = QHBoxLayout()
        self.checks_row.addWidget(QLabel("Curves:", self))
        self.checks_row.addStretch(1)
        layout.addLayout(self.checks_row)
        layout.addWidget(plot, 1)
        self.checks: dict[str, QCheckBox] = {}

    def set_names(self, names: Sequence[str]) -> None:
        for box in self.checks.values():
            self.checks_row.removeWidget(box)
            box.deleteLater()
        self.checks.clear()
        for i, name in enumerate(names):
            box = QCheckBox(name, self)
            box.setChecked(True)
            box.setStyleSheet(f"QCheckBox {{ color: {series_color(i)}; font-weight: bold; }}")
            box.toggled.connect(lambda on, n=name: self.plot.set_curve_visible(n, on))
            self.checks_row.insertWidget(self.checks_row.count() - 1, box)
            self.checks[name] = box


class MainWindow(QMainWindow):
    """Application main window."""

    computeFinished = Signal()
    computeStateChanged = Signal(bool)

    def __init__(self, appdata: str | None = None, *, restore: bool = True,
                 autosave: bool = True, engine: Any = None, use_settings: bool = True,
                 auto_compute: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MainWindow")
        self.setMinimumSize(1100, 700)
        self.bridge = engine if engine is not None else EngineBridge()
        self.project = Project()
        self.project_path: str | None = None
        self.modified = False
        self.recent_files: list[str] = []
        self.results: list[Any] = []
        self.result_issues: list[Issue] = []
        self.stale = False
        self.last_compute_s: float | None = None
        self._thread: QThread | None = None
        self._worker: ComputeWorker | None = None
        self._pending_views: dict[str, PlotView] = {}
        self._csv_per_pwr = False
        self._touchstone_combined = False
        self._touchstone_options = TouchstoneOptions()
        self._plot_images_options = PlotImagesOptions()
        self._pending_result_tab: str | None = None
        self._had_results_restored = False
        self._use_settings = use_settings
        self._auto_compute = auto_compute
        self._applying = 0
        self._edited_during_compute = False
        self.help_window: HelpWindow | None = None
        self.plots: dict[str, ImpedancePlot] = {}

        self._build_models()
        self._build_ui()
        self._build_actions()
        self._build_menus()
        self._load_project_into_ui()

        self.autosave: AutosaveManager | None = None
        self.appdata_dir = appdata
        if autosave:
            directory = ensure_dir(appdata or appdata_dir())
            self.appdata_dir = directory
            self.autosave = AutosaveManager(AutosaveStore(directory), self)
            self.autosave.status.connect(lambda text: self.statusBar().showMessage(text, 8000))
            if not self.autosave.acquire_lock():
                self.banner.setText("Another Simple PI Calculator window owns the auto-save. "
                                    "Changes in this window are not auto-saved — use File ▸ "
                                    "Save As.")
                self.banner.setVisible(True)
            elif restore:
                self._restore_autosave()
        if self.autosave is None or not restore or not self.autosave.enabled:
            self._restore_settings_geometry()
        self._connect_change_signals()
        self._update_title()
        self._update_actions()

    # =========================================================================================
    # construction
    # =========================================================================================
    def _build_models(self) -> None:
        self.stackup_model = StackupTableModel(self.project.layers, self)
        self.pwr_model = PwrTableModel(self.project, self.bridge, self)
        self.decap_model = DecapTableModel(self.project, self._path_context, self)

    def _path_context(self) -> tuple[str | None, str | None]:
        project_dir = os.path.dirname(self.project_path) if self.project_path else None
        return project_dir, self.project.advanced.model_search_dir

    def _start_dir(self) -> str:
        if self.project_path:
            return os.path.dirname(self.project_path)
        if self.recent_files:
            return os.path.dirname(self.recent_files[0])
        return examples_dir() or os.path.expanduser("~")

    def _build_ui(self) -> None:
        central = QWidget(self)
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(4, 4, 4, 4)
        self.banner = QLabel("", central)
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("QLabel { background-color: #fff3a0; color: #333333; "
                                  "padding: 6px; border: 1px solid #d8c65a; }")
        self.banner.setVisible(False)
        vbox.addWidget(self.banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.splitter.setObjectName("MainSplitter")
        # left: inputs
        self.input_tabs = QTabWidget(self.splitter)
        self.stackup_panel = StackupPanel(self.stackup_model, self.input_tabs)
        self.via_panel = ViaPanel(self.input_tabs)
        self.pwr_panel = PwrPanel(self.pwr_model, self.input_tabs)
        self.decap_panel = DecapPanel(self.decap_model, self.pwr_model.names, self._start_dir,
                                      self.input_tabs)
        self.sweep_panel = SweepPanel(self.input_tabs)
        self.input_tabs.addTab(self.stackup_panel, "Stack-up")
        self.input_tabs.addTab(self.via_panel, "Vias")
        self.input_tabs.addTab(self.pwr_panel, "PWR Nets")
        self.input_tabs.addTab(self.decap_panel, "Decaps")
        self.input_tabs.addTab(self.sweep_panel, "Sweep")

        # right: results
        right = QWidget(self.splitter)
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        self.results_toolbar = QToolBar("Results", right)
        self.results_toolbar.setObjectName("ResultsToolbar")
        self.results_toolbar.setMovable(False)
        self.results_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        rbox.addWidget(self.results_toolbar)
        result_splitter = QSplitter(Qt.Orientation.Vertical, right)
        self.result_tabs = QTabWidget(result_splitter)
        self.overview_plot = ImpedancePlot(title="All PWRs", unit=self.project.display.z_unit,
                                           marker_texts=False)
        self.overview = OverviewPanel(self.overview_plot)
        self.result_tabs.addTab(self.overview, OVERVIEW_TAB)
        self.readout_table = QTableWidget(0, len(MARKER_FREQUENCIES_HZ), result_splitter)
        self.readout_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.readout_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.readout_table.setMinimumHeight(80)
        result_splitter.addWidget(self.result_tabs)
        result_splitter.addWidget(self.readout_table)
        result_splitter.setSizes([520, 130])
        rbox.addWidget(result_splitter)
        self.splitter.addWidget(self.input_tabs)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 6)
        self.splitter.setSizes([440, 660])
        vbox.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        self.message_dock = MessageDock(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.message_dock)
        self.message_dock.issueActivated.connect(self.navigate_to_issue)
        self.resizeDocks([self.message_dock], [150], Qt.Orientation.Vertical)

        bar = self.statusBar()
        self.progress_bar = QProgressBar(bar)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setMaximumWidth(220)
        self.progress_bar.setVisible(False)
        self.time_label = QLabel("", bar)
        self.modes_label = QLabel("", bar)
        bar.addPermanentWidget(self.modes_label)
        bar.addPermanentWidget(self.time_label)
        bar.addPermanentWidget(self.progress_bar)
        self._update_readout_table()

    def _build_actions(self) -> None:
        A = QAction
        self.act_new = A("&New", self, shortcut=QKeySequence.StandardKey.New,
                         triggered=lambda: self.new_project())
        self.act_open = A("&Open…", self, shortcut=QKeySequence.StandardKey.Open,
                          triggered=lambda: self.open_project())
        self.act_save = A("&Save", self, shortcut=QKeySequence.StandardKey.Save,
                          triggered=lambda: self.save_project())
        self.act_save_as = A("Save &As…", self, shortcut=QKeySequence("Ctrl+Shift+S"),
                             triggered=lambda: self.save_project_as())
        self.act_open_example = A("Open &Example Project", self,
                                  triggered=self.open_example_project)
        self.act_import_stackup = A("&Stack-up…", self, triggered=lambda: self.import_stackup())
        self.act_import_pwr = A("&PWR List…", self, triggered=lambda: self.import_pwr_list())
        self.act_import_decap = A("&Decap List…", self,
                                  triggered=lambda: self.import_decap_list())
        self.act_export_csv = A("Results &CSV…", self, triggered=lambda: self.export_csv())
        self.act_export_xlsx = A("Results &XLSX…", self, triggered=lambda: self.export_xlsx())
        self.act_export_png = A("Plot &PNG…", self, triggered=lambda: self.export_png())
        self.act_export_touchstone = A("&Touchstone…", self,
                                       triggered=lambda: self.export_touchstone())
        self.act_export_all_plots = A("&All Plots…", self,
                                      triggered=lambda: self.export_all_plots())
        self.act_export_all_plots.setToolTip("Export all plots…")
        self.act_autosave_folder = A("Show Auto-save &Folder", self,
                                     triggered=self.show_autosave_folder)
        self.act_exit = A("E&xit", self, shortcut=QKeySequence.StandardKey.Quit,
                          triggered=self.close)
        self.act_clear_recent = A("Clear Recent Files", self, triggered=self.clear_recent_files)

        self.act_add_row = A("&Add Row", self, shortcut=QKeySequence("Ctrl+Shift+N"),
                             triggered=self.add_row)
        self.act_duplicate_row = A("&Duplicate Row", self, shortcut=QKeySequence("Ctrl+Shift+D"),
                                   triggered=self.duplicate_row)
        self.act_remove_rows = A("&Remove Selected Rows", self,
                                 shortcut=QKeySequence("Ctrl+Del"),
                                 triggered=self.remove_rows)
        self.act_clear_messages = A("Clear &Messages", self,
                                    triggered=self.message_dock.clear)

        self.act_run = A("&Run", self, shortcut=QKeySequence("F5"),
                         triggered=lambda: self.start_compute())
        self.act_cancel = A("&Cancel", self, shortcut=QKeySequence("Esc"),
                            triggered=self.cancel_compute)

        self.unit_group = QActionGroup(self)
        self.unit_group.setExclusive(True)
        self.unit_actions: dict[str, QAction] = {}
        for key in ("ohm", "mohm", "uohm"):
            act = A(z_label(key), self, checkable=True)
            act.setData(key)
            act.triggered.connect(lambda _c=False, k=key: self.set_z_unit(k))
            self.unit_group.addAction(act)
            self.unit_actions[key] = act
        self.act_plane_only = A("Show &Plane-only Curve", self, checkable=True)
        self.act_plane_only.toggled.connect(self._on_plane_only_action)
        self.act_markers = A("Show &Markers", self, checkable=True, checked=True)
        self.act_markers.toggled.connect(self.set_markers_visible)
        self.act_reset_view = A("&Reset View", self, triggered=lambda: self.reset_view())
        self.act_reset_view.setShortcuts([QKeySequence(RESET_VIEW_SHORTCUT),
                                          QKeySequence("Ctrl+0")])
        self.act_reset_view.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_reset_view.setToolTip("Reset view: fit all visible curves, log–log axes "
                                       f"({RESET_VIEW_SHORTCUT})")
        self.act_reset_zoom = self.act_reset_view  # backwards-compatible name
        self.addAction(self.act_reset_view)
        self.act_messages = self.message_dock.toggleViewAction()
        self.act_messages.setText("Show &Messages Dock")

        self.act_help = A("&Help Contents", self, shortcut=QKeySequence.StandardKey.HelpContents,
                          triggered=lambda: self.show_help())
        self.act_help_browser = A("Open Help in &Browser", self,
                                  triggered=self.open_help_in_browser)
        self.act_examples_folder = A("Open &Examples Folder", self,
                                     triggered=self.open_examples_folder)
        self.act_about = A("&About", self, triggered=self.show_about)
        self.act_license = A("&License", self, triggered=self.show_license)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        for act in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            file_menu.addAction(act)
        self.recent_menu = file_menu.addMenu("Recent &Files")
        file_menu.addAction(self.act_open_example)
        file_menu.addSeparator()
        imp = file_menu.addMenu("&Import")
        for act in (self.act_import_stackup, self.act_import_pwr, self.act_import_decap):
            imp.addAction(act)
        exp = file_menu.addMenu("&Export")
        for act in self._export_actions():
            exp.addAction(act)
        self._build_results_toolbar()
        file_menu.addSeparator()
        file_menu.addAction(self.act_autosave_folder)
        file_menu.addSeparator()
        file_menu.addAction(self.act_exit)

        edit = mb.addMenu("&Edit")
        for act in (self.act_add_row, self.act_duplicate_row, self.act_remove_rows):
            edit.addAction(act)
        edit.addSeparator()
        edit.addAction(self.act_clear_messages)

        comp = mb.addMenu("&Compute")
        comp.addAction(self.act_run)
        comp.addAction(self.act_cancel)

        view = mb.addMenu("&View")
        unit_menu = view.addMenu("|Z| &Unit")
        for act in self.unit_actions.values():
            unit_menu.addAction(act)
        view.addAction(self.act_plane_only)
        view.addAction(self.act_markers)
        view.addAction(self.act_reset_view)
        view.addSeparator()
        view.addAction(self.act_messages)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self.act_help)
        pages = help_menu.addMenu("&Pages")
        self.help_page_actions: dict[str, QAction] = {}
        for file_name, title in HELP_PAGES:
            act = pages.addAction(title)
            act.triggered.connect(lambda _c=False, f=file_name: self.show_help(f))
            self.help_page_actions[file_name] = act
        help_menu.addAction(self.act_help_browser)
        help_menu.addAction(self.act_examples_folder)
        help_menu.addSeparator()
        help_menu.addAction(self.act_about)
        help_menu.addAction(self.act_license)
        self._rebuild_recent_menu()

    def _export_actions(self) -> list[QAction]:
        return [self.act_export_csv, self.act_export_xlsx, self.act_export_touchstone,
                self.act_export_png, self.act_export_all_plots]

    def _build_results_toolbar(self) -> None:
        bar = self.results_toolbar
        self.reset_view_button = QToolButton(bar)
        self.reset_view_button.setText("\u27f2 Reset view")
        self.reset_view_button.setToolTip(self.act_reset_view.toolTip())
        self.reset_view_button.clicked.connect(lambda _checked=False: self.reset_view())
        bar.addWidget(self.reset_view_button)
        bar.addSeparator()
        self.export_button = QToolButton(bar)
        self.export_button.setText("\u21a7 Export")
        self.export_button.setToolTip("Export results or plot images")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.export_button)
        for act in self._export_actions():
            menu.addAction(act)
        self.export_button.setMenu(menu)
        bar.addWidget(self.export_button)

    def _connect_change_signals(self) -> None:
        for model in (self.stackup_model, self.pwr_model, self.decap_model):
            model.edited.connect(self.on_inputs_changed)
        self.stackup_model.edited.connect(self._refresh_derived)
        self.decap_model.edited.connect(self._refresh_derived)
        self.pwr_model.edited.connect(self._on_pwr_list_changed)
        self.pwr_model.pwrRenamed.connect(self._on_pwr_renamed)
        self.via_panel.edited.connect(self.on_inputs_changed)
        self.via_panel.edited.connect(self._refresh_derived)
        self.sweep_panel.edited.connect(self.on_inputs_changed)
        self.sweep_panel.planeOnlyToggled.connect(self._sync_plane_only_action)
        self.stackup_panel.importRequested.connect(lambda: self.import_stackup())
        self.stackup_panel.reimportRequested.connect(self.reimport_stackup)
        self.pwr_panel.importRequested.connect(lambda: self.import_pwr_list())
        self.decap_panel.importRequested.connect(lambda: self.import_decap_list())
        self.pwr_panel.selectedPwrChanged.connect(self._on_selected_pwr)
        self.decap_panel.filterChanged.connect(lambda *_: self._schedule_autosave())
        self.input_tabs.currentChanged.connect(lambda *_: self._schedule_autosave())
        self.result_tabs.currentChanged.connect(self._on_result_tab_changed)
        self.splitter.splitterMoved.connect(lambda *_: self._schedule_autosave())
        self.message_dock.visibilityChanged.connect(lambda *_: self._schedule_autosave())
        self.overview_plot.viewChanged.connect(self._schedule_autosave)
        app = QGuiApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._flush_autosave)

    # =========================================================================================
    # state <-> UI
    # =========================================================================================
    def _load_project_into_ui(self) -> None:
        """Push ``self.project`` into all models and widgets (no change signals)."""
        self._applying += 1
        try:
            self.stackup_model.set_rows(self.project.layers)
            self.pwr_model.set_project(self.project)
            self.decap_model.set_project(self.project)
            self.via_panel.load(self.project)
            self.sweep_panel.load(self.project)
            self.stackup_panel.set_source_path(self.project.stackup_source_path)
            self.pwr_panel.source_label.setText(
                os.path.basename(self.project.pwr_source_path or ""))
            self.decap_panel.refresh_filter_items()
            unit = self.project.display.z_unit
            if unit in self.unit_actions:
                self.unit_actions[unit].setChecked(True)
            self.act_plane_only.blockSignals(True)
            self.act_plane_only.setChecked(bool(self.project.sweep.show_plane_only))
            self.act_plane_only.blockSignals(False)
            for plot in self._all_plots():
                plot.set_unit(unit)
                plot.set_plane_only_visible(bool(self.project.sweep.show_plane_only))
            if self.project.pwr_rows:
                self.pwr_panel.table.selectRow(0)
            self._refresh_derived()
        finally:
            self._applying -= 1

    def _set_project(self, project: Project, path: str | None, modified: bool) -> None:
        self.project = project
        self.project_path = os.path.abspath(path) if path else None
        self.modified = modified
        self.clear_results()
        self._pending_views.clear()
        self._load_project_into_ui()
        self._update_title()

    def _all_plots(self) -> list[ImpedancePlot]:
        return [self.overview_plot, *self.plots.values()]

    def _schedule_autosave(self) -> None:
        if self.autosave is not None and not self._applying:
            self.autosave.schedule()

    def _flush_autosave(self) -> None:
        if self.autosave is not None:
            self.autosave.flush()

    def on_inputs_changed(self) -> None:
        if self._applying:
            return
        self.modified = True
        if self.is_computing():
            # the running computation uses a snapshot of the old inputs (§5.4)
            self._edited_during_compute = True
        if self.results and not self.stale:
            self.stale = True
            for plot in self._all_plots():
                plot.set_stale(True)
        self._update_title()
        self._schedule_autosave()

    def _refresh_derived(self) -> None:
        self.pwr_model.refresh_derived()
        self.decap_model.refresh()
        self.stackup_panel.preview.update()
        self._on_selected_pwr(self.pwr_panel.selected_pwr())

    def _on_pwr_list_changed(self) -> None:
        self.decap_panel.refresh_filter_items()
        self.decap_model.refresh()
        self._on_selected_pwr(self.pwr_panel.selected_pwr())

    def _on_pwr_renamed(self, old: str, new: str) -> None:
        self.decap_model.rename_pwr(old, new)
        if self.decap_panel.current_filter() == old:
            self.decap_panel.proxy.set_pwr_filter(new)
        self.decap_panel.refresh_filter_items()

    def _pwr_row(self, name: str | None):
        for r in self.project.pwr_rows:
            if r.name == name:
                return r
        return None

    def _on_selected_pwr(self, name: str | None) -> None:
        row = self._pwr_row(name)
        if row is None:
            self.pwr_panel.preview.set_placement(None, "", "Select a PWR net to preview its "
                                                 "plane and ports.")
            self.stackup_panel.preview.set_pair(None)
            self.via_panel.set_derived(None, None, None, None)
            return
        placement = self.bridge.placement(self.project, row)
        self.pwr_panel.preview.set_placement(placement, row.name)
        self.stackup_panel.preview.set_pair((row.pwr_layer, row.gnd_layer))
        summary = self.bridge.via_summary(self.project, row)
        try:
            w_pad, w_dec = self.bridge.port_widths_m(self.project)
            w_pad_mm, w_dec_mm = w_pad / MM, w_dec / MM
        except Exception:  # noqa: BLE001
            w_pad_mm = w_dec_mm = None
        self.via_panel.set_derived(summary.get("h_near_mm"), summary.get("l_loop_nh"),
                                   w_pad_mm, w_dec_mm)
        if not self._applying and self.decap_panel.current_filter() != name:
            self.decap_panel.set_filter(name)

    def _update_title(self) -> None:
        name = project_stem(self.project_path) if self.project_path else "Untitled"
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — {name}[*]")
        self.setWindowModified(self.modified)

    def _update_actions(self) -> None:
        running = self.is_computing()
        self.act_run.setEnabled(not running)
        self.act_cancel.setEnabled(running)
        has = bool(self.results)
        self.act_export_csv.setEnabled(has)
        self.act_export_xlsx.setEnabled(has)
        self.act_export_png.setEnabled(has)
        self.act_export_touchstone.setEnabled(has)
        self.act_export_all_plots.setEnabled(has)
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(has)

    # =========================================================================================
    # session (auto-save)
    # =========================================================================================
    def collect_session(self) -> Session:
        window = WindowState(
            geometry_b64=b64_from_qbytearray(self.saveGeometry()),
            state_b64=b64_from_qbytearray(self.saveState()),
            splitter_sizes=[int(s) for s in self.splitter.sizes()],
            input_tab=int(self.input_tabs.currentIndex()),
            result_tab=self.current_result_name(),
            decap_filter=self.decap_panel.current_filter(),
            message_dock_visible=not self.message_dock.isHidden(),
        )
        plots = dict(self._pending_views)
        for name, plot in self.plots.items():
            plots[name] = plot.view_state()
        return Session(project_path=self.project_path, modified=bool(self.modified),
                       recent_files=list(self.recent_files[:MAX_RECENT_FILES]), window=window,
                       plots=plots, had_results=bool(self.results) or (
                           self._had_results_restored and self.is_computing()))

    def apply_session(self, session: Session) -> None:
        self._applying += 1
        try:
            w = session.window
            if w.geometry_b64:
                self.restoreGeometry(qbytearray_from_b64(w.geometry_b64))
                self._ensure_on_screen()
            if w.state_b64:
                self.restoreState(qbytearray_from_b64(w.state_b64))
            if len(w.splitter_sizes) == 2 and all(s >= 0 for s in w.splitter_sizes):
                self.splitter.setSizes(list(w.splitter_sizes))
            if 0 <= w.input_tab < self.input_tabs.count():
                self.input_tabs.setCurrentIndex(w.input_tab)
            self.decap_panel.set_filter(w.decap_filter)
            self.message_dock.setVisible(bool(w.message_dock_visible))
            self.recent_files = list(session.recent_files[:MAX_RECENT_FILES])
            self._rebuild_recent_menu()
            self.project_path = session.project_path
            self.modified = bool(session.modified)
            self._pending_views = dict(session.plots)
            self._pending_result_tab = w.result_tab
            self._had_results_restored = bool(session.had_results)
            self._update_title()
        finally:
            self._applying -= 1

    def _ensure_on_screen(self) -> None:
        frame = self.frameGeometry()
        screens = QGuiApplication.screens()
        if not screens:
            return
        if not any(s.availableGeometry().intersects(frame) for s in screens):
            primary = QGuiApplication.primaryScreen() or screens[0]
            center = primary.availableGeometry().center()
            frame.moveCenter(center)
            self.move(frame.topLeft())

    def _restore_autosave(self) -> None:
        assert self.autosave is not None
        result = self.autosave.store.load()
        with self.autosave.suspend():
            self._applying += 1
            try:
                self.project = result.project
                self.project_path = None
                self._load_project_into_ui()
                self.autosave.apply_session(result.session)
                # project_path affects relative model resolution: refresh derived columns
                self._refresh_derived()
            finally:
                self._applying -= 1
        if result.issues:
            self.message_dock.set_issues("autosave", result.issues)
            warn = [i for i in result.issues if i.severity is not Severity.INFO]
            if warn:
                self.statusBar().showMessage(warn[0].message, 15000)
        if self._had_results_restored and self._auto_compute:
            QTimer.singleShot(0, self._auto_compute_after_restore)

    def _auto_compute_after_restore(self) -> None:
        if self.is_computing():
            return
        self.start_compute(auto=True)

    def _restore_settings_geometry(self) -> None:
        if not self._use_settings:
            return
        try:
            settings = app_settings(self.appdata_dir if self.autosave else None)
            geom = settings.value("window/geometry")
            if geom:
                self.restoreGeometry(geom)
                self._ensure_on_screen()
            state = settings.value("window/splitter")
            if state:
                self.splitter.restoreState(state)
            markers = settings.value("view/show_markers")
            if markers is not None:
                self.act_markers.setChecked(str(markers).lower() in ("true", "1"))
        except Exception:  # noqa: BLE001 - settings are a convenience only
            log.debug("could not restore QSettings", exc_info=True)

    def _save_settings(self) -> None:
        if not self._use_settings:
            return
        try:
            settings = app_settings(self.appdata_dir if self.autosave else None)
            settings.setValue("window/geometry", self.saveGeometry())
            settings.setValue("window/splitter", self.splitter.saveState())
            settings.setValue("view/show_markers", self.act_markers.isChecked())
            settings.sync()
        except Exception:  # noqa: BLE001
            log.debug("could not write QSettings", exc_info=True)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._schedule_autosave()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_autosave()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.is_computing():
            self.cancel_compute()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(5000)
        self._flush_autosave()
        self._save_settings()
        if self.autosave is not None:
            self.autosave.release_lock()
        if self.help_window is not None:
            self.help_window.close()
        event.accept()

    # =========================================================================================
    # recent files
    # =========================================================================================
    def push_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        key = _norm(path)
        self.recent_files = [path] + [p for p in self.recent_files if _norm(p) != key]
        self.recent_files = self.recent_files[:MAX_RECENT_FILES]
        self._rebuild_recent_menu()
        self._schedule_autosave()

    def clear_recent_files(self) -> None:
        self.recent_files = []
        self._rebuild_recent_menu()
        self._schedule_autosave()

    def _rebuild_recent_menu(self) -> None:
        menu = self.recent_menu
        menu.clear()
        for i, path in enumerate(self.recent_files):
            act = menu.addAction(f"&{i + 1} {os.path.basename(path)}")
            act.setToolTip(path)
            act.setStatusTip(path)
            act.setData(path)
            act.setEnabled(os.path.isfile(path))
            act.triggered.connect(lambda _c=False, p=path: self.open_recent(p))
        if self.recent_files:
            menu.addSeparator()
        menu.addAction(self.act_clear_recent)
        self.act_clear_recent.setEnabled(bool(self.recent_files))

    def open_recent(self, path: str) -> bool:
        if not os.path.isfile(path):
            QMessageBox.information(self, "Recent Files", f"The file no longer exists:\n{path}")
            self.recent_files = [p for p in self.recent_files if _norm(p) != _norm(path)]
            self._rebuild_recent_menu()
            self._schedule_autosave()
            return False
        return self.open_project(path)

    # =========================================================================================
    # File menu (§5.8.5)
    # =========================================================================================
    def _ask_save_changes(self) -> str:
        """"save" | "discard" | "cancel"."""
        name = project_stem(self.project_path) if self.project_path else "Untitled"
        answer = QMessageBox.question(
            self, APP_DISPLAY_NAME, f"Save changes to {name}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return "save"
        if answer == QMessageBox.StandardButton.Discard:
            return "discard"
        return "cancel"

    def _maybe_save(self) -> bool:
        needs = (self.modified and self.project_path is not None) or (
            self.project_path is None and self.project.has_table_rows() and self.modified)
        if not needs:
            return True
        choice = self._ask_save_changes()
        if choice == "cancel":
            return False
        if choice == "save":
            return self.save_project()
        return True

    def new_project(self, confirm: bool = True) -> bool:
        if confirm and not self._maybe_save():
            return False
        self._flush_autosave()
        self.cancel_compute()
        self._set_project(Project(), None, False)
        self.message_dock.clear()
        self._flush_autosave()
        return True

    def open_project(self, path: str | None = None, confirm: bool = True) -> bool:
        if confirm and not self._maybe_save():
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open Project", self._start_dir(),
                                                  PROJECT_FILTER)
            if not path:
                return False
        self._flush_autosave()
        try:
            project, issues = load_project(path)
        except (ProjectFormatError, ProjectTooNewError) as exc:
            self.message_dock.set_issues("project", [exc.issue])
            self._error_box("Open Project", str(exc))
            return False
        except OSError as exc:
            self._error_box("Open Project", f"Cannot read {path}:\n{exc}")
            return False
        self.cancel_compute()
        self._set_project(project, path, project.migrated_from is not None)
        self.message_dock.clear()
        self.message_dock.set_issues("project", issues)
        self.push_recent(path)
        self.statusBar().showMessage(f"Opened {path}", 5000)
        self._flush_autosave()
        return True

    def open_example_project(self) -> bool:
        folder = examples_dir()
        if folder is None:
            self._error_box("Open Example Project", "The examples folder was not found.")
            return False
        return self.open_project(os.path.join(folder, "example_project.spical.json"))

    def save_project(self) -> bool:
        if self.project_path is None:
            return self.save_project_as()
        return self._save_to(self.project_path)

    def save_project_as(self, path: str | None = None) -> bool:
        if path is None:
            start = self.project_path or os.path.join(self._start_dir(), "untitled.spical.json")
            path, _ = QFileDialog.getSaveFileName(self, "Save Project As", start,
                                                  PROJECT_FILTER)
            if not path:
                return False
        return self._save_to(ensure_project_suffix(path))

    def _save_to(self, path: str) -> bool:
        path = os.path.abspath(path)
        try:
            save_project(self.project, path)
        except OSError as exc:
            self._error_box("Save Project", f"Cannot write {path}:\n{exc}")
            return False
        self.project_path = path
        self.modified = False
        self._update_title()
        self.push_recent(path)
        self.decap_model.refresh()
        self.statusBar().showMessage(f"Saved {path}", 5000)
        self._flush_autosave()
        return True

    def _error_box(self, title: str, text: str) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            self.statusBar().showMessage(f"{title}: {text}", 10000)
            log.error("%s: %s", title, text)
            return
        QMessageBox.critical(self, title, text)

    # -- import -----------------------------------------------------------------------------------
    def _ask_excel(self, title: str, current: str | None) -> str | None:
        start = os.path.dirname(current) if current else self._start_dir()
        path, _ = QFileDialog.getOpenFileName(self, title, start, EXCEL_FILTER)
        return path or None

    def _report_import(self, title: str, issues: IssueCollector, ok: bool) -> None:
        self.message_dock.set_issues("import", issues.issues)
        if issues.issues:
            self.message_dock.show()
        n_err = len(issues.errors)
        self.statusBar().showMessage(
            f"{title}: {'imported' if ok else 'failed'}"
            + (f" ({n_err} error(s), {len(issues.warnings)} warning(s))" if issues.issues
               else ""), 8000)

    def import_stackup(self, path: str | None = None) -> bool:
        path = path or self._ask_excel("Import Stack-up", self.project.stackup_source_path)
        if not path:
            return False
        from simple_pi_calculator.io.excel_import import read_stackup
        issues = IssueCollector()
        try:
            stackup = read_stackup(path, issues)
        except InputError:
            self._report_import("Stack-up import", issues, False)
            return False
        except Exception as exc:  # noqa: BLE001
            issues.error("E_XL_READ", f"Cannot read {path}: {exc}", path)
            self._report_import("Stack-up import", issues, False)
            return False
        self.project.layers[:] = rows_from_stackup(stackup)
        self.project.stackup_source_path = os.path.abspath(path)
        self.stackup_model.set_rows(self.project.layers)
        self.stackup_panel.set_source_path(self.project.stackup_source_path)
        self._report_import("Stack-up import", issues, True)
        self._refresh_derived()
        self.on_inputs_changed()
        return True

    def reimport_stackup(self) -> bool:
        if self.project.stackup_source_path:
            return self.import_stackup(self.project.stackup_source_path)
        return False

    def import_pwr_list(self, path: str | None = None) -> bool:
        path = path or self._ask_excel("Import PWR List", self.project.pwr_source_path)
        if not path:
            return False
        from simple_pi_calculator.io.excel_import import read_pwr_list
        issues = IssueCollector()
        try:
            rows = read_pwr_list(path, issues)
        except InputError:
            self._report_import("PWR list import", issues, False)
            return False
        except Exception as exc:  # noqa: BLE001
            issues.error("E_XL_READ", f"Cannot read {path}: {exc}", path)
            self._report_import("PWR list import", issues, False)
            return False
        self.pwr_model.beginResetModel()
        self.project.pwr_rows[:] = rows
        self.project.pwr_source_path = os.path.abspath(path)
        self.pwr_model.refresh_derived(emit=False)
        self.pwr_model.endResetModel()
        self.pwr_panel.source_label.setText(os.path.basename(path))
        if rows:
            self.pwr_panel.table.selectRow(0)
        self._report_import("PWR list import", issues, True)
        self._on_pwr_list_changed()
        self.on_inputs_changed()
        return True

    def import_decap_list(self, path: str | None = None) -> bool:
        path = path or self._ask_excel("Import Decap List", self.project.decap_source_path)
        if not path:
            return False
        from simple_pi_calculator.io.excel_import import read_decap_list
        issues = IssueCollector()
        try:
            rows = read_decap_list(path, issues)
        except InputError:
            self._report_import("Decap list import", issues, False)
            return False
        except Exception as exc:  # noqa: BLE001
            issues.error("E_XL_READ", f"Cannot read {path}: {exc}", path)
            self._report_import("Decap list import", issues, False)
            return False
        self.decap_model.beginResetModel()
        self.project.decap_rows[:] = rows
        self.project.decap_source_path = os.path.abspath(path)
        self.decap_model.endResetModel()
        self._report_import("Decap list import", issues, True)
        self._refresh_derived()
        self.on_inputs_changed()
        return True

    # -- export -----------------------------------------------------------------------------------
    def _export_stem(self) -> str:
        return project_stem(self.project_path) if self.project_path else "results"

    def _export_done(self, title: str, paths: Sequence[str]) -> None:
        """Info line per export in the Messages dock (category ``export``)."""
        if not paths:
            return
        text = paths[0] if len(paths) == 1 else f"{len(paths)} files: " + "; ".join(paths)
        self.message_dock.add_issues("export", [Issue("I_EXPORT", Severity.INFO,
                                                      f"{title}: exported {text}", "Export")])
        self.statusBar().showMessage(f"{title}: exported {len(paths)} file(s)", 8000)

    def _export_failed(self, title: str, exc: BaseException) -> None:
        log.error("%s failed", title, exc_info=exc)
        self.message_dock.add_issues("export", [Issue("E_EXPORT", Severity.ERROR,
                                                      f"{title} failed: {exc}", "Export")])
        self.message_dock.show()
        self.statusBar().showMessage(f"{title} failed: {exc}", 10000)

    def export_csv(self, target: str | None = None,
                   one_file_per_pwr: bool | None = None) -> list[str]:
        """CSV export. ``target`` is a folder (one file per PWR) or a file path (one file).
        Without ``target`` the options and file dialogs are shown. A ``target`` without
        ``one_file_per_pwr`` keeps the historic one-file-per-PWR behaviour."""
        if not self._exportable_results():
            return []
        if target is None:
            dialog = CsvExportDialog(self, self._csv_per_pwr)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return []
            one_file_per_pwr = dialog.one_file_per_pwr()
            self._csv_per_pwr = one_file_per_pwr
            if one_file_per_pwr:
                target = QFileDialog.getExistingDirectory(self, "Export Results CSV (folder)",
                                                          self._start_dir())
            else:
                start = os.path.join(self._start_dir(), f"{self._export_stem()}.csv")
                target, _ = QFileDialog.getSaveFileName(self, "Export Results CSV", start,
                                                        "CSV files (*.csv)")
            if not target:
                return []
        elif one_file_per_pwr is None:
            one_file_per_pwr = True
        from simple_pi_calculator.io.export import export_csv, export_csv_combined
        try:
            if one_file_per_pwr:
                written = export_csv(self._exportable_results(), target, self._export_stem())
            else:
                if not target.lower().endswith(".csv"):
                    target += ".csv"
                written = [export_csv_combined(self._exportable_results(), target,
                                               self._export_stem())]
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            self._export_failed("Export CSV", exc)
            return []
        self._export_done("Export CSV", written)
        return written

    def export_xlsx(self, path: str | None = None) -> str | None:
        if not self._exportable_results():
            return None
        if path is None:
            start = os.path.join(self._start_dir(), f"{self._export_stem()}.xlsx")
            path, _ = QFileDialog.getSaveFileName(self, "Export Results XLSX", start,
                                                  "Excel workbook (*.xlsx)")
            if not path:
                return None
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        from simple_pi_calculator.io.export import export_xlsx
        try:
            export_xlsx(self._exportable_results(), path, self.project)
        except Exception as exc:  # noqa: BLE001
            self._export_failed("Export XLSX", exc)
            return None
        self._export_done("Export XLSX", [path])
        return path

    def export_touchstone(self, target: str | None = None, combined: bool | None = None,
                          options: TouchstoneOptions | None = None) -> list[str]:
        """Touchstone v1 export (§4.8): per-PWR ``.s1p`` into folder ``target``, or one
        uncoupled N-port ``.sNp`` at file path ``target`` (extension set automatically)."""
        results = self._exportable_results()
        if not results:
            return []
        if target is None:
            dialog = TouchstoneExportDialog(self, len(results), self._touchstone_options,
                                            self._touchstone_combined)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return []
            combined = dialog.is_combined()
            options = dialog.options()
            self._touchstone_combined = combined
            self._touchstone_options = options
            if combined:
                ext = f".s{len(results)}p"
                start = os.path.join(self._start_dir(), f"{self._export_stem()}{ext}")
                target, _ = QFileDialog.getSaveFileName(self, "Export Touchstone", start,
                                                        f"Touchstone {len(results)}-port "
                                                        f"(*{ext});;All files (*)")
            else:
                target = QFileDialog.getExistingDirectory(self, "Export Touchstone (folder)",
                                                          self._start_dir())
            if not target:
                return []
        combined = bool(combined)
        options = options or self._touchstone_options
        from simple_pi_calculator.io.export import (
            export_touchstone_combined,
            export_touchstone_per_pwr,
        )
        try:
            if combined:
                written = [export_touchstone_combined(results, target, self._export_stem(),
                                                      options, self.project)]
            else:
                written = export_touchstone_per_pwr(results, target, self._export_stem(),
                                                    options, self.project)
        except Exception as exc:  # noqa: BLE001
            self._export_failed("Export Touchstone", exc)
            return []
        self._export_done("Export Touchstone", written)
        return written

    def export_png(self, path: str | None = None) -> str | None:
        plot = self.current_plot()
        if plot is None or not self._exportable_results():
            return None
        if path is None:
            name = self.current_result_name() or "all"
            start = os.path.join(self._start_dir(), f"{self._export_stem()}_{name}.png")
            path, _ = QFileDialog.getSaveFileName(self, "Export Plot PNG", start,
                                                  "PNG image (*.png)")
            if not path:
                return None
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            plot.export_png(path)
        except Exception as exc:  # noqa: BLE001
            self._export_failed("Export PNG", exc)
            return None
        self._export_done("Export PNG", [path])
        return path

    def export_all_plots(self, folder: str | None = None, fmt: str | None = None,
                         width: int | None = None, height: int | None = None,
                         keep_zoom: bool | None = None) -> list[str]:
        """Save ``All_PWRs.<ext>`` plus one image per PWR tab (sanitised names) into ``folder``
        at ``width`` × ``height`` px (PNG or SVG). Without ``folder`` the options dialog is
        shown."""
        if not self._exportable_results():
            return []
        opts = self._plot_images_options
        if folder is None:
            if not opts.folder:
                opts.folder = self._start_dir()
            dialog = ExportPlotsDialog(self, opts)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return []
            opts = dialog.options()
            if not opts.folder:
                return []
            self._plot_images_options = opts
        else:
            opts = PlotImagesOptions(folder=folder, fmt=fmt or opts.fmt,
                                     width=int(width or opts.width),
                                     height=int(height or opts.height),
                                     keep_zoom=opts.keep_zoom if keep_zoom is None
                                     else bool(keep_zoom))
        ext = opts.fmt.lower().lstrip(".")
        written: list[str] = []
        used: set[str] = set()
        jobs = [(safe_file_name("All_PWRs", used), self.overview_plot)]
        for name, plot in self.plots.items():
            jobs.append((safe_file_name(name, used), plot))
        try:
            for stem, plot in jobs:
                path = os.path.join(opts.folder, f"{stem}.{ext}")
                written.append(plot.export_image(path, opts.width, opts.height, opts.keep_zoom))
        except Exception as exc:  # noqa: BLE001
            if written:
                self._export_done("Export all plots", written)
            self._export_failed("Export all plots", exc)
            return written
        self._export_done("Export all plots", written)
        return written

    def _exportable_results(self) -> list[Any]:
        return [r for r in self.results if getattr(r, "z_pad", None) is not None]

    def show_autosave_folder(self) -> None:
        folder = self.appdata_dir or appdata_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def open_examples_folder(self) -> None:
        folder = examples_dir()
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # =========================================================================================
    # Edit menu
    # =========================================================================================
    def add_row(self) -> None:
        tab = self.input_tabs.currentIndex()
        if tab == TAB_STACKUP:
            self.stackup_model.insert_row()
        elif tab == TAB_PWR:
            self.pwr_panel.add_button.click()
        elif tab == TAB_DECAPS:
            self.decap_panel.add_button.click()

    def duplicate_row(self) -> None:
        tab = self.input_tabs.currentIndex()
        if tab == TAB_PWR:
            self.pwr_panel.duplicate_button.click()
        elif tab == TAB_DECAPS:
            self.decap_panel.duplicate_button.click()

    def remove_rows(self) -> None:
        tab = self.input_tabs.currentIndex()
        if tab == TAB_STACKUP:
            self.stackup_panel.remove_button.click()
        elif tab == TAB_PWR:
            self.pwr_panel.remove_button.click()
        elif tab == TAB_DECAPS:
            self.decap_panel.remove_button.click()

    # =========================================================================================
    # View menu
    # =========================================================================================
    def set_z_unit(self, unit: str) -> None:
        if unit not in self.unit_actions:
            return
        self.unit_actions[unit].setChecked(True)
        if self.project.display.z_unit == unit and all(p.unit == unit
                                                        for p in self._all_plots()):
            return
        self.project.display.z_unit = unit
        for plot in self._all_plots():
            plot.set_unit(unit)
        self._update_readout_table()
        self._schedule_autosave()

    def _on_plane_only_action(self, checked: bool) -> None:
        if self.sweep_panel.plane_only.isChecked() != checked:
            self.sweep_panel.plane_only.setChecked(checked)  # emits edited → modified
        for plot in self._all_plots():
            plot.set_plane_only_visible(checked)
        if checked and self.results and all(getattr(r, "z_plane_only", None) is None
                                            for r in self.results):
            self.statusBar().showMessage("Run the computation again to get the plane-only "
                                         "curve.", 6000)

    def _sync_plane_only_action(self, checked: bool) -> None:
        if self.act_plane_only.isChecked() != checked:
            self.act_plane_only.setChecked(checked)

    def set_markers_visible(self, visible: bool) -> None:
        for plot in self._all_plots():
            plot.set_markers_visible(visible)

    def reset_view(self) -> None:
        """Default view of the visible plot (toolbar button, View ▸ Reset View, Ctrl+D)."""
        plot = self.current_plot()
        if plot is not None:
            plot.reset_view()

    def reset_zoom(self) -> None:
        """Backwards-compatible alias of :meth:`reset_view`."""
        self.reset_view()

    # =========================================================================================
    # Help menu
    # =========================================================================================
    def show_help(self, page: str | None = None) -> HelpWindow:
        if self.help_window is None:
            self.help_window = HelpWindow(None, help_dir())
        self.help_window.show_page(page or "index.html")
        self.help_window.show()
        self.help_window.raise_()
        return self.help_window

    def open_help_in_browser(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(help_dir(), "index.html")))

    def about_text(self) -> str:
        return (f"<h3>{APP_DISPLAY_NAME} {__version__}</h3>"
                "<p>Fast first-order PDN impedance calculator for PCB / MLO power planes with "
                "decoupling capacitors.</p>"
                "<p>Copyright (c) 2026 Simple PI Calculator contributors.<br>"
                "Released under the MIT License.</p>"
                "<p>Uses Qt / PySide6 (LGPLv3), pyqtgraph (MIT), numpy (BSD), openpyxl (MIT).</p>")

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_DISPLAY_NAME}", self.about_text())

    def show_license(self) -> None:
        self.show_help("about.html")

    # =========================================================================================
    # Compute (§5.4)
    # =========================================================================================
    def is_computing(self) -> bool:
        return self._thread is not None

    def start_compute(self, auto: bool = False) -> bool:
        if self.is_computing():
            return False
        self.message_dock.clear_category("compute")
        self.message_dock.clear_category("validation")
        try:
            inputs = self.bridge.make_inputs(self.project, self.project_path)
        except EngineUnavailableError as exc:
            self._compute_problem("E_ENGINE_UNAVAILABLE", str(exc))
            return False
        except InputError as exc:
            self.message_dock.set_issues("validation", exc.issues)
            self._focus_first_error(exc.issues)
            return False
        except Exception as exc:  # noqa: BLE001 - malformed inputs while editing
            self._compute_problem("E_INPUTS", f"Cannot prepare the inputs: {exc}")
            return False
        try:
            issues = list(self.bridge.validate(inputs))
        except InputError as exc:
            issues = list(exc.issues)
        except Exception as exc:  # noqa: BLE001
            self._compute_problem("E_VALIDATION", f"Validation failed: {exc}")
            return False
        self.message_dock.set_issues("validation", issues)
        errors = [i for i in issues if i.severity is Severity.ERROR]
        if errors:
            self.statusBar().showMessage(
                f"Cannot compute: {len(errors)} input error(s) — see Messages.", 10000)
            self.message_dock.show()
            if not auto:
                self._focus_first_error(errors)
            return False

        thread = QThread(self)
        worker = ComputeWorker(inputs, self.bridge.compute,
                               tuple(getattr(self.bridge, "cancelled_exceptions", ()) or ()))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.on_compute_progress)
        worker.finished.connect(self.on_compute_finished)
        worker.failed.connect(self.on_compute_failed)
        worker.cancelled.connect(self.on_compute_cancelled)
        for sig in (worker.finished, worker.failed, worker.cancelled):
            sig.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        self._edited_during_compute = False
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.statusBar().showMessage("Computing…")
        self._update_actions()
        self.computeStateChanged.emit(True)
        thread.start()
        return True

    def cancel_compute(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.statusBar().showMessage("Cancelling…")

    def _compute_problem(self, code: str, message: str) -> None:
        self.message_dock.set_issues("compute", [Issue(code, Severity.ERROR, message)])
        self.message_dock.show()
        self.statusBar().showMessage(message, 10000)

    def report_internal_error(self, summary: str, trace: str, log_path: str | None = None) -> None:
        """Non-modal report of an uncaught exception (installed by ``app.install_excepthook``)."""
        where = f" Details in the log file: {log_path}" if log_path else ""
        issue = Issue("E_INTERNAL", Severity.ERROR,
                      f"Unexpected internal error: {summary}. The action was not completed; "
                      f"your inputs are kept (auto-saved).{where}",
                      None, trace.strip().splitlines()[-1] if trace.strip() else None)
        self.message_dock.add_issues("internal", [issue])
        self.message_dock.show()
        self.statusBar().showMessage(f"Internal error: {summary}", 15000)

    def _focus_first_error(self, issues: Sequence[Issue]) -> None:
        for issue in issues:
            if issue.severity is Severity.ERROR:
                self.navigate_to_issue(issue)
                return

    def navigate_to_issue(self, issue: Issue) -> None:
        tab = tab_for_issue(issue)
        source = issue.source or ""
        if source.startswith("PWR:"):
            name = source[4:]
            if tab not in (TAB_DECAPS, TAB_VIAS, TAB_STACKUP):
                tab = TAB_PWR
            self.pwr_panel.select_pwr(name)
            if tab == TAB_DECAPS:
                self.decap_panel.set_filter(name)
        if tab is not None:
            self.input_tabs.setCurrentIndex(tab)

    def on_compute_progress(self, fraction: float, text: str) -> None:
        self.progress_bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        if text:
            self.statusBar().showMessage(f"Computing… {text}")

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.progress_bar.setVisible(False)
        self._update_actions()
        self.computeStateChanged.emit(False)

    def on_compute_finished(self, payload: tuple[list[Any], list[Issue]]) -> None:
        results, issues = payload
        worker = self._worker
        self.last_compute_s = worker.elapsed_s if worker is not None else None
        self.result_issues = list(issues)
        self.message_dock.set_issues("compute", issues)
        if any(i.severity is not Severity.INFO for i in issues):
            self.message_dock.show()
        self.show_results(results)
        if self._edited_during_compute and self.results:
            self.stale = True
            for plot in self._all_plots():
                plot.set_stale(True)
        if self.last_compute_s is not None:
            self.time_label.setText(f"Last compute: {self.last_compute_s:.2f} s")
        self.statusBar().showMessage(f"Computed {len(results)} PWR net(s).", 6000)
        self._had_results_restored = False
        self._flush_autosave()
        self.computeFinished.emit()

    def on_compute_failed(self, trace: str) -> None:
        last = trace.strip().splitlines()[-1] if trace.strip() else "unknown error"
        log.error("Computation failed:\n%s", trace)
        self.message_dock.set_issues("compute", [Issue("E_COMPUTE_FAILED", Severity.ERROR,
                                                       f"Computation failed: {last}",
                                                       None, None)])
        self.message_dock.show()
        self.statusBar().showMessage(f"Computation failed: {last}", 15000)
        self._had_results_restored = False
        self.computeFinished.emit()

    def on_compute_cancelled(self) -> None:
        self.message_dock.set_issues("compute", [Issue("I_COMPUTE_CANCELLED", Severity.INFO,
                                                       "Computation cancelled by the user.")])
        self.statusBar().showMessage("Computation cancelled.", 6000)
        self._had_results_restored = False
        self.computeFinished.emit()

    # =========================================================================================
    # results
    # =========================================================================================
    def clear_results(self) -> None:
        self.results = []
        self.stale = False
        self.result_tabs.blockSignals(True)
        try:
            while self.result_tabs.count() > 1:
                widget = self.result_tabs.widget(1)
                self.result_tabs.removeTab(1)
                widget.deleteLater()
        finally:
            self.result_tabs.blockSignals(False)
        self.plots.clear()
        self.overview_plot.clear_results()
        self.overview_plot.set_stale(False)
        self.overview.set_names([])
        self.modes_label.setText("")
        self._update_readout_table()
        self._update_actions()

    def show_results(self, results: Sequence[Any]) -> None:
        views = {name: plot.view_state() for name, plot in self.plots.items()}
        views.update(self._pending_views)
        current = self._pending_result_tab or self.current_result_name()
        self.clear_results()
        self.results = list(results)
        unit = self.project.display.z_unit
        plane = bool(self.project.sweep.show_plane_only)
        markers = self.act_markers.isChecked()
        good = [r for r in self.results if getattr(r, "z_pad", None) is not None]
        colors = [series_color(i) for i in range(len(good))]
        self.overview_plot.set_unit(unit)
        self.overview_plot.set_results(good, colors)
        self.overview_plot.set_plane_only_visible(plane)
        self.overview_plot.set_markers_visible(markers)
        self.overview.set_names([r.name for r in good])
        self.result_tabs.blockSignals(True)
        try:
            color_of = {r.name: c for r, c in zip(good, colors)}
            failed_names = self._failed_pwr_names()
            for res in self.results:
                if getattr(res, "z_pad", None) is None:
                    continue
                plot = ImpedancePlot(title=res.name, unit=unit)
                plot.set_results([res], [color_of[res.name]])
                plot.set_plane_only_visible(plane)
                plot.set_markers_visible(markers)
                if res.name in views:
                    plot.apply_view_state(views[res.name])
                plot.viewChanged.connect(self._schedule_autosave)
                self.plots[res.name] = plot
                i = self.result_tabs.addTab(plot, res.name)
                if any(iss.severity is Severity.ERROR for iss in getattr(res, "issues", [])):
                    self.result_tabs.setTabIcon(i, self.style().standardIcon(
                        self.style().StandardPixmap.SP_MessageBoxCritical))
            for name in failed_names:
                if name in self.plots:
                    continue
                label = QLabel(f"The computation of {name} failed — see Messages.")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                i = self.result_tabs.addTab(label, name)
                self.result_tabs.setTabIcon(i, self.style().standardIcon(
                    self.style().StandardPixmap.SP_MessageBoxCritical))
            self.select_result_tab(current)
        finally:
            self.result_tabs.blockSignals(False)
        self._pending_views.clear()
        self._pending_result_tab = None
        self._update_readout_table()
        self._update_modes_label()
        self._update_actions()

    def _failed_pwr_names(self) -> list[str]:
        ok = {r.name for r in self.results if getattr(r, "z_pad", None) is not None}
        names = []
        for issue in self.result_issues:
            src = issue.source or ""
            if issue.severity is Severity.ERROR and src.startswith("PWR:"):
                name = src[4:]
                if name not in ok and name not in names:
                    names.append(name)
        return names

    def select_result_tab(self, name: str | None) -> None:
        for i in range(self.result_tabs.count()):
            text = self.result_tabs.tabText(i)
            if (name is None and i == 0) or (name is not None and text == name):
                self.result_tabs.setCurrentIndex(i)
                return

    def current_result_name(self) -> str | None:
        i = self.result_tabs.currentIndex()
        if i <= 0:
            return None
        return self.result_tabs.tabText(i)

    def current_plot(self) -> ImpedancePlot | None:
        name = self.current_result_name()
        if name is None:
            return self.overview_plot
        return self.plots.get(name)

    def _on_result_tab_changed(self, *_args) -> None:
        self._update_modes_label()
        self._schedule_autosave()

    def _update_modes_label(self) -> None:
        name = self.current_result_name()
        res = next((r for r in self.results if r.name == name), None)
        info = getattr(res, "info", None) or {}
        if res is not None and "M" in info and "N" in info:
            text = f"{name}: modes {info['M']}×{info['N']}"
            if "P" in info:
                text += f", {info['P']} ports"
            self.modes_label.setText(text)
        else:
            self.modes_label.setText("")

    def readout_values(self) -> list[list[float | None]]:
        """|Z| in the current unit at each marker for each result row."""
        scale = z_scale(self.project.display.z_unit)
        rows: list[list[float | None]] = []
        for res in self._exportable_results():
            mf = [float(x) for x in (getattr(res, "marker_f_hz", None) if getattr(
                res, "marker_f_hz", None) is not None else [])]
            mz = list(getattr(res, "marker_z", None) if getattr(res, "marker_z", None)
                      is not None else [])
            row: list[float | None] = []
            for f in MARKER_FREQUENCIES_HZ:
                val = None
                for fk, zk in zip(mf, mz):
                    if math.isclose(fk, f, rel_tol=1e-9):
                        val = abs(complex(zk)) * scale
                        break
                row.append(val)
            rows.append(row)
        return rows

    def _update_readout_table(self) -> None:
        unit = self.project.display.z_unit
        table = self.readout_table
        headers = [f"|Z| @ {format_frequency(f, 3)} ({z_label(unit)})"
                   for f in MARKER_FREQUENCIES_HZ]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        results = self._exportable_results()
        values = self.readout_values()
        table.setRowCount(len(results))
        for r, (res, row) in enumerate(zip(results, values)):
            table.setVerticalHeaderItem(r, QTableWidgetItem(res.name))
            for c, val in enumerate(row):
                item = QTableWidgetItem("n/a" if val is None else format_sig(val, 4))
                item.setTextAlignment((Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter))
                if val is not None:
                    item.setData(Qt.ItemDataRole.UserRole, float(val))
                table.setItem(r, c, item)


__all__ = ["MainWindow", "examples_dir", "OVERVIEW_TAB"]
