"""Review v0.2 (docs/REVIEW-v0.2.md) — GUI checks with the real engine: reset view after unit
switch and compute, Ctrl+D with the focus in input widgets, exports with a failed PWR, '# PADs'
editor commit on Run, auto-save migration from schema 1 and 2."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from simple_pi_calculator.gui.engine_bridge import EngineBridge  # noqa: E402
from simple_pi_calculator.gui.main_window import MainWindow  # noqa: E402
from simple_pi_calculator.gui.models import PwrTableModel  # noqa: E402
from simple_pi_calculator.io.project_io import AutosaveStore  # noqa: E402

pytestmark = [pytest.mark.gui,
              pytest.mark.skipif(not EngineBridge().available(), reason="engine unavailable")]

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DATA = Path(__file__).resolve().parent / "data"


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
    for name in ("example_project.spical.json", "cap_0402_100nF.mod", "cap_0603_10uF.mod"):
        shutil.copy(EXAMPLES / name, folder / name)
    return folder


@pytest.fixture
def make_window(qtbot, appdata):
    created: list[MainWindow] = []

    def _make(**kwargs) -> MainWindow:
        kwargs.setdefault("auto_compute", False)
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


def _run(w, qtbot):
    with qtbot.waitSignal(w.computeFinished, timeout=120000):
        assert w.start_compute()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=10000)


def _zoom(plot):
    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.5, 1.0), padding=0)
    assert not plot.is_default_view()


def test_reset_view_after_unit_switch_and_after_compute(make_window, qtbot, workdir):
    w = make_window()
    assert w.open_project(str(workdir / "example_project.spical.json"))
    _run(w, qtbot)
    w.select_result_tab("VDD_CORE")
    plot = w.current_plot()
    assert plot.is_default_view()
    _zoom(plot)
    for unit in ("ohm", "uohm", "mohm"):
        w.set_z_unit(unit)
        assert not plot.is_default_view()
        w.reset_view()
        assert plot.is_default_view()
        _zoom(plot)
    # a recompute keeps a manual zoom (session behaviour) and Reset view restores the fit to the
    # *new* data
    w.project.decap_rows[0].count = 20
    _run(w, qtbot)
    plot = w.current_plot()
    assert plot is w.plots["VDD_CORE"]
    assert not plot.is_default_view()
    w.reset_view_button.click()
    assert plot.is_default_view()
    y = np.log10(np.asarray(plot.curve("VDD_CORE").yData, dtype=float))
    (_, _), (y0, y1) = plot.vb.viewRange()
    assert y0 <= y.min() and y1 >= y.max()
    # default view survives a recompute
    w.project.decap_rows[0].count = 10
    _run(w, qtbot)
    assert w.current_plot().is_default_view()
    assert w.overview_plot.is_default_view()


def test_ctrl_d_resets_view_with_focus_in_input_widgets(make_window, qtbot, workdir):
    """View ▸ Reset View is an application shortcut: it must also work while a spin box, a line
    edit or an open table cell editor has the keyboard focus."""
    w = make_window()
    assert w.open_project(str(workdir / "example_project.spical.json"))
    _run(w, qtbot)
    w.select_result_tab("VDD_CORE")
    plot = w.current_plot()
    w.activateWindow()
    qtbot.waitUntil(lambda: w.isActiveWindow(), timeout=2000)

    def press_on(widget):
        _zoom(plot)
        widget.setFocus()
        qtbot.waitUntil(widget.hasFocus, timeout=2000)
        QTest.keyClick(widget, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        qtbot.waitUntil(plot.is_default_view, timeout=2000)

    w.input_tabs.setCurrentIndex(1)
    spin = w.via_panel.drill
    drill = w.project.vias.drill_diameter_mm
    press_on(spin.lineEdit())
    assert w.project.vias.drill_diameter_mm == drill  # the key did not edit the value

    w.input_tabs.setCurrentIndex(2)
    view = w.pwr_panel.table
    idx = view.model().index(0, PwrTableModel.COL_WIDTH)
    view.setCurrentIndex(idx)
    view.edit(idx)
    editor = view.indexWidget(idx)
    assert editor is not None
    target = editor.lineEdit() if hasattr(editor, "lineEdit") else editor
    press_on(target)
    press_on(view)
    press_on(plot)


def test_exports_skip_failed_pwr_with_warning(make_window, qtbot, workdir, tmp_path):
    # a model that only fails when it is parsed at compute time (validation passes)
    (workdir / "broken.mod").write_text(".SUBCKT BROKEN 1 2\nC1 1 2 {UNDEFINED_PARAM}\n.ENDS\n",
                                        "utf-8")
    doc = json.loads((workdir / "example_project.spical.json").read_text("utf-8"))
    for row in doc["decaps"]["rows"]:
        if row["pwr_name"] == "VDD_IO":
            row["model_file"] = "broken.mod"
    path = workdir / "fail.spical.json"
    path.write_text(json.dumps(doc), "utf-8")
    w = make_window()
    assert w.open_project(str(path))
    _run(w, qtbot)
    assert [r.name for r in w.results] == ["VDD_CORE"]
    assert "VDD_IO" in w._failed_pwr_names()
    assert w.act_export_touchstone.isEnabled()
    out = tmp_path / "out"
    ts = w.export_touchstone(str(out / "ts"), combined=False)
    assert [os.path.basename(p) for p in ts] == ["fail_VDD_CORE.s1p"]
    one = w.export_touchstone(str(out / "all.s2p"), combined=True)
    assert one and one[0].endswith(".s1p")  # one successful PWR → 1-port file
    csvs = w.export_csv(str(out / "csv"))
    assert [os.path.basename(p) for p in csvs] == ["fail_VDD_CORE.csv"]
    imgs = w.export_all_plots(str(out / "img"), "png", 400, 300)
    assert sorted(os.path.basename(p) for p in imgs) == ["All_PWRs.png", "VDD_CORE.png"]
    assert w.export_xlsx(str(out / "r.xlsx"))
    export_issues = w.message_dock.issues("export")
    skipped = [i for i in export_issues if i.code == "W_EXPORT_SKIPPED"]
    assert len(skipped) == 5 and all("VDD_IO" in i.message for i in skipped)
    failed = [i for i in w.result_issues if i.code == "E_PWR_FAILED"]
    assert [i.source for i in failed] == ["PWR:VDD_IO"]


def test_npads_cell_editor_committed_by_run(make_window, qtbot, workdir):
    w = make_window()
    assert w.open_project(str(workdir / "example_project.spical.json"))
    captured = []
    real = w.bridge.make_inputs

    def spy(project, path):
        inputs = real(project, path)
        captured.append({p.name: p.n_pads for p in inputs.pwrs})
        return inputs

    w.bridge.make_inputs = spy
    w.input_tabs.setCurrentIndex(2)
    view = w.pwr_panel.table
    model = view.model()
    row = [r.name for r in w.project.pwr_rows].index("VDD_IO")
    idx = model.index(row, PwrTableModel.COL_NPADS)
    view.setCurrentIndex(idx)
    view.edit(idx)
    editor = view.indexWidget(idx)
    assert editor is not None and view.state() == view.State.EditingState
    line = editor.lineEdit() if hasattr(editor, "lineEdit") else editor
    line.selectAll()
    qtbot.keyClicks(line, "3")
    assert w.project.pwr_rows[row].n_pads == 1
    with qtbot.waitSignal(w.computeFinished, timeout=120000):
        w.act_run.trigger()
    qtbot.waitUntil(lambda: not w.is_computing(), timeout=10000)
    assert w.project.pwr_rows[row].n_pads == 3
    assert captured[-1] == {"VDD_CORE": 1, "VDD_IO": 3}
    res = {r.name: r for r in w.results}
    assert res["VDD_IO"].n_pads == 3 and res["VDD_IO"].info["n_pads"] == 3


@pytest.mark.parametrize("fixture,vias_per_pad", [("project_v1.spical.json", 1),
                                                  ("project_v2.spical.json", None)])
def test_autosave_from_old_schema_is_migrated(make_window, qtbot, appdata, workdir, fixture,
                                              vias_per_pad):
    doc = json.loads((DATA / fixture).read_text("utf-8"))
    for row in doc["decaps"]["rows"]:
        row["model_file"] = str(workdir / os.path.basename(row["model_file"]))
    doc["session"] = {"project_path": None, "modified": True, "recent_files": [],
                      "plots": {}, "had_results": False}
    (appdata / AutosaveStore.FILE).write_text(json.dumps(doc), "utf-8")
    w = make_window()
    assert w.project.pwr_rows and all(r.n_pads == 1 for r in w.project.pwr_rows)
    expected = vias_per_pad if vias_per_pad is not None else doc["vias"]["vias_per_pad"]
    assert w.project.vias.vias_per_pad == expected
    _run(w, qtbot)
    assert {r.name for r in w.results} == {"VDD_CORE", "VDD_IO"}
    w.close()
    saved = json.loads((appdata / AutosaveStore.FILE).read_text("utf-8"))
    assert saved["schema_version"] == 4
    assert saved["decaps"]["distance_mode"] == "fixed"
    assert all(r["n_pads"] == 1 for r in saved["pwr"]["rows"])
    assert "vias_per_decap" not in saved["vias"]
