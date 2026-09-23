"""GUI regressions of 0.4.1: editing a table cell must not change the selected PWR / filter /
result tab / preview, the wheel and arrow keys must not change values by accident, and table
columns are resizable with widths kept across edits and restarts."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QDoubleSpinBox,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from simple_pi_calculator.constants import MARKER_FREQUENCIES_HZ  # noqa: E402
from simple_pi_calculator.gui.main_window import MainWindow  # noqa: E402
from simple_pi_calculator.gui.models import DecapTableModel, PwrTableModel  # noqa: E402
from simple_pi_calculator.gui.widgets import (  # noqa: E402
    CellComboBox,
    CellDoubleSpinBox,
    CellSpinBox,
    install_wheel_guard,
    is_wheel_guarded,
)
from simple_pi_calculator.io.project_io import Session, WindowState  # noqa: E402

pytestmark = pytest.mark.gui

ROOT = Path(__file__).resolve().parent.parent
FILES = ("example_project.spical.json", "cap_0402_100nF.mod", "cap_0603_10uF.mod")


@dataclass
class FakeResult:
    name: str
    f_hz: np.ndarray
    z_pad: np.ndarray
    z_plane_only: np.ndarray | None = None
    marker_f_hz: np.ndarray = field(default_factory=lambda: np.asarray(MARKER_FREQUENCIES_HZ))
    marker_z: np.ndarray = field(default_factory=lambda: np.full(3, 1e-3, complex))
    info: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)


def fake(name: str, level: float) -> FakeResult:
    f = np.logspace(5, 9, 100)
    return FakeResult(name, f, level * (1 + 1j * f / 1e7))


@pytest.fixture
def appdata(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "appdata"
    folder.mkdir()
    monkeypatch.setenv("SPICAL_APPDATA_DIR", str(folder))
    return folder


@pytest.fixture
def workdir(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    for name in FILES:
        shutil.copy(ROOT / "examples" / name, folder / name)
    return folder


@pytest.fixture
def make_window(qtbot, appdata):
    created: list[MainWindow] = []

    def _make(**kwargs) -> MainWindow:
        kwargs.setdefault("auto_compute", False)
        kwargs.setdefault("use_settings", False)
        w = MainWindow(**kwargs)
        qtbot.addWidget(w)
        w.resize(1500, 950)
        w.show()
        qtbot.waitExposed(w)
        created.append(w)
        return w

    yield _make
    for w in created:
        try:
            w.close()
        except RuntimeError:
            pass


@pytest.fixture
def window(make_window, workdir):
    w = make_window()
    assert w.open_project(str(workdir / "example_project.spical.json"), confirm=False)
    w.show_results([fake("VDD_CORE", 1e-3), fake("VDD_IO", 2e-3)])
    return w


def wheel(widget: QWidget, dy: int = -120) -> bool:
    """Send a wheel notch to ``widget``; returns whether the widget accepted it.

    A synthesized (``sendEvent``) wheel event is not propagated to the parent by Qt; a real one
    is, when the receiver ignores it (see :func:`wheel_spontaneous`).
    """
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(pos, QPointF(widget.mapToGlobal(pos.toPoint())), QPoint(0, 0),
                        QPoint(0, dy), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event.isAccepted()


def wheel_spontaneous(widget: QWidget, dy: int = -120) -> None:
    """A wheel notch through the window system (as from a mouse) at the centre of ``widget``."""
    top = widget.window()
    pos = widget.mapTo(top, QPoint(widget.width() // 2, widget.height() // 2))
    QTest.wheelEvent(top.windowHandle(), QPointF(pos), QPoint(0, dy))
    QApplication.processEvents()


def state(w: MainWindow) -> tuple:
    """Everything that must not move when a decap cell is edited."""
    return (w.decap_panel.current_filter(), w.decap_panel.filter_combo.currentText(),
            w.current_result_name(), w.pwr_panel.selected_pwr(), w.pwr_panel.preview._title)


def type_into_cell(qtbot, view: QAbstractItemView, index, text: str, finish=Qt.Key.Key_Return):
    """Select ``index``, type ``text`` (AnyKeyPressed opens the delegate editor) and finish."""
    view.setCurrentIndex(index)
    view.setFocus()
    qtbot.waitUntil(view.hasFocus, timeout=2000)
    QTest.keyClicks(view, text[0])
    assert view.state() == QAbstractItemView.State.EditingState
    editor = QApplication.focusWidget()
    if len(text) > 1:
        QTest.keyClicks(editor, text[1:])
    QTest.keyClick(editor, finish)
    QApplication.processEvents()


# ---------------------------------------------------------------------------------------------
# bug 1: editing a decap cell changed the PWR ("channel") by itself
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("flt", [None, "VDD_IO", "VDD_CORE"])
def test_editing_decap_cells_keeps_pwr_filter_result_tab_and_preview(window, qtbot, flt):
    w = window
    w.pwr_panel.select_pwr("VDD_CORE")      # PWR Nets selection ≠ Decaps filter
    w.select_result_tab("VDD_IO")
    w.input_tabs.setCurrentIndex(3)
    panel = w.decap_panel
    panel.set_filter(flt)
    before = state(w)
    assert before[0] == flt and before[2] == "VDD_IO" and before[3] == "VDD_CORE"
    view, proxy = panel.table, panel.proxy
    n_rows = proxy.rowCount()
    src = proxy.mapToSource(proxy.index(1, 0)).row()
    pwr_of_row = w.project.decap_rows[src].pwr_name

    # Distance: digits + Enter
    type_into_cell(qtbot, view, proxy.index(1, DecapTableModel.COL_DIST), "7.5")
    assert w.project.decap_rows[src].distance_mm == 7.5
    assert state(w) == before
    # # Decaps: digits + Tab (commits and moves to the next cell)
    type_into_cell(qtbot, view, proxy.index(1, DecapTableModel.COL_COUNT), "12",
                   Qt.Key.Key_Tab)
    assert w.project.decap_rows[src].count == 12
    assert state(w) == before
    # the edited row is still the current one, with its PWR, and no row appeared / vanished
    assert proxy.mapToSource(view.currentIndex()).row() == src
    assert w.project.decap_rows[src].pwr_name == pwr_of_row
    assert proxy.rowCount() == n_rows
    assert w.stale and w.modified
    # a distance-distribution edit (global control) does not re-sync the filter either
    panel.distance_mode.setCurrentIndex(panel.distance_mode.findData("normal"))
    assert state(w) == before


def test_other_edits_do_not_resync_the_decap_filter(window, qtbot):
    w = window
    w.pwr_panel.select_pwr("VDD_CORE")
    w.decap_panel.set_filter("VDD_IO")
    before = state(w)
    # PWR Nets: width edit of the selected PWR, via / stack-up edits, add a stack-up layer
    idx = w.pwr_model.index(0, PwrTableModel.COL_WIDTH)
    assert w.pwr_model.setData(idx, 31.0)
    w.via_panel.drill.setValue(w.via_panel.drill.value() + 0.05)
    w.stackup_model.setData(w.stackup_model.index(0, 1), "TOPX")
    assert state(w) == before
    # removing another row of the PWR list keeps the same selected PWR: no re-sync
    w.pwr_panel.table.selectRow(0)
    w.pwr_model.insert_row()           # appended, not selected
    w.pwr_panel.select_pwr("VDD_CORE")
    w.decap_panel.set_filter("VDD_IO")
    before = state(w)
    w.pwr_model.remove_rows([len(w.project.pwr_rows) - 1])
    assert state(w) == before
    # a real selection change in the PWR Nets table still drives the filter (0.1 behaviour)
    w.pwr_panel.select_pwr("VDD_IO")
    assert w.decap_panel.current_filter() == "VDD_IO"
    w.pwr_panel.select_pwr("VDD_CORE")
    assert w.decap_panel.current_filter() == "VDD_CORE"


def test_editing_pwr_name_under_filter_keeps_the_row_under_the_cursor(window, qtbot):
    w = window
    w.input_tabs.setCurrentIndex(3)
    panel = w.decap_panel
    panel.set_filter("VDD_CORE")
    view, proxy = panel.table, panel.proxy
    n_rows = proxy.rowCount()
    src = proxy.mapToSource(proxy.index(0, 0)).row()
    idx = proxy.index(0, DecapTableModel.COL_PWR)
    view.setCurrentIndex(idx)
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_F2)
    editor = view.indexWidget(idx)
    assert isinstance(editor, CellComboBox) and editor.isEditable()
    assert editor.completer() is None  # no inline completion into another PWR name
    editor.lineEdit().selectAll()
    QTest.keyClicks(editor, "VDD_IO")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()  # the delegate commits Enter through a queued call
    assert w.project.decap_rows[src].pwr_name == "VDD_IO"
    # the row stays where it is (no re-filter on edit), the filter is unchanged
    assert proxy.rowCount() == n_rows
    assert proxy.mapToSource(view.currentIndex()).row() == src
    assert panel.current_filter() == "VDD_CORE"
    # choosing the filter again applies it
    panel.set_filter("VDD_CORE")
    assert proxy.rowCount() == n_rows - 1


def test_arrow_keys_leave_cell_editors_without_stepping_the_value(window, qtbot):
    w = window
    w.input_tabs.setCurrentIndex(3)
    panel = w.decap_panel
    panel.set_filter(None)
    view, proxy = panel.table, panel.proxy
    for col, cls in ((DecapTableModel.COL_COUNT, CellSpinBox),
                     (DecapTableModel.COL_DIST, CellDoubleSpinBox),
                     (DecapTableModel.COL_PWR, CellComboBox)):
        idx = proxy.index(0, col)
        before = [proxy.index(r, col).data(Qt.ItemDataRole.EditRole) for r in range(2)]
        view.setCurrentIndex(idx)
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_F2)
        editor = view.indexWidget(idx)
        assert isinstance(editor, cls)
        QTest.keyClick(editor, Qt.Key.Key_Down)
        QApplication.processEvents()
        assert view.currentIndex().row() == 1 and view.currentIndex().column() == col
        assert view.state() != QAbstractItemView.State.EditingState
        assert [proxy.index(r, col).data(Qt.ItemDataRole.EditRole) for r in range(2)] == before
        QTest.keyClick(view, Qt.Key.Key_Up)
    assert state(w)[0] is None


def test_escape_in_cell_editor_cancels_the_edit_not_the_computation(window, qtbot):
    w = window
    fired: list[bool] = []
    w.act_cancel.setEnabled(True)  # as while a computation runs
    w.act_cancel.triggered.connect(lambda *_: fired.append(True))
    w.activateWindow()
    qtbot.waitUntil(w.isActiveWindow, timeout=2000)
    w.input_tabs.setCurrentIndex(3)
    panel = w.decap_panel
    panel.set_filter(None)
    view, proxy = panel.table, panel.proxy
    idx = proxy.index(0, DecapTableModel.COL_DIST)
    old = proxy.index(0, DecapTableModel.COL_DIST).data(Qt.ItemDataRole.EditRole)
    view.setCurrentIndex(idx)
    view.setFocus()
    QTest.keyClicks(view, "9")
    editor = QApplication.focusWidget()
    assert view.state() == QAbstractItemView.State.EditingState
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert not fired
    assert view.state() != QAbstractItemView.State.EditingState
    assert proxy.index(0, DecapTableModel.COL_DIST).data(Qt.ItemDataRole.EditRole) == old
    # outside an editor Esc is still the Cancel shortcut
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert fired


def test_row_actions_commit_the_open_editor_first(window, qtbot):
    w = window
    w.input_tabs.setCurrentIndex(3)
    panel = w.decap_panel
    panel.set_filter("VDD_CORE")
    view, proxy = panel.table, panel.proxy
    src = proxy.mapToSource(proxy.index(0, 0)).row()
    idx = proxy.index(0, DecapTableModel.COL_DIST)
    view.setCurrentIndex(idx)
    view.setFocus()
    QTest.keyClicks(view, "4")
    assert view.state() == QAbstractItemView.State.EditingState
    QTest.keyClicks(QApplication.focusWidget(), ".25")
    n = len(w.project.decap_rows)
    w.act_add_row.trigger()
    assert w.project.decap_rows[src].distance_mm == 4.25
    assert len(w.project.decap_rows) == n + 1
    assert w.project.decap_rows[-1].pwr_name == "VDD_CORE"
    assert panel.current_filter() == "VDD_CORE"


# ---------------------------------------------------------------------------------------------
# wheel hazards
# ---------------------------------------------------------------------------------------------
def test_wheel_over_a_spin_cell_editor_does_not_change_the_value(window, qtbot):
    w = window
    w.input_tabs.setCurrentIndex(3)
    panel = w.decap_panel
    panel.set_filter(None)
    view, proxy = panel.table, panel.proxy
    for col in (DecapTableModel.COL_COUNT, DecapTableModel.COL_DIST, DecapTableModel.COL_PWR):
        idx = proxy.index(0, col)
        old = idx.data(Qt.ItemDataRole.EditRole)
        view.setCurrentIndex(idx)
        view.edit(idx)                     # double click / F2: the editor has the focus
        editor = view.indexWidget(idx)
        assert is_wheel_guarded(editor)
        for _ in range(3):
            wheel(editor)
        text = editor.currentText() if col == DecapTableModel.COL_PWR else editor.value()
        assert text == old
        QTest.keyClick(editor, Qt.Key.Key_Return)
        assert proxy.index(0, col).data(Qt.ItemDataRole.EditRole) == old
    # a wheel over a closed cell only scrolls: nothing changes
    rect = view.visualRect(proxy.index(0, DecapTableModel.COL_DIST))
    before = [r.distance_mm for r in w.project.decap_rows]
    wheel(view.viewport())
    assert [r.distance_mm for r in w.project.decap_rows] == before
    assert rect.isValid()


def test_wheel_changes_a_spin_box_only_after_a_click_into_it(qtbot):
    received: list[int] = []

    class Parent(QWidget):
        def wheelEvent(self, event):  # noqa: N802
            received.append(event.angleDelta().y())
            event.accept()

    parent = Parent()
    layout = QVBoxLayout(parent)
    spin = QDoubleSpinBox(parent)
    spin.setRange(0, 100)
    spin.setValue(50)
    layout.addWidget(spin)
    install_wheel_guard(spin)
    qtbot.addWidget(parent)
    parent.show()
    qtbot.waitExposed(parent)
    assert spin.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert not wheel(spin)                    # ignored: Qt hands a real one on to the parent
    assert spin.value() == 50
    if hasattr(QTest, "wheelEvent"):          # Qt ≥ 6.8: a real (spontaneous) wheel event
        wheel_spontaneous(spin)
        assert spin.value() == 50 and received == [-120]  # the parent scrolls instead
    spin.setFocus()                           # focus alone (Tab, cell editor) is not enough
    assert not wheel(spin)
    assert spin.value() == 50
    QTest.mouseClick(spin.lineEdit(), Qt.MouseButton.LeftButton)
    assert wheel(spin)
    assert spin.value() == 49
    spin.clearFocus()                         # leaving disarms it again
    wheel(spin)
    assert spin.value() == 49


def test_wheel_over_panel_controls_does_not_change_inputs(window, qtbot):
    w = window
    w.decap_panel.set_filter(None)
    w.decap_panel.distance_mode.setCurrentIndex(1)  # normal: σ and seed enabled
    snapshot = (w.decap_panel.current_filter(), w.project.distance.mode,
                w.project.distance.sigma_mm, w.project.distance.seed,
                w.project.sweep.n_points, w.project.vias.drill_diameter_mm,
                w.project.vias.vias_per_pad, w.project.advanced.via_model,
                w.project.advanced.workers)
    d, v, s = w.decap_panel, w.via_panel, w.sweep_panel
    widgets = [d.filter_combo, d.distance_mode, d.sigma, d.seed, s.points, v.drill, v.antipad,
               v.pitch, v.vias_per_pad, v.pad_vias, v.via_model, v.plating, v.conductivity,
               v.mounting, v.s2p_mode, v.workers]
    for widget in widgets:
        assert is_wheel_guarded(widget), widget
        for dy in (-120, 120, 120):
            wheel(widget, dy)
    assert (w.decap_panel.current_filter(), w.project.distance.mode,
            w.project.distance.sigma_mm, w.project.distance.seed,
            w.project.sweep.n_points, w.project.vias.drill_diameter_mm,
            w.project.vias.vias_per_pad, w.project.advanced.via_model,
            w.project.advanced.workers) == snapshot


# ---------------------------------------------------------------------------------------------
# bug 2: column widths
# ---------------------------------------------------------------------------------------------
def _tables(w: MainWindow):
    return {"stackup": w.stackup_panel.table, "pwr": w.pwr_panel.table,
            "decaps": w.decap_panel.table, "readout": w.readout_table}


def test_all_table_columns_are_interactive(window):
    w = window
    for key, table in _tables(w).items():
        header = table.horizontalHeader()
        assert header.count() > 0
        for col in range(header.count()):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive, (key, col)
        assert not header.stretchLastSection()
    tree = w.message_dock.tree.header()
    assert all(tree.sectionResizeMode(c) == QHeaderView.ResizeMode.Interactive
               for c in range(tree.count()))


def test_user_column_width_survives_edits_refreshes_and_reload(window, workdir, qtbot):
    w = window
    w.input_tabs.setCurrentIndex(3)
    tables = _tables(w)
    chosen = {"stackup": (0, 140), "pwr": (5, 133), "decaps": (DecapTableModel.COL_DIST, 157),
              "readout": (1, 211)}
    for key, (col, width) in chosen.items():
        header = tables[key].horizontalHeader()
        assert header.sectionSize(col) != width
        header.resizeSection(col, width)  # what a drag of the divider does
        assert header.sectionSize(col) == width
    # the file-name column (fill) can be resized as well and then stops filling
    decap_header = tables["decaps"].horizontalHeader()
    decap_header.resizeSection(DecapTableModel.COL_FILE, 222)

    # data changes: cell edits in every table, derived refreshes, results, unit switch
    w.decap_model.setData(w.decap_model.index(0, DecapTableModel.COL_COUNT), 99)
    w.decap_model.setData(w.decap_model.index(0, DecapTableModel.COL_FILE),
                          "a_much_longer_model_file_name_than_before_0402_100nF.mod")
    w.pwr_model.setData(w.pwr_model.index(0, PwrTableModel.COL_NAME), "VDD_CORE_RENAMED_LONG")
    w.stackup_model.setData(w.stackup_model.index(0, 1), "TOP_LAYER_WITH_A_LONG_NAME")
    w._refresh_derived()
    w.show_results([fake("VDD_CORE_RENAMED_LONG", 1e-3), fake("VDD_IO", 2e-3)])
    w.set_z_unit("uohm")
    w.resize(1300, 900)
    qtbot.wait(20)
    for key, (col, width) in chosen.items():
        assert tables[key].horizontalHeader().sectionSize(col) == width, key
    assert decap_header.sectionSize(DecapTableModel.COL_FILE) == 222
    # model reset (open the project again) keeps them too
    assert w.open_project(str(workdir / "example_project.spical.json"), confirm=False)
    qtbot.wait(20)
    for key, (col, width) in chosen.items():
        if key != "readout":
            assert tables[key].horizontalHeader().sectionSize(col) == width, key


def test_automatic_columns_fit_contents_and_fill(window, qtbot):
    w = window
    w.input_tabs.setCurrentIndex(3)
    qtbot.wait(20)
    view = w.decap_panel.table
    sizer = view.column_sizer
    header = view.horizontalHeader()
    for col in range(header.count()):
        if col != DecapTableModel.COL_FILE:
            assert header.sectionSize(col) >= min(header.sectionSizeHint(col), 320) - 1
    primary = sum(header.sectionSize(c) for c in range(DecapTableModel.COL_MODE + 1))
    assert abs(primary - (view.viewport().width() - 1)) <= 2 \
        or header.sectionSize(DecapTableModel.COL_FILE) == sizer.minimum
    assert sizer.widths() == [0] * header.count()  # nothing chosen by the user yet


def test_column_widths_round_trip_through_the_session(make_window, workdir, appdata, qtbot):
    w = make_window()
    assert w.open_project(str(workdir / "example_project.spical.json"), confirm=False)
    w.input_tabs.setCurrentIndex(3)
    w.decap_panel.table.horizontalHeader().resizeSection(DecapTableModel.COL_DIST, 171)
    w.pwr_panel.table.horizontalHeader().resizeSection(PwrTableModel.COL_NAME, 190)
    w.stackup_panel.table.horizontalHeader().resizeSection(3, 123)
    w.message_dock.tree.header().resizeSection(1, 150)
    session = w.collect_session()
    assert session.window.column_widths["decaps"][DecapTableModel.COL_DIST] == 171
    assert session.window.column_widths["decaps"][DecapTableModel.COL_FILE] == 0  # automatic
    assert "readout" not in session.window.column_widths  # never resized: not stored
    w.close()

    w2 = make_window()  # restores the auto-save
    w2.input_tabs.setCurrentIndex(3)
    qtbot.wait(20)
    assert w2.decap_panel.table.horizontalHeader().sectionSize(DecapTableModel.COL_DIST) == 171
    assert w2.pwr_panel.table.horizontalHeader().sectionSize(PwrTableModel.COL_NAME) == 190
    assert w2.stackup_panel.table.horizontalHeader().sectionSize(3) == 123
    assert w2.message_dock.tree.header().sectionSize(1) == 150
    assert w2.decap_panel.table.column_sizer.is_user_sized(DecapTableModel.COL_DIST)
    assert not w2.decap_panel.table.column_sizer.is_user_sized(DecapTableModel.COL_FILE)


def test_stored_widths_with_another_column_count_are_ignored(make_window, workdir, qtbot):
    w = make_window()
    assert w.open_project(str(workdir / "example_project.spical.json"), confirm=False)
    header = w.decap_panel.table.horizontalHeader()
    auto = [header.sectionSize(c) for c in range(header.count())]
    session = w.collect_session()
    session.window.column_widths = {"decaps": [300] * (header.count() - 1),
                                    "pwr": "garbage", "unknown": [1, 2]}
    w.apply_session(session)
    assert [header.sectionSize(c) for c in range(header.count())] == auto
    assert not w.decap_panel.table.column_sizer.user_columns()


def test_window_state_column_widths_serialisation():
    from simple_pi_calculator.io.project_io import _session_from_dict, _session_to_dict

    s = Session(window=WindowState(column_widths={"decaps": [0, 80, 0, 64], "pwr": [120]}))
    raw = _session_to_dict(s)
    assert raw["window"]["column_widths"] == {"decaps": [0, 80, 0, 64], "pwr": [120]}
    back = _session_from_dict(raw)
    assert back.window.column_widths == {"decaps": [0, 80, 0, 64], "pwr": [120]}
    raw["window"]["column_widths"] = {"decaps": [1, "x"], "pwr": [True], "ok": [-5, 7.9],
                                      "bad": 3}
    assert _session_from_dict(raw).window.column_widths == {"ok": [0, 7]}
    raw["window"].pop("column_widths")
    assert _session_from_dict(raw).window.column_widths == {}
