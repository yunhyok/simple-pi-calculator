"""Results export (CSV, Touchstone, plot images) and the plot "Reset view" (DESIGN.md §4.8, §5.6).

GUI parts run offscreen (``QT_QPA_PLATFORM=offscreen``) with fake results; no engine needed.
"""

from __future__ import annotations

import csv
import io
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from simple_pi_calculator.constants import MARKER_FREQUENCIES_HZ  # noqa: E402
from simple_pi_calculator.core.touchstone import (  # noqa: E402
    format_touchstone_v1,
    parse_s2p_text,
    touchstone_extension,
)
from simple_pi_calculator.errors import IssueCollector  # noqa: E402
from simple_pi_calculator.io.export import (  # noqa: E402
    TouchstoneOptions,
    combined_csv_rows,
    export_csv,
    export_csv_combined,
    export_touchstone_combined,
    export_touchstone_per_pwr,
    safe_file_name,
    touchstone_combined_path,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "example_project.spical.json"


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


def fake_result(name: str, level_ohm: float = 1e-3, n: int = 120, plane: bool = False
                ) -> FakeResult:
    f = np.logspace(5, 9, n)
    # capacitive + resistive + inductive, so Re/Im both vary and have both signs
    w = 2 * np.pi * f
    z = level_ohm + 1j * (w * 1e-10 - 1.0 / (w * 1e-4))
    mf = np.asarray(MARKER_FREQUENCIES_HZ)
    wm = 2 * np.pi * mf
    mz = level_ohm + 1j * (wm * 1e-10 - 1.0 / (wm * 1e-4))
    return FakeResult(name, f, z, 3 * z if plane else None, mf, mz, {"M": 10, "N": 5})


# ---------------------------------------------------------------------------------------------
# minimal generic Touchstone v1 reader for the tests (the app reader only handles 2-port S)
# ---------------------------------------------------------------------------------------------
def read_touchstone_generic(text: str, n_ports: int):
    option = None
    values: list[float] = []
    for raw in text.splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            option = line[1:].split()
            continue
        values.extend(float(t) for t in line.split())
    assert option is not None
    unit, param, fmt, r_kw, r = option
    assert unit.lower() == "hz" and r_kw.upper() == "R"
    block = 1 + 2 * n_ports * n_ports
    assert len(values) % block == 0
    arr = np.asarray(values).reshape(-1, block)
    a, b = arr[:, 1::2], arr[:, 2::2]
    v = a + 1j * b if fmt.upper() == "RI" else a * np.exp(1j * np.deg2rad(b))
    if n_ports == 2:
        m = np.empty((len(arr), 2, 2), complex)
        m[:, 0, 0], m[:, 1, 0], m[:, 0, 1], m[:, 1, 1] = v[:, 0], v[:, 1], v[:, 2], v[:, 3]
    else:
        m = v.reshape(-1, n_ports, n_ports)
    return arr[:, 0], m, param.upper(), fmt.upper(), float(r)


def z_from(param: str, m: np.ndarray, r: float) -> np.ndarray:
    return r * (1 + m) / (1 - m) if param == "S" else m * r


# ---------------------------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------------------------
def test_csv_single_file_content(tmp_path: Path):
    results = [fake_result("VDD_CORE", 1e-3, plane=True), fake_result("VDD_IO", 2e-3)]
    path = export_csv_combined(results, str(tmp_path / "all.csv"), "board")
    text = Path(path).read_text(encoding="utf-8")
    comments = [ln for ln in text.splitlines() if ln.startswith("#")]
    assert any("VDD_IO: |Z| @ 1e+06 Hz" in ln for ln in comments)
    assert "# VDD_IO: Number of PADs = 1" in comments
    rows = list(csv.reader(io.StringIO("\n".join(ln for ln in text.splitlines()
                                                 if not ln.startswith("#")))))
    assert rows[0] == ["Frequency (Hz)", "VDD_CORE |Z| (Ohm)", "VDD_CORE Re Z (Ohm)",
                       "VDD_CORE Im Z (Ohm)", "VDD_CORE |Z| plane only (Ohm)",
                       "VDD_IO |Z| (Ohm)", "VDD_IO Re Z (Ohm)", "VDD_IO Im Z (Ohm)"]
    data = np.array(rows[1:], dtype=float)
    assert data.shape == (120, 8)
    np.testing.assert_allclose(data[:, 0], results[0].f_hz, rtol=1e-9)
    z0, z1 = results[0].z_pad, results[1].z_pad
    np.testing.assert_allclose(data[:, 1], np.abs(z0), rtol=1e-9)
    np.testing.assert_allclose(data[:, 2], z0.real, rtol=1e-9)
    np.testing.assert_allclose(data[:, 3], z0.imag, rtol=1e-9)
    np.testing.assert_allclose(data[:, 4], np.abs(results[0].z_plane_only), rtol=1e-9)
    np.testing.assert_allclose(data[:, 5:], np.column_stack([np.abs(z1), z1.real, z1.imag]),
                               rtol=1e-9)


def test_csv_single_file_rejects_different_grids():
    a = fake_result("A")
    b = fake_result("B", n=50)
    with pytest.raises(ValueError, match="frequency grid"):
        combined_csv_rows([a, b])


def test_csv_per_pwr_files(tmp_path: Path):
    paths = export_csv([fake_result("A/1"), fake_result("B")], str(tmp_path), "p")
    assert [os.path.basename(p) for p in paths] == ["p_A_1.csv", "p_B.csv"]
    res = fake_result("C")
    res.info["n_pads"] = 4
    path = export_csv([res], str(tmp_path), "p")[0]
    text = Path(path).read_text(encoding="utf-8")
    assert "# Number of PADs: 4" in text.splitlines()


def test_safe_file_name():
    used: set[str] = set()
    assert safe_file_name("VDD:CORE/1*", used) == "VDD_CORE_1_"
    assert safe_file_name("vdd:core/1*", used) == "vdd_core_1__2"
    assert safe_file_name("CON") == "_CON"
    assert safe_file_name("x. ") == "x"


# ---------------------------------------------------------------------------------------------
# Touchstone
# ---------------------------------------------------------------------------------------------
def test_extension_naming():
    assert touchstone_extension(1) == ".s1p"
    assert touchstone_extension(3) == ".s3p"
    assert touchstone_extension(99) == ".s99p"
    with pytest.raises(ValueError):
        touchstone_extension(100)
    assert touchstone_combined_path("/x/board.s2p", 3) == "/x/board.s3p"
    assert touchstone_combined_path("/x/board", 2) == "/x/board.s2p"


@pytest.mark.parametrize("fmt", ["RI", "MA"])
def test_s2p_roundtrip_with_app_reader(tmp_path: Path, fmt: str):
    results = [fake_result("VDD_CORE", 1e-3), fake_result("VDD_IO", 5e-3)]
    path = export_touchstone_combined(results, str(tmp_path / "board"), "board",
                                      TouchstoneOptions("S", fmt, 1.0))
    assert path.endswith("board.s2p")
    text = Path(path).read_text(encoding="utf-8")
    assert f"# Hz S {fmt} R 1" in text.splitlines()
    assert "! Port 2: PWR VDD_IO" in text
    assert "UNCOUPLED" in text
    data = parse_s2p_text(text, IssueCollector(), path)
    assert data.z0 == 1.0
    np.testing.assert_allclose(data.f_hz, results[0].f_hz, rtol=1e-15)
    assert np.all(data.s[:, 0, 1] == 0) and np.all(data.s[:, 1, 0] == 0)
    for k, res in enumerate(results):
        z = data.z0 * (1 + data.s[:, k, k]) / (1 - data.s[:, k, k])
        assert np.max(np.abs(z - res.z_pad)) < 1e-12


@pytest.mark.parametrize("param,fmt,r", [("S", "RI", 1.0), ("Z", "RI", 1.0), ("S", "MA", 0.1),
                                         ("Z", "MA", 50.0)])
def test_s1p_per_pwr_roundtrip(tmp_path: Path, param: str, fmt: str, r: float):
    results = [fake_result("VDD_CORE", 1e-3), fake_result("VDD:IO", 2e-3)]
    project = None
    if EXAMPLE.is_file():
        from simple_pi_calculator.io.project_io import load_project

        project, _ = load_project(EXAMPLE)
        results[0].name = project.pwr_rows[0].name
    paths = export_touchstone_per_pwr(results, str(tmp_path), "board",
                                      TouchstoneOptions(param, fmt, r), project)
    assert [os.path.basename(p) for p in paths] == [f"board_{results[0].name}.s1p",
                                                    "board_VDD_IO.s1p"]
    for path, res in zip(paths, results):
        text = Path(path).read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0].startswith("! Simple PI Calculator ")
        assert f"! PWR: {res.name}" in lines
        assert any(ln.startswith("! Exported (UTC): ") for ln in lines)
        assert f"# Hz {param} {fmt} R {r:.12g}" in lines
        data_lines = [ln for ln in lines if ln and not ln.startswith(("!", "#"))]
        assert len(data_lines) == len(res.f_hz) and all(len(ln.split()) == 3 for ln in data_lines)
        f, m, p, fm, rr = read_touchstone_generic(text, 1)
        np.testing.assert_allclose(f, res.f_hz, rtol=1e-15)
        z = z_from(p, m[:, 0, 0], rr)
        assert np.max(np.abs(z - res.z_pad)) < 1e-12
    if project is not None:
        assert "PWR layer" in Path(paths[0]).read_text(encoding="utf-8")


def test_s3p_self_consistency(tmp_path: Path):
    results = [fake_result(n, lvl) for n, lvl in (("A", 1e-3), ("B", 2e-3), ("C", 4e-4))]
    path = export_touchstone_combined(results, str(tmp_path / "x.csv"), "x",
                                      TouchstoneOptions("S", "RI", 1.0))
    assert path.endswith("x.csv.s3p")
    text = Path(path).read_text(encoding="utf-8")
    data_lines = [ln for ln in text.splitlines() if ln and not ln.startswith(("!", "#"))]
    # v1 layout for 3 ports: 3 lines per frequency (one per matrix row), 3 pairs each,
    # frequency only on the first line
    assert len(data_lines) == 3 * len(results[0].f_hz)
    for k in range(0, len(data_lines), 3):
        assert len(data_lines[k].split()) == 7
        assert len(data_lines[k + 1].split()) == 6 and data_lines[k + 1].startswith(" ")
        assert len(data_lines[k + 2].split()) == 6
    f, m, param, fmt, r = read_touchstone_generic(text, 3)
    np.testing.assert_allclose(f, results[0].f_hz, rtol=1e-15)
    off = ~np.eye(3, dtype=bool)
    assert np.all(m[:, off] == 0)
    for k, res in enumerate(results):
        assert np.max(np.abs(z_from(param, m[:, k, k], r) - res.z_pad)) < 1e-12


def test_five_port_rows_wrap_after_four_pairs():
    f = np.array([1e6, 2e6])
    data = np.arange(2 * 25).reshape(2, 5, 5).astype(complex)
    text = format_touchstone_v1(f, data, parameter="Z")
    lines = [ln for ln in text.splitlines() if not ln.startswith(("!", "#"))]
    # per frequency: 5 matrix rows × (4 pairs + 1 pair) lines
    assert len(lines) == 2 * 10
    assert [len(ln.split()) for ln in lines[:10]] == [9, 2, 8, 2, 8, 2, 8, 2, 8, 2]
    _, m, *_ = read_touchstone_generic(text, 5)
    np.testing.assert_array_equal(m.real, data.real)


def test_touchstone_rejects_bad_input():
    with pytest.raises(ValueError):
        format_touchstone_v1([1e6], np.ones((1, 1, 1)), parameter="Y")
    with pytest.raises(ValueError):
        format_touchstone_v1([1e6], np.ones((1, 1, 1)), r_ref=0)
    with pytest.raises(ValueError):
        format_touchstone_v1([2e6, 1e6], np.ones((2, 1, 1)))


# ---------------------------------------------------------------------------------------------
# GUI: export all plots, messages, reset view, Ctrl+D
# ---------------------------------------------------------------------------------------------
gui = pytest.mark.gui


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    pytest.importorskip("pyqtgraph")
    folder = tmp_path / "appdata"
    folder.mkdir()
    monkeypatch.setenv("SPICAL_APPDATA_DIR", str(folder))
    from simple_pi_calculator.gui.main_window import MainWindow

    w = MainWindow(auto_compute=False)
    qtbot.addWidget(w)
    w.show()
    w.show_results([fake_result("VDD_CORE", 1e-3, plane=True), fake_result("VDD/IO:2", 2e-3)])
    yield w
    w.close()


def _dark_pixels(image, x0: int, x1: int, y0: int, y1: int) -> int:
    count = 0
    for x in range(x0, x1):
        for y in range(y0, y1):
            c = image.pixelColor(x, y)
            if c.red() < 100 and c.green() < 100 and c.blue() < 100:
                count += 1
    return count


@gui
def test_export_all_plots_png(window, tmp_path: Path):
    from PySide6.QtGui import QImage

    w = window
    assert w.current_result_name() is None  # the PWR tabs were never shown
    out = tmp_path / "plots"
    written = w.export_all_plots(str(out), "png", 900, 600)
    names = sorted(os.path.basename(p) for p in written)
    assert names == ["All_PWRs.png", "VDD_CORE.png", "VDD_IO_2.png"]
    for p in written:
        assert os.path.getsize(p) > 10_000
        image = QImage(p)
        assert (image.width(), image.height()) == (900, 600)
        # axis tick labels / axis title on the left and the frequency labels at the bottom
        assert _dark_pixels(image, 0, 70, 0, 600) > 200
        assert _dark_pixels(image, 0, 900, 550, 600) > 200
    assert "I_EXPORT" in w.message_dock.codes()
    info = [i for i in w.message_dock.issues("export") if i.code == "I_EXPORT"]
    assert str(out) in info[-1].message


@gui
def test_export_all_plots_svg_and_keep_zoom(window, tmp_path: Path):
    w = window
    plot = w.plots["VDD_CORE"]
    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.0, 1.0), padding=0)
    written = w.export_all_plots(str(tmp_path), "svg", 800, 500, keep_zoom=True)
    assert len(written) == 3
    for p in written:
        text = Path(p).read_text(encoding="utf-8")
        assert text.lstrip().startswith("<?xml") and "<svg" in text
        assert os.path.getsize(p) > 5_000
    # exporting never changes the on-screen view
    assert plot.vb.viewRange()[0] == pytest.approx([6.0, 7.0])


@gui
def test_exports_from_window_and_errors_go_to_messages(window, tmp_path: Path):
    from simple_pi_calculator.io.export import TouchstoneOptions as Opt

    w = window
    single = w.export_csv(str(tmp_path / "all"), one_file_per_pwr=False)
    assert [os.path.basename(p) for p in single] == ["all.csv"]
    per = w.export_csv(str(tmp_path / "csv"))
    assert len(per) == 2
    ts = w.export_touchstone(str(tmp_path / "board"), combined=True, options=Opt("Z", "MA", 1.0))
    assert [os.path.basename(p) for p in ts] == ["board.s2p"]
    s1p = w.export_touchstone(str(tmp_path / "ts"), combined=False)
    assert sorted(os.path.basename(p) for p in s1p) == ["results_VDD_CORE.s1p",
                                                        "results_VDD_IO_2.s1p"]
    assert sum(1 for i in w.message_dock.issues("export") if i.code == "I_EXPORT") == 4
    # error: target folder is an existing file → Error line, no exception
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    assert w.export_all_plots(str(blocker), "png", 400, 300) == []
    assert w.export_touchstone(str(blocker / "sub"), combined=False) == []
    errors = [i for i in w.message_dock.issues("export") if i.code == "E_EXPORT"]
    assert len(errors) == 2
    for act in (w.act_export_touchstone, w.act_export_all_plots):
        assert act.isEnabled()


def _fresh_default(plot):
    return [v for r in plot.vb.viewRange() for v in r]


@gui
def test_reset_view_restores_auto_range(window, qtbot):
    w = window
    w.select_result_tab("VDD_CORE")
    plot = w.current_plot()
    qtbot.wait(50)
    plot.vb.updateAutoRange()
    assert plot.is_default_view()
    default = _fresh_default(plot)
    assert plot.plot.getAxis("bottom").logMode and plot.plot.getAxis("left").logMode
    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.5, 1.0), padding=0)  # programmatic zoom
    assert not plot.is_default_view()
    plot.set_markers_visible(True)
    w.reset_view_button.click()
    assert plot.is_default_view()
    assert _fresh_default(plot) == pytest.approx(default, abs=1e-9)
    assert all(line.isVisible() for line in plot.marker_lines)
    view = plot.view_state()
    assert view.auto_range

    # pyqtgraph's context-menu "View All" maps to the same default
    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.5, 1.0), padding=0)
    plot.vb.menu.viewAll.trigger()
    assert plot.is_default_view()
    # our own context-menu entry
    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.5, 1.0), padding=0)
    assert plot.reset_view_action in plot.vb.menu.actions()
    plot.reset_view_action.trigger()
    assert plot.is_default_view()


@gui
def test_unit_switch_keeps_view_state_consistent(window, qtbot):
    w = window
    w.select_result_tab("VDD_CORE")
    plot = w.current_plot()
    plot.reset_view()
    w.set_z_unit("uohm")
    assert plot.is_default_view()  # default view stays the default (re-fitted) view
    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.5, 1.0), padding=0)
    w.set_z_unit("ohm")
    assert not plot.is_default_view()
    (x0, x1), (y0, y1) = plot.vb.viewRange()
    assert (x0, x1) == pytest.approx((6.0, 7.0))
    assert (y0, y1) == pytest.approx((0.5 - 6, 1.0 - 6))
    w.reset_view()
    assert plot.is_default_view()


@gui
def test_ctrl_d_resets_view(window, qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    from PySide6.QtTest import QTest

    w = window
    assert QKeySequence("Ctrl+D") in w.act_reset_view.shortcuts()
    assert w.act_reset_view.shortcutContext() == Qt.ShortcutContext.ApplicationShortcut
    assert w.act_duplicate_row.shortcut() != QKeySequence("Ctrl+D")
    w.select_result_tab("VDD_CORE")
    plot = w.current_plot()
    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.5, 1.0), padding=0)
    assert not plot.is_default_view()
    w.activateWindow()
    w.raise_()
    qtbot.waitUntil(lambda: w.isActiveWindow(), timeout=2000)
    QTest.keyClick(w, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(plot.is_default_view, timeout=2000)

    plot.vb.setRange(xRange=(6.0, 7.0), yRange=(0.5, 1.0), padding=0)
    w.act_reset_view.trigger()
    assert plot.is_default_view()


@gui
def test_fresh_results_show_default_view(window):
    for plot in [window.overview_plot, *window.plots.values()]:
        assert all(plot.vb.autoRangeEnabled())
        assert plot.is_default_view()
        assert math.isfinite(plot.vb.viewRange()[0][0])
