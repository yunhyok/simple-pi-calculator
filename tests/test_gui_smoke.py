"""GUI smoke tests (DESIGN.md §8.12) — pytest-qt, ``QT_QPA_PLATFORM=offscreen``."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from simple_pi_calculator import __version__
from typing import Any

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402

from simple_pi_calculator.constants import MARKER_FREQUENCIES_HZ  # noqa: E402
from simple_pi_calculator.errors import Issue, Severity  # noqa: E402
from simple_pi_calculator.gui.engine_bridge import EngineBridge, cluster_port_width  # noqa: E402
from simple_pi_calculator.gui.help_window import HELP_PAGES, HelpWindow  # noqa: E402
from simple_pi_calculator.gui.main_window import OVERVIEW_TAB, MainWindow  # noqa: E402
from simple_pi_calculator.gui.models import DecapTableModel, PwrTableModel  # noqa: E402
from simple_pi_calculator.io.project_io import AutosaveStore  # noqa: E402

pytestmark = pytest.mark.gui

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "example_project.spical.json"
ENGINE_AVAILABLE = EngineBridge().available()


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
@dataclass
class FakeResult:
    name: str
    f_hz: np.ndarray
    z_pad: np.ndarray
    z_plane_only: np.ndarray | None = None
    marker_f_hz: np.ndarray = field(default_factory=lambda: np.asarray(MARKER_FREQUENCIES_HZ))
    marker_z: np.ndarray = field(default_factory=lambda: np.zeros(3, complex))
    info: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)


def fake_result(name: str, level_ohm: float = 1e-3, n: int = 200) -> FakeResult:
    f = np.logspace(5, 9, n)
    z = level_ohm * (1 + 1j * f / 1e7)
    mf = np.asarray(MARKER_FREQUENCIES_HZ)
    mz = level_ohm * (1 + 1j * mf / 1e7)
    return FakeResult(name, f, z, 2 * z, mf, mz, {"M": 10, "N": 5, "P": 3})


class FakeBridge(EngineBridge):
    """Real geometry, fake engine (inputs = the project itself)."""

    class CancelledError(Exception):
        pass

    def __init__(self, mode: str = "ok", delay_s: float = 0.0):
        super().__init__()
        self.mode = mode
        self.delay_s = delay_s
        self.started = threading.Event()

    def available(self) -> bool:
        return True

    @property
    def cancelled_exceptions(self):
        return (self.CancelledError,)

    def make_inputs(self, project, project_path):
        return {"pwrs": [r.name for r in project.pwr_rows if r.enabled]}

    def validate(self, inputs):
        if not inputs["pwrs"]:
            return [Issue("E_PWR_EMPTY", Severity.ERROR, "No PWR nets.")]
        return []

    def compute(self, inputs, progress, cancel):
        self.started.set()
        if self.mode == "fail":
            raise RuntimeError("boom from fake engine")
        end = time.monotonic() + self.delay_s
        while time.monotonic() < end:
            if cancel():
                raise self.CancelledError()
            progress(0.5, "working")
            time.sleep(0.01)
        progress(1.0, "done")
        return ([fake_result(n, 1e-3 * (i + 1)) for i, n in enumerate(inputs["pwrs"])],
                [Issue("W_FAKE", Severity.WARNING, "fake warning", "PWR:" + inputs["pwrs"][0])])


@pytest.fixture
def appdata(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "appdata"
    folder.mkdir()
    monkeypatch.setenv("SPICAL_APPDATA_DIR", str(folder))
    return folder


@pytest.fixture
def make_window(qtbot, appdata):
    created: list[MainWindow] = []

    def _make(**kwargs) -> MainWindow:
        kwargs.setdefault("auto_compute", True)
        w = MainWindow(**kwargs)
        qtbot.addWidget(w)
        w.show()
        created.append(w)
        return w

    yield _make
    for w in created:
        try:
            if w.is_computing():
                w.cancel_compute()
                qtbot.waitUntil(lambda w=w: not w.is_computing(), timeout=10000)
            w.close()
        except RuntimeError:
            pass


def pwr_row_index(window: MainWindow, name: str) -> int:
    return [r.name for r in window.project.pwr_rows].index(name)


def height_of(window: MainWindow, name: str) -> float:
    model = window.pwr_model
    idx = model.index(pwr_row_index(window, name), PwrTableModel.COL_HEIGHT)
    return float(model.data(idx, Qt.ItemDataRole.EditRole))


# ---------------------------------------------------------------------------------------------
# window, project loading, tables
# ---------------------------------------------------------------------------------------------
def test_window_opens_with_empty_appdata(make_window, appdata):
    w = make_window()
    assert w.windowTitle().startswith("Simple PI Calculator — Untitled")
    menus = [a.text().replace("&", "") for a in w.menuBar().actions()]
    assert menus == ["File", "Edit", "Compute", "View", "Help"]
    assert w.act_run.isEnabled() and not w.act_cancel.isEnabled()
    assert w.result_tabs.tabText(0) == OVERVIEW_TAB
    assert w.autosave is not None and w.autosave.enabled
    assert w.project.display.z_unit == "mohm"
    assert w.unit_actions["mohm"].isChecked()


def test_load_example_project_populates_tables(make_window):
    w = make_window()
    assert w.open_project(str(EXAMPLE))
    assert w.stackup_model.rowCount() == 11
    assert w.pwr_model.rowCount() == 2
    assert w.decap_model.rowCount() == 4
    # metal / dielectric detection
    col = w.stackup_model.COL_TYPE
    assert w.stackup_model.data(w.stackup_model.index(0, col)) == "Metal"
    assert w.stackup_model.data(w.stackup_model.index(1, col)) == "Dielectric"
    # derived heights (§2.5 worked example)
    assert height_of(w, "VDD_CORE") == pytest.approx(21.0)
    assert height_of(w, "VDD_IO") == pytest.approx(14.0)
    # dummy checkbox
    dummy = [w.decap_model.data(w.decap_model.index(r, DecapTableModel.COL_DUMMY),
                                Qt.ItemDataRole.CheckStateRole) for r in range(4)]
    assert dummy[2] == Qt.CheckState.Checked and dummy[0] == Qt.CheckState.Unchecked
    # model files resolved relative to the project
    assert all(w.decap_model.resolved_path(r) for r in w.project.decap_rows)
    # decap filter follows the selected PWR
    w.pwr_panel.select_pwr("VDD_IO")
    assert w.decap_panel.current_filter() == "VDD_IO"
    assert w.decap_panel.proxy.rowCount() == 2
    w.decap_panel.set_filter(None)
    assert w.decap_panel.proxy.rowCount() == 4
    assert w.windowTitle() == "Simple PI Calculator — example_project[*]"
    assert not w.isWindowModified()
    assert Path(w.recent_files[0]) == EXAMPLE


def test_height_column_updates_and_preview_paints(make_window, qtbot):
    w = make_window()
    w.open_project(str(EXAMPLE))
    row = next(i for i, r in enumerate(w.project.decap_rows)
               if r.pwr_name == "VDD_CORE" and r.distance_mm == 15.0)
    idx = w.decap_model.index(row, DecapTableModel.COL_DIST)
    assert w.decap_model.setData(idx, 20.0)
    assert height_of(w, "VDD_CORE") == pytest.approx(28.0)
    assert w.isWindowModified()
    w.input_tabs.setCurrentIndex(2)
    w.pwr_panel.select_pwr("VDD_CORE")
    preview = w.pwr_panel.preview
    assert preview.placement is not None
    assert preview.placement.n_decap_ports == 14
    before = preview.paint_count
    image = preview.grab()
    assert not image.isNull()
    assert preview.paint_count > before



def test_number_of_pads_column_reaches_preview_and_inputs(make_window):
    """PWR table '# PADs' column (schema 3): edit → derived placement with N_pad pads, header
    tooltip, validation E_PWR_NPADS, and the engine inputs carry n_pads."""
    w = make_window()
    w.open_project(str(EXAMPLE))
    model = w.pwr_model
    assert model.headerData(PwrTableModel.COL_NPADS, Qt.Orientation.Horizontal) == "# PADs"
    assert "Number of PADs" in model.headerData(PwrTableModel.COL_NPADS, Qt.Orientation.Horizontal,
                                                Qt.ItemDataRole.ToolTipRole)
    row = pwr_row_index(w, "VDD_CORE")
    idx = model.index(row, PwrTableModel.COL_NPADS)
    assert model.data(idx) == "1"
    assert model.setData(idx, 4)
    assert w.project.pwr_rows[row].n_pads == 4
    w.input_tabs.setCurrentIndex(2)
    w.pwr_panel.select_pwr("VDD_CORE")
    preview = w.pwr_panel.preview
    assert preview.placement is not None and preview.placement.n_pads == 4
    assert preview.placement.n_decap_ports == 14 and len(preview.placement.xy_m) == 18
    assert not preview.grab().isNull()
    inputs = w.bridge.make_inputs(w.project, None)
    assert {p.name: p.n_pads for p in inputs.pwrs} == {"VDD_CORE": 4, "VDD_IO": 1}
    w.project.pwr_rows[row].n_pads = 0
    model.refresh_derived()
    assert "E_PWR_NPADS" in model.data(idx, Qt.ItemDataRole.ToolTipRole)


# ---------------------------------------------------------------------------------------------
# plot, markers, unit switch (fake results, engine independent)
# ---------------------------------------------------------------------------------------------
def test_plot_markers_and_unit_switch(make_window):
    w = make_window()
    results = [fake_result("A", 1e-3), fake_result("B", 2e-3)]
    w.show_results(results)
    assert w.result_tabs.count() == 3
    plot = w.plots["A"]
    positions = sorted(line.value() for line in plot.marker_lines)
    assert positions == pytest.approx([math.log10(f) for f in MARKER_FREQUENCIES_HZ])
    assert all(line.isVisible() for line in plot.marker_lines)
    assert set(w.overview_plot.series_names()) == {"A", "B"}
    assert plot.axis_label_text() == "|Z| (mΩ)"
    y_mohm = np.array(plot.curve("A").yData, dtype=float)

    # readout table: 2 rows × 3 numeric columns in mΩ
    assert w.readout_table.rowCount() == 2 and w.readout_table.columnCount() == 3
    vals = w.readout_values()
    assert vals[0][0] == pytest.approx(abs(results[0].marker_z[0]) * 1e3)

    w.unit_actions["uohm"].trigger()
    assert w.project.display.z_unit == "uohm"
    assert plot.axis_label_text() == "|Z| (µΩ)"
    y_uohm = np.array(plot.curve("A").yData, dtype=float)
    assert np.allclose(y_uohm, y_mohm * 1e3)
    assert "µΩ" in w.readout_table.horizontalHeaderItem(0).text()
    assert w.readout_values()[0][0] == pytest.approx(abs(results[0].marker_z[0]) * 1e6)

    # markers toggle, plane-only toggle, curve visibility, hover text, reset zoom
    w.act_markers.setChecked(False)
    assert not any(line.isVisible() for line in plot.marker_lines)
    w.act_markers.setChecked(True)
    w.act_plane_only.setChecked(True)
    assert plot.plane_curve("A").isVisible()
    assert w.project.sweep.show_plane_only
    w.overview.checks["B"].setChecked(False)
    assert not w.overview_plot.is_curve_visible("B")
    assert "µΩ" in plot.hover_text(7.0)
    w.select_result_tab("A")
    w.reset_zoom()
    assert w.current_plot() is plot


# ---------------------------------------------------------------------------------------------
# worker: fake engine
# ---------------------------------------------------------------------------------------------
def test_worker_with_fake_engine(make_window, qtbot):
    bridge = FakeBridge(delay_s=0.1)
    w = make_window(engine=bridge)
    w.open_project(str(EXAMPLE))
    with qtbot.waitSignal(w.computeFinished, timeout=10000):
        assert w.start_compute()
        assert not w.act_run.isEnabled() and w.act_cancel.isEnabled()
        assert not w.start_compute()  # already running
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=5000)
    assert w.act_run.isEnabled()
    assert w.result_tabs.count() == 3
    assert "W_FAKE" in w.message_dock.codes()
    assert w.plots["VDD_CORE"].curve("VDD_CORE") is not None


def test_worker_failure_is_reported_not_raised(make_window, qtbot):
    w = make_window(engine=FakeBridge(mode="fail"))
    w.open_project(str(EXAMPLE))
    with qtbot.waitSignal(w.computeFinished, timeout=10000):
        w.start_compute()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=5000)
    assert "E_COMPUTE_FAILED" in w.message_dock.codes()
    assert w.act_run.isEnabled()


def test_worker_cancel(make_window, qtbot):
    bridge = FakeBridge(delay_s=30.0)
    w = make_window(engine=bridge)
    w.open_project(str(EXAMPLE))
    with qtbot.waitSignal(w.computeFinished, timeout=10000):
        w.start_compute()
        assert bridge.started.wait(5)
        w.act_cancel.trigger()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=5000)
    assert "I_COMPUTE_CANCELLED" in w.message_dock.codes()
    assert w.result_tabs.count() == 1


def test_validation_errors_block_compute(make_window):
    w = make_window(engine=FakeBridge())
    assert not w.start_compute()
    assert "E_PWR_EMPTY" in w.message_dock.codes()
    assert not w.is_computing()


# ---------------------------------------------------------------------------------------------
# real engine end-to-end
# ---------------------------------------------------------------------------------------------
@pytest.mark.skipif(not ENGINE_AVAILABLE, reason="core.engine not available")
def test_compute_example_with_engine(make_window, qtbot, tmp_path):
    w = make_window()
    w.open_project(str(EXAMPLE))
    with qtbot.waitSignal(w.computeFinished, timeout=60000):
        assert w.start_compute()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=10000)
    assert [r.name for r in w.results] == ["VDD_CORE", "VDD_IO"]
    assert len(w.plots) == 2 and w.result_tabs.count() == 3
    curve = w.plots["VDD_CORE"].curve("VDD_CORE")
    assert curve is not None and len(curve.xData) == 400
    values = w.readout_values()
    assert len(values) == 2 and all(len(r) == 3 for r in values)
    assert all(v is not None and v > 0 for row in values for v in row)
    for c in range(3):
        float(w.readout_table.item(0, c).text())
    # export
    written = w.export_csv(str(tmp_path / "csv"))
    assert len(written) == 2 and all(os.path.isfile(p) for p in written)
    xlsx = w.export_xlsx(str(tmp_path / "res.xlsx"))
    assert xlsx and os.path.isfile(xlsx)
    png = w.export_png(str(tmp_path / "plot.png"))
    assert png and os.path.getsize(png) > 0
    # autosave records had_results
    doc = json.loads((Path(w.appdata_dir) / AutosaveStore.FILE).read_text("utf-8"))
    assert doc["session"]["had_results"] is True


# ---------------------------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------------------------
def test_help_window_opens_every_page(qtbot):
    win = HelpWindow()
    qtbot.addWidget(win)
    win.show()
    files = win.page_files()
    assert len(files) == len(HELP_PAGES) == 15
    assert "troubleshooting.html" in files
    for file_name in files:
        win.show_page(file_name)
        assert win.current_page_file() == file_name
        assert win.browser.document().toPlainText().strip()
        assert win.missing_images() == [], file_name
        assert win.contents.currentItem().data(Qt.ItemDataRole.UserRole + 1) == file_name
    assert win.back_action.isEnabled()
    win.back_action.trigger()
    assert win.current_page_file() == files[-2]
    win.home_action.trigger()
    assert win.current_page_file() == "index.html"
    win.find_edit.setText("PDN")
    assert win.find_next()


def test_help_menu_and_about(make_window):
    w = make_window()
    hw = w.show_help("results.html")
    assert hw.current_page_file() == "results.html"
    w.help_page_actions["physics.html"].trigger()
    assert hw.current_page_file() == "physics.html"
    assert "MIT" in w.about_text() and __version__ in w.about_text()


# ---------------------------------------------------------------------------------------------
# persistence (§5.8)
# ---------------------------------------------------------------------------------------------
def test_autosave_debounce_and_content(make_window, qtbot, appdata, monkeypatch):
    w = make_window()
    w.open_project(str(EXAMPLE))
    target = appdata / AutosaveStore.FILE
    qtbot.wait(50)

    writes = []
    original = AutosaveStore.save

    def counting_save(self, project, session):
        wrote = original(self, project, session)
        if wrote:
            writes.append(project.vias.drill_diameter_mm)
        return wrote

    monkeypatch.setattr(AutosaveStore, "save", counting_save)
    w.via_panel.drill.setValue(0.25)
    w.via_panel.drill.setValue(0.3)
    w.via_panel.drill.setValue(0.35)
    assert w.autosave.is_pending()

    def written() -> bool:
        return (target.is_file() and json.loads(target.read_text("utf-8"))["vias"][
            "drill_diameter_mm"] == pytest.approx(0.35))

    qtbot.waitUntil(written, timeout=1500)
    qtbot.wait(300)
    assert writes == [pytest.approx(0.35)]
    assert w.isWindowModified()


def test_autosave_restore_roundtrip(make_window, qtbot, appdata):
    w = make_window(auto_compute=False)
    w.open_project(str(EXAMPLE))
    w.via_panel.drill.setValue(0.3)
    w.splitter.setSizes([500, 700])
    w.input_tabs.setCurrentIndex(3)
    w.decap_panel.set_filter("VDD_IO")
    w.set_z_unit("ohm")
    sizes = w.splitter.sizes()
    w.close()
    assert (appdata / AutosaveStore.FILE).is_file()

    w2 = make_window(auto_compute=False)
    assert w2.pwr_model.rowCount() == 2
    assert w2.decap_model.rowCount() == 4
    assert w2.stackup_model.rowCount() == 11
    assert w2.via_panel.drill.value() == pytest.approx(0.3)
    assert w2.project.decap_rows[2].dummy is True
    assert w2.input_tabs.currentIndex() == 3
    assert w2.decap_panel.current_filter() == "VDD_IO"
    assert w2.recent_files and Path(w2.recent_files[0]) == EXAMPLE
    assert Path(w2.project_path) == EXAMPLE
    assert w2.isWindowModified()
    assert "example_project" in w2.windowTitle()
    assert w2.project.display.z_unit == "ohm" and w2.unit_actions["ohm"].isChecked()
    assert abs(w2.splitter.sizes()[0] - sizes[0]) <= 5


@pytest.mark.skipif(not ENGINE_AVAILABLE, reason="core.engine not available")
def test_restore_with_results_recomputes_and_reapplies_view(make_window, qtbot, appdata):
    w = make_window()
    w.open_project(str(EXAMPLE))
    with qtbot.waitSignal(w.computeFinished, timeout=60000):
        w.start_compute()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=10000)
    plot = w.plots["VDD_CORE"]
    plot.vb.disableAutoRange()
    plot.vb.setRange(xRange=(6.0, 8.0), yRange=(0.0, 2.0), padding=0)
    w.select_result_tab("VDD_CORE")
    w.close()

    w2 = make_window()
    qtbot.waitUntil(lambda: len(w2.plots) == 2 and not w2.is_computing(), timeout=60000)
    view = w2.plots["VDD_CORE"].view_state()
    assert not view.auto_range
    assert view.x_range_log10 == pytest.approx((6.0, 8.0), abs=1e-6)
    assert w2.current_result_name() == "VDD_CORE"


def test_corrupt_autosave_starts_with_defaults(make_window, appdata):
    (appdata / AutosaveStore.FILE).write_text("{ this is not json", encoding="utf-8")
    w = make_window()
    assert w.pwr_model.rowCount() == 0
    assert "W_AUTOSAVE_CORRUPT" in w.message_dock.codes()
    assert any(p.name.startswith("autosave.corrupt-") for p in appdata.iterdir())


def test_second_window_does_not_autosave(make_window, qtbot, appdata):
    w1 = make_window()
    w1.open_project(str(EXAMPLE))
    w1.autosave.flush()
    target = appdata / AutosaveStore.FILE
    before = target.read_bytes()
    w2 = make_window()
    assert not w2.autosave.enabled
    assert w2.banner.isVisible()
    w2.via_panel.drill.setValue(1.5)
    w2.autosave.flush()
    qtbot.wait(1200)
    assert target.read_bytes() == before


def test_save_as_appends_suffix_and_updates_recent(make_window, tmp_path):
    w = make_window()
    w.open_project(str(EXAMPLE))
    w.via_panel.drill.setValue(0.3)
    assert w.isWindowModified()
    assert w.save_project_as(str(tmp_path / "board"))
    saved = tmp_path / "board.spical.json"
    assert saved.is_file()
    assert Path(w.recent_files[0]) == saved
    assert not w.isWindowModified()
    assert w.windowTitle() == "Simple PI Calculator — board[*]"
    doc = json.loads(saved.read_text("utf-8"))
    assert "session" not in doc
    assert doc["vias"]["drill_diameter_mm"] == pytest.approx(0.3)
    assert w.new_project(confirm=False)
    assert w.pwr_model.rowCount() == 0 and w.project_path is None


def test_import_excel_examples(make_window):
    w = make_window()
    ex = ROOT / "examples"
    assert w.import_stackup(str(ex / "stackup_6L.xlsx"))
    assert w.import_pwr_list(str(ex / "pwr_list.xlsx"))
    assert w.import_decap_list(str(ex / "decap_list.xlsx"))
    assert w.stackup_model.rowCount() == 11
    assert w.pwr_model.rowCount() == 2
    assert w.decap_model.rowCount() >= 1
    assert w.isWindowModified()
    assert not w.import_stackup(str(ex / "cap_0402_100nF.mod"))
    assert w.message_dock.issues("import")


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def test_self_test_cli(tmp_path):
    appdata = tmp_path / "selftest_appdata"
    appdata.mkdir()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SPICAL_APPDATA_DIR=str(appdata))
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "simple_pi_calculator", "--self-test"],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert list(appdata.iterdir()) == []


# ---------------------------------------------------------------------------------------------
# regression tests from the pre-release code review (docs/REVIEW-code.md)
# ---------------------------------------------------------------------------------------------
def test_uncaught_exception_goes_to_messages_dock_not_modal(make_window, monkeypatch):
    from simple_pi_calculator import app as app_mod

    w = make_window()
    hook = app_mod.make_excepthook("/tmp/app.log", window_getter=lambda: w)
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: pytest.fail("fell through"))
    try:
        raise ValueError("slot exploded")
    except ValueError:
        hook(*sys.exc_info())
    issues = w.message_dock.issues("internal")
    assert [i.code for i in issues] == ["E_INTERNAL"]
    assert "slot exploded" in issues[0].message and "/tmp/app.log" in issues[0].message
    assert w.message_dock.isVisible()
    # the real lookup finds the window among the top-level widgets
    assert app_mod._find_main_window() is not None


def test_remove_without_selection_is_not_an_edit(make_window):
    w = make_window()
    w.open_project(str(EXAMPLE))
    assert not w.isWindowModified()
    for model in (w.stackup_model, w.pwr_model, w.decap_model):
        model.remove_rows([])
        model.remove_rows([99])
    assert not w.isWindowModified()
    assert w.decap_model.rowCount() == 4
    w.decap_model.remove_rows([0])
    assert w.decap_model.rowCount() == 3 and w.isWindowModified()


def test_vias_per_decap_pad_spin_box(make_window):
    w = make_window()
    panel = w.via_panel
    assert panel.vias_per_pad.value() == 1 and panel.vias_per_pad.minimum() == 1
    panel.vias_per_pad.setValue(3)  # odd counts are valid: 3 vias on each pad
    assert w.project.vias.vias_per_pad == 3 and panel.vias_per_pad.value() == 3
    form = panel.form
    assert form.labelForField(panel.vias_per_pad).text() == "Vias per decap pad"
    assert form.labelForField(panel.pad_vias).text() == "PAD vias (per observation pad)"
    assert "EACH" in panel.vias_per_pad.toolTip() and "observation" in panel.pad_vias.toolTip()
    _, w_dec = w.bridge.port_widths_m(w.project)
    assert w_dec == pytest.approx(cluster_port_width(3, 0.2e-3, 1.0e-3), rel=1e-12)


def test_run_commits_typed_via_count(make_window, qtbot):
    """A count typed into a spin box with keyboard tracking off is committed by Run."""
    w = make_window(engine=FakeBridge())
    w.open_project(str(EXAMPLE))
    w.input_tabs.setCurrentIndex(1)
    spin = w.via_panel.vias_per_pad
    spin.setFocus()
    qtbot.waitUntil(spin.hasFocus, timeout=2000)
    spin.lineEdit().selectAll()
    qtbot.keyClicks(spin.lineEdit(), "4")
    assert w.project.vias.vias_per_pad == 1  # not yet committed (no Enter, no focus change)
    w.act_run.trigger()
    assert w.project.vias.vias_per_pad == 4
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=10000)


def test_edit_during_compute_marks_results_stale(make_window, qtbot):
    bridge = FakeBridge(delay_s=0.5)
    w = make_window(engine=bridge)
    w.open_project(str(EXAMPLE))
    with qtbot.waitSignal(w.computeFinished, timeout=10000):
        assert w.start_compute()
        assert bridge.started.wait(5)
        w.via_panel.drill.setValue(0.3)  # edited while the snapshot is being computed
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=5000)
    assert w.results and w.stale
    assert w.plots["VDD_CORE"].title_text().endswith("(inputs changed)")
    # a clean recompute clears the flag
    with qtbot.waitSignal(w.computeFinished, timeout=10000):
        assert w.start_compute()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=5000)
    assert not w.stale


def test_markers_outside_sweep_read_na(make_window):
    w = make_window()
    res = fake_result("A")
    res.marker_f_hz = np.asarray([1e6])
    res.marker_z = np.asarray([1e-3 + 0j])
    w.show_results([res])
    assert [w.readout_table.item(0, c).text() for c in range(3)][1:] == ["n/a", "n/a"]


def test_self_test_report_file(tmp_path):
    report = tmp_path / "self test ü.txt"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SPICAL_APPDATA_DIR=str(tmp_path / "ad"))
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "simple_pi_calculator", "--self-test",
                           "--self-test-report", str(report)],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = report.read_text("utf-8")
    assert text.rstrip().endswith("SELF-TEST OK")
    assert "help pages checked" in text and "VDD_IO: |Z|" in text
    assert not (tmp_path / "ad").exists()


# ---------------------------------------------------------------------------------------------
# Dummy Cap / count edits reach the computation (user report: "2× count + Dummy Cap gives the
# same result as the original rows")
# ---------------------------------------------------------------------------------------------
def _run_and_wait(w: MainWindow, qtbot) -> None:
    with qtbot.waitSignal(w.computeFinished, timeout=120000):
        assert w.start_compute()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=10000)


def _core_markers_mohm(w: MainWindow) -> np.ndarray:
    res = {r.name: r for r in w.results}["VDD_CORE"]
    return np.abs(res.marker_z) * 1e3


@pytest.mark.skipif(not ENGINE_AVAILABLE, reason="core.engine not available")
def test_doubled_count_with_dummy_cap_changes_results(make_window, qtbot):
    """User flow through the table models: example → count ×2 → Dummy Cap on → Run."""
    w = make_window()
    w.open_project(str(EXAMPLE))
    _run_and_wait(w, qtbot)
    base = _core_markers_mohm(w)
    base_curve = np.array(w.plots["VDD_CORE"].curve("VDD_CORE").yData, dtype=float)

    w.decap_panel.set_filter("VDD_CORE")
    proxy = w.decap_panel.proxy
    assert proxy.rowCount() == 2
    for prow in range(proxy.rowCount()):
        count_idx = proxy.index(prow, DecapTableModel.COL_COUNT)
        n = int(proxy.data(count_idx, Qt.ItemDataRole.EditRole))
        assert proxy.setData(count_idx, 2 * n, Qt.ItemDataRole.EditRole)
        dummy_idx = proxy.index(prow, DecapTableModel.COL_DUMMY)
        assert proxy.setData(dummy_idx, Qt.CheckState.Checked.value,
                             Qt.ItemDataRole.CheckStateRole)
    assert [(r.count, r.dummy) for r in w.project.decap_rows if r.pwr_name == "VDD_CORE"] \
        == [(20, True), (8, True)]
    assert w.stale  # edits mark the previous results stale

    _run_and_wait(w, qtbot)
    assert not w.stale
    doubled = _core_markers_mohm(w)
    curve = np.array(w.plots["VDD_CORE"].curve("VDD_CORE").yData, dtype=float)
    assert not np.allclose(curve, base_curve, rtol=1e-3)
    assert np.all(np.abs(doubled / base - 1.0) > 0.05)
    # engine reference values (VDD_CORE, 1/10/100 MHz)
    assert doubled == pytest.approx([2.306, 18.13, 129.0], rel=2e-3)
    assert w.readout_values()[0] == pytest.approx(list(doubled), rel=1e-9)


@pytest.mark.skipif(not ENGINE_AVAILABLE, reason="core.engine not available")
def test_run_commits_open_cell_editor(make_window, qtbot):
    """Root cause of the report: a count typed into the cell editor was ignored by Run (F5 /
    toolbar do not take the focus, so the editor never committed)."""
    captured = []
    w = make_window()
    real = w.bridge.make_inputs

    def spy(project, path):
        inputs = real(project, path)
        captured.append([(r.count, r.dummy) for r in inputs.decap_rows])
        return inputs

    w.bridge.make_inputs = spy
    w.open_project(str(EXAMPLE))
    w.input_tabs.setCurrentIndex(3)
    view, proxy = w.decap_panel.table, w.decap_panel.proxy
    idx = proxy.mapFromSource(w.decap_model.index(0, DecapTableModel.COL_COUNT))
    view.setCurrentIndex(idx)
    view.edit(idx)
    editor = view.indexWidget(idx)
    assert editor is not None and view.state() == view.State.EditingState
    editor.lineEdit().selectAll()
    qtbot.keyClicks(editor.lineEdit(), "20")
    assert w.project.decap_rows[0].count == 10  # still only in the editor
    w.act_run.trigger()
    assert w.project.decap_rows[0].count == 20
    assert captured and captured[-1][0] == (20, False)
    assert view.state() != view.State.EditingState
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=120000)


def test_checkbox_cell_toggles_once_per_click_or_double_click(make_window, qtbot):
    """A click anywhere in the Dummy Cap cell toggles; a double click toggles exactly once
    (Qt's default toggled twice, leaving the state unchanged)."""
    w = make_window(engine=FakeBridge())
    w.open_project(str(EXAMPLE))
    w.input_tabs.setCurrentIndex(3)
    view, proxy = w.decap_panel.table, w.decap_panel.proxy
    idx = proxy.mapFromSource(w.decap_model.index(0, DecapTableModel.COL_DUMMY))
    view.scrollTo(idx)
    rect = view.visualRect(idx)
    point = rect.center()
    assert not w.project.decap_rows[0].dummy
    assert point.x() - rect.left() > 30  # centre of the cell, well away from the indicator
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert w.project.decap_rows[0].dummy
    from PySide6.QtWidgets import QApplication
    qtbot.wait(QApplication.doubleClickInterval() + 100)
    # real double click: press, release, double-click, release → exactly one toggle
    vp, left = view.viewport(), Qt.MouseButton.LeftButton
    qtbot.mousePress(vp, left, pos=point)
    qtbot.mouseRelease(vp, left, pos=point)
    qtbot.mouseDClick(vp, left, pos=point)
    qtbot.mouseRelease(vp, left, pos=point)
    assert not w.project.decap_rows[0].dummy
    qtbot.wait(QApplication.doubleClickInterval() + 100)
    # a synthetic double click whose first click never reached the cell still toggles once
    qtbot.mouseDClick(vp, left, pos=point)
    assert w.project.decap_rows[0].dummy
    qtbot.wait(QApplication.doubleClickInterval() + 100)
    view.setCurrentIndex(idx)
    qtbot.keyClick(view, Qt.Key.Key_Space)
    assert not w.project.decap_rows[0].dummy
    assert w.isWindowModified()
