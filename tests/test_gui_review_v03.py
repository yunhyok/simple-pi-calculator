"""GUI regression tests of review v0.3 (docs/REVIEW-v0.3.md): distance controls keep values the
spin boxes cannot show, v3 auto-save migration, dirty flag and auto-save on distance edits."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("pyqtgraph")

from simple_pi_calculator.gui.engine_bridge import EngineBridge  # noqa: E402
from simple_pi_calculator.gui.main_window import MainWindow  # noqa: E402
from simple_pi_calculator.io.project_io import AutosaveStore  # noqa: E402

pytestmark = [pytest.mark.gui,
              pytest.mark.skipif(not EngineBridge().available(), reason="engine unavailable")]

ROOT = Path(__file__).resolve().parent.parent


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
        kwargs.setdefault("auto_compute", False)
        w = MainWindow(**kwargs)
        qtbot.addWidget(w)
        w.show()
        created.append(w)
        return w

    yield _make
    for w in created:
        try:
            w.close()
        except RuntimeError:
            pass


def test_seed_or_mode_edit_keeps_sigma_the_spin_box_cannot_show(make_window, tmp_path):
    for name in ("example_project.spical.json", "cap_0402_100nF.mod", "cap_0603_10uF.mod"):
        shutil.copy(ROOT / "examples" / name, tmp_path / name)
    doc = json.loads((tmp_path / "example_project.spical.json").read_text("utf-8"))
    doc["decaps"].update(distance_mode="normal", sigma_mm=0.12345, seed=7)
    path = tmp_path / "p.spical.json"
    path.write_text(json.dumps(doc), "utf-8")
    w = make_window()
    assert w.open_project(str(path), confirm=False)
    panel = w.decap_panel
    assert panel.sigma.value() == pytest.approx(0.123)  # 3 decimals shown
    assert not w.modified
    panel.seed.setValue(8)  # review v0.3: this used to write σ = 0.123 back
    assert w.project.distance.sigma_mm == 0.12345 and w.project.distance.seed == 8
    assert w.modified
    panel.distance_mode.setCurrentIndex(panel.distance_mode.findData("fixed"))
    assert w.project.distance.mode == "fixed" and w.project.distance.sigma_mm == 0.12345
    panel.distance_mode.setCurrentIndex(panel.distance_mode.findData("normal"))
    panel.sigma.setValue(0.2)
    assert w.project.distance.sigma_mm == 0.2 and w.project.distance.seed == 8


def test_v3_autosave_restores_fixed_and_rewrites_schema_4(make_window, appdata):
    shutil.copy(ROOT / "tests" / "data" / "project_v3.spical.json", appdata / AutosaveStore.FILE)
    w = make_window()
    d = w.project.distance
    assert (d.mode, d.sigma_mm, d.seed) == ("fixed", 0.5, 12345)
    panel = w.decap_panel
    assert panel.distance_mode.currentData() == "fixed"
    assert not any(x.isEnabled() for x in (panel.sigma, panel.seed, panel.new_seed_button))
    assert w.project.decap_rows
    panel.distance_mode.setCurrentIndex(panel.distance_mode.findData("normal"))
    assert w.modified and panel.seed.isEnabled()
    w._flush_autosave()
    saved = json.loads((appdata / AutosaveStore.FILE).read_text("utf-8"))
    assert saved["schema_version"] == 4
    assert (saved["decaps"]["distance_mode"], saved["decaps"]["sigma_mm"],
            saved["decaps"]["seed"]) == ("normal", 0.5, 12345)
    assert len(saved["decaps"]["rows"]) == len(w.project.decap_rows)
