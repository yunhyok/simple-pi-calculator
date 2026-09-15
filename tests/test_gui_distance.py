"""GUI smoke tests of the decap distance distribution (DESIGN.md §2.5.5, §5.5): Decaps tab
controls, dirty flag, persistence (project file and auto-save), placement preview and results."""

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

from PySide6.QtCore import QPointF  # noqa: E402

from simple_pi_calculator.gui.engine_bridge import EngineBridge  # noqa: E402
from simple_pi_calculator.gui.main_window import MainWindow  # noqa: E402
from simple_pi_calculator.io.project_io import AutosaveStore, load_project  # noqa: E402

pytestmark = [pytest.mark.gui,
              pytest.mark.skipif(not EngineBridge().available(), reason="engine unavailable")]

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


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
    return {r.name: r for r in w.results}


def _port_pos(preview, p):
    pl = preview.placement
    x0, y0, scale, _pw, ph = preview._frame(pl)
    return QPointF(x0 + pl.xy_m[p][0] * scale, y0 + ph - pl.xy_m[p][1] * scale)


def test_distance_controls_dirty_preview_results_and_persistence(make_window, qtbot, workdir,
                                                                appdata):
    path = workdir / "example_project.spical.json"
    w = make_window()
    assert w.open_project(str(path))
    w.project.sweep.n_points = 41
    panel = w.decap_panel
    assert panel.distance_mode.currentData() == "fixed"
    assert not panel.sigma.isEnabled() and not panel.seed.isEnabled()
    assert not panel.new_seed_button.isEnabled()
    fixed = {k: v.z_pad.copy() for k, v in _run(w, qtbot).items()}
    w.save_project()
    assert not w.modified

    w.pwr_panel.select_pwr("VDD_CORE")
    pl_fixed = w.pwr_panel.preview.placement
    assert pl_fixed.distance_mode == "fixed"
    assert len(np.unique(pl_fixed.xy_m[1:11, 1])) == 1

    # Fixed → Normal: dirty, enabled, preview scattered, stale results
    panel.distance_mode.setCurrentIndex(panel.distance_mode.findData("normal"))
    assert w.modified and w.isWindowModified() and w.stale
    assert w.project.distance.mode == "normal"
    assert panel.sigma.isEnabled() and panel.seed.isEnabled() and panel.new_seed_button.isEnabled()
    pl = w.pwr_panel.preview.placement
    assert pl.distance_mode == "normal" and pl.seed == 12345 and pl.sigma_mm == 0.5
    ys = pl.xy_m[1:11, 1]
    assert len(np.unique(ys)) == 10
    d = pl.port_distances_m[:10] * 1e3
    assert np.all((d >= 7.5) & (d <= 8.5))
    tip = w.pwr_panel.preview.tooltip_text(_port_pos(w.pwr_panel.preview, 1))
    assert "via set 1" in tip and "(sampled)" in tip and f"{d[0]:.4f} mm" in tip
    assert "PAD 1" in w.pwr_panel.preview.tooltip_text(_port_pos(w.pwr_panel.preview, 0))

    normal = _run(w, qtbot)
    assert "I_DIST_SAMPLED" in w.message_dock.codes()
    for name, z in fixed.items():
        assert np.max(np.abs(normal[name].z_pad - z) / np.abs(z)) > 1e-6
    # preview == computed geometry
    assert np.array_equal(normal["VDD_CORE"].placement.xy_m, pl.xy_m)
    assert [s[2] for s in normal["VDD_CORE"].sampled_distances] == \
        pytest.approx((pl.port_distances_m * 1e3).tolist(), rel=1e-15)

    # σ and seed spin boxes and "New seed"
    panel.sigma.setValue(0.25)
    assert w.project.distance.sigma_mm == 0.25 and w.stale
    before = w.pwr_panel.preview.placement.port_distances_m.copy()
    old_seed = panel.seed.value()
    new_seed = panel.new_seed()
    assert new_seed != old_seed and w.project.distance.seed == new_seed
    assert not np.array_equal(w.pwr_panel.preview.placement.port_distances_m, before)
    panel.seed.setValue(4242)
    assert w.project.distance.seed == 4242
    changed = _run(w, qtbot)
    assert not np.array_equal(changed["VDD_CORE"].z_pad, normal["VDD_CORE"].z_pad)
    assert changed["VDD_CORE"].distance.seed == 4242

    # named project file
    assert w.save_project() and not w.modified
    doc = json.loads(path.read_text("utf-8"))
    assert doc["schema_version"] == 4
    assert (doc["decaps"]["distance_mode"], doc["decaps"]["sigma_mm"], doc["decaps"]["seed"]) \
        == ("normal", 0.25, 4242)
    project, _ = load_project(path)
    assert (project.distance.mode, project.distance.sigma_mm, project.distance.seed) == \
        ("normal", 0.25, 4242)

    # auto-save / session round trip
    panel.seed.setValue(99)
    w.close()
    saved = json.loads((appdata / AutosaveStore.FILE).read_text("utf-8"))
    assert saved["decaps"]["seed"] == 99
    w2 = make_window()
    assert w2.project.distance.mode == "normal" and w2.project.distance.seed == 99
    assert w2.decap_panel.distance_mode.currentData() == "normal"
    assert w2.decap_panel.seed.value() == 99 and w2.decap_panel.sigma.value() == 0.25
    assert w2.decap_panel.seed.isEnabled()

    # back to Fixed: controls disabled, fixed result reproduced
    w2.decap_panel.distance_mode.setCurrentIndex(0)
    assert w2.project.distance.mode == "fixed" and not w2.decap_panel.seed.isEnabled()
    w2.project.sweep.n_points = 41
    again = _run(w2, qtbot)
    for name, z in fixed.items():
        assert np.array_equal(again[name].z_pad, z)
