"""Regression tests for the pre-release code review (docs/REVIEW-code.md), Qt-free parts."""

from __future__ import annotations

import dataclasses
import math
import os
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core import decap_model as decap_model_mod
from simple_pi_calculator.core import engine as engine_mod
from simple_pi_calculator.core.decap_model import DecapModelCache
from simple_pi_calculator.core.engine import compute_project, validate_inputs
from simple_pi_calculator.errors import InputError, IssueCollector
from simple_pi_calculator.io.excel_headers import STACKUP_RULES, match_columns
from simple_pi_calculator.io.excel_import import read_decap_list, read_pwr_list, read_stackup
from simple_pi_calculator.io.project_io import load_project, to_inputs

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
MU0 = 4e-7 * math.pi
EPS0 = 8.8541878128e-12


# =============================================================================================
# R1: independent end-to-end cross-check of the compute path (hand implementation of §2)
# =============================================================================================
def _hand_vdd_io(f: np.ndarray, pad_vias: int = 1) -> np.ndarray:
    """|Z_PAD| of the bundled VDD_IO net, written directly from DESIGN.md §2.2–§2.8 with a
    brute-force modal double sum (no static split) and closed-form decap impedances."""
    mm = 1e-3
    d, er, tand, sig, t = 0.1 * mm, 4.3, 0.018, 5.8e7, 35e-6
    W, H, dref = 30 * mm, 14 * mm, 10 * mm
    D, Dap, sv = 0.2 * mm, 0.5 * mm, 1.0 * mm
    r0 = D / 2
    w = r0 / 0.44705
    assert pad_vias == 1
    m_x = w / 2 + 0.1 * W
    l_x = W - 2 * m_x
    ports = np.array([[W / 2, 0.2 * dref],
                      [m_x + 0.5 * l_x / 2, 0.2 * dref + 5 * mm],
                      [m_x + 1.5 * l_x / 2, 0.2 * dref + 5 * mm],
                      [W / 2, 0.2 * dref + dref]])
    om = 2 * np.pi * f
    dl = np.sqrt(2 / (om * MU0 * sig))
    zs = (1 + 1j) / (sig * dl) / np.tanh((1 + 1j) / dl * t)
    zp = 1j * om * MU0 * d + 2 * zs
    k2 = -zp * 1j * om * EPS0 * er * (1 - 1j * tand) / d
    M, N = 403, 188  # §8.11 table
    m, n = np.arange(M + 1), np.arange(N + 1)
    X = np.sqrt(np.where(m == 0, 1, 2)) * np.cos(np.outer(ports[:, 0], m) * np.pi / W) \
        * np.sinc(m * w / (2 * W))
    Y = np.sqrt(np.where(n == 0, 1, 2)) * np.cos(np.outer(ports[:, 1], n) * np.pi / H) \
        * np.sinc(n * w / (2 * H))
    kmn2 = (m[:, None] * np.pi / W) ** 2 + (n[None, :] * np.pi / H) ** 2

    def mp(length, x):
        root = math.sqrt(length ** 2 + x ** 2)
        return MU0 / (2 * math.pi) * (length * math.log((length + root) / x) - root + x)

    h_near = (0.035 + 0.1 + 0.035 + 0.1 + 0.035 + 0.8) * mm  # z_top(7)
    l_loop = mp(2 * h_near, r0) - mp(2 * h_near, sv) + MU0 / (2 * math.pi) * t * math.log(Dap / D)
    te = min(25e-6, r0)
    de = te * (1 - np.exp(-np.sqrt(2 / (om * MU0 * 5.8e7)) / te))
    r_via = (2 * h_near + t) / (5.8e7 * np.pi * (r0 ** 2 - (r0 - de) ** 2))
    z_via = r_via + 1j * om * l_loop
    z0402 = 0.030 + 1j * om * 0.45e-9 + 1 / (1j * om * 100e-9)
    z0603 = 1j * om * 0.5e-9 + 0.003 + 1 / (1 / (1 / (1j * om * 10e-6) + 0.002) + 1 / 100e6)
    loads = np.stack([z0402 / 2 + z_via, z0402 / 2 + z_via, z0603 + z_via], axis=1)
    out = []
    for i in range(f.size):
        z = zp[i] / (W * H) * np.einsum("im,in,jm,jn,mn->ij", X, Y, X, Y, 1 / (kmn2 - k2[i]),
                                         optimize=True)
        zred = z[0, 0] - z[0, 1:] @ np.linalg.solve(z[1:, 1:] + np.diag(loads[i]), z[1:, 0])
        out.append(abs(zred + z_via[i]))
    return np.array(out)


def test_end_to_end_matches_independent_hand_calculation():
    project, _ = load_project(EXAMPLES / "example_project.spical.json")
    results, issues = compute_project(to_inputs(project, str(EXAMPLES / "x.spical.json")))
    io = {r.name: r for r in results}["VDD_IO"]
    hand = _hand_vdd_io(np.array(io.marker_f_hz))
    # static split error ≈ 1e-8 at 100 MHz (§3.3); exact marker evaluation (§3.1)
    assert np.abs(io.marker_z) == pytest.approx(hand, rel=1e-6)


# =============================================================================================
# R2: Excel robustness
# =============================================================================================
def test_stackup_header_variants_units_and_korean_suffixes(make_xlsx):
    path = make_xlsx([["Layer No.", "Name (이름)", "Thickness (um)", "Conductivity [S/m]",
                       "Dk@1GHz", "Df@1GHz"],
                      [1, "TOP", "35", "5.8E7", None, None],
                      [None, None, None, None, None, None],          # empty row skipped
                      [2, "PP", " 100 ", "", "4,2", "0.02"],         # numbers as text
                      [3, "BOT", 35, 5.8e7, None, None]])
    issues = IssueCollector()
    st = read_stackup(path, issues)
    assert not issues.has_errors(), issues.issues
    assert [layer.number for layer in st.layers] == [1, 2, 3]
    assert st.by_number(1).thickness_m == pytest.approx(35e-6)
    assert st.by_number(2).dk == pytest.approx(4.2)
    assert not st.by_number(2).is_metal and st.by_number(3).is_metal


@pytest.mark.parametrize("header, unit_mm", [("Thk (mils)", 0.0254), ("Thickness [microns]", 1e-3),
                                             ("두께 Thickness(mm)", 1.0)])
def test_length_unit_aliases(make_xlsx, header, unit_mm):
    path = make_xlsx([["Layer #", header, "Sigma", "Er", "tan δ"],
                      [1, 10, 5.8e7, None, None], [2, 10, None, 4, 0.02],
                      [3, 10, 5.8e7, None, None]])
    st = read_stackup(path, IssueCollector())
    assert st.by_number(2).thickness_m == pytest.approx(10 * unit_mm * 1e-3)


def test_dk_df_qualified_headers_do_not_steal_other_columns():
    issues = IssueCollector()
    mapping = match_columns(["Layer", "Dielectric Constant", "Thickness", "Conductivity",
                             "Df 10GHz"], STACKUP_RULES, issues, "S", 1)
    assert mapping["dk"][0] == 1 and mapping["df"][0] == 4 and mapping["thickness"][0] == 2


def test_table_found_on_a_later_sheet(make_xlsx):
    """The keyword/active sheet has no header (cover sheet): the other sheets are searched."""
    path = make_xlsx([["PWR Name", "Layer No.", "GND Layer No.", "Plane Width (mm)"],
                      ["VDD", 5, 3, 60]], sheet="전원", extra_sheets={"Cover": [["notes"]]})
    issues = IssueCollector()
    rows = read_pwr_list(path, issues)
    assert [r.name for r in rows] == ["VDD"] and not issues.has_errors()


def test_header_not_found_still_reported_when_no_sheet_matches(make_xlsx):
    path = make_xlsx([["foo", "bar"]], extra_sheets={"Other": [["baz"]]})
    issues = IssueCollector()
    with pytest.raises(InputError):
        read_pwr_list(path, issues)
    assert issues.codes() == ["E_XL_HEADER_NOT_FOUND"]


def test_merged_cells_are_filled(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Decaps"
    for row in (["PWR Name", "Decap Model", "Decap Count", "Distance(mm)", "Dummy Cap"],
                ["VDD", "a.mod", 4, 5, "Yes"], [None, "b.mod", 2, 8, None]):
        ws.append(row)
    ws.merge_cells("A2:A3")  # one PWR name spanning two decap rows
    path = tmp_path / "merged.xlsx"
    wb.save(path)
    issues = IssueCollector()
    rows = read_decap_list(path, issues)
    assert not issues.has_errors(), issues.issues
    assert [(r.pwr_name, r.count, r.dummy) for r in rows] == [("VDD", 4, True), ("VDD", 2, False)]


# =============================================================================================
# R3: project files and model paths
# =============================================================================================
def test_project_file_with_utf8_bom_loads(tmp_path):
    src = (EXAMPLES / "example_project.spical.json").read_bytes()
    target = tmp_path / "bom.spical.json"
    target.write_bytes(b"\xef\xbb\xbf" + src)
    for name in ("cap_0402_100nF.mod", "cap_0603_10uF.mod"):
        shutil.copy(EXAMPLES / name, tmp_path / name)
    project, _ = load_project(target)
    assert len(project.pwr_rows) == 2


def test_relative_model_resolved_via_excel_folder_at_compute_time(tmp_path):
    """A relative model file typed in the table that resolves next to the decap Excel file (the
    GUI shows it as found) must also resolve in the engine (§4.4 order)."""
    project, _ = load_project(EXAMPLES / "example_project.spical.json")
    excel_dir = tmp_path / "excel folder 한글"
    excel_dir.mkdir()
    for name in ("cap_0402_100nF.mod", "cap_0603_10uF.mod"):
        shutil.copy(EXAMPLES / name, excel_dir / name)
    project.decap_source_path = str(excel_dir / "decap_list.xlsx")
    for row in project.decap_rows:
        row.model_file = os.path.basename(row.model_file)
    inputs = to_inputs(project, None)  # untitled project: no project folder
    assert inputs.decap_source_dir == str(excel_dir)
    assert not [i for i in validate_inputs(inputs) if i.code == "E_DECAP_FILE_NOT_FOUND"]
    inputs = dataclasses.replace(inputs, n_points=20)
    results, issues = compute_project(inputs)
    assert [r.name for r in results] == ["VDD_CORE", "VDD_IO"]


def test_unreadable_model_file_is_an_input_error_not_a_crash(tmp_path, monkeypatch):
    path = tmp_path / "locked.mod"
    shutil.copy(EXAMPLES / "cap_0402_100nF.mod", path)

    def deny(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(decap_model_mod, "parse_spice_file", deny)
    issues = IssueCollector()
    with pytest.raises(InputError):
        DecapModelCache().get(str(path), None, None, issues)
    assert issues.codes() == ["E_DECAP_FILE_READ"]


def test_unexpected_error_in_one_pwr_does_not_abort_the_others(monkeypatch):
    project, _ = load_project(EXAMPLES / "example_project.spical.json")
    inputs = dataclasses.replace(to_inputs(project, str(EXAMPLES / "x.spical.json")),
                                 n_points=20)
    real = engine_mod.compute_pwr

    def flaky(stackup, pwr, *args, **kwargs):
        if pwr.name == "VDD_CORE":
            raise ZeroDivisionError("boom")
        return real(stackup, pwr, *args, **kwargs)

    monkeypatch.setattr(engine_mod, "compute_pwr", flaky)
    results, issues = compute_project(inputs, cache=DecapModelCache())
    assert [r.name for r in results] == ["VDD_IO"]
    internal = [i for i in issues if i.code == "E_PWR_INTERNAL"]
    assert len(internal) == 1 and internal[0].source == "PWR:VDD_CORE"


# =============================================================================================
# R4: packaging
# =============================================================================================
def test_write_version_info_renders_package_version(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("wvi", ROOT / "tools" / "write_version_info.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "version_info.txt"
    assert mod.main(["x", str(out)]) == 0
    from simple_pi_calculator import __version__
    text = out.read_text("utf-8")
    major, minor, patch = (int(x) for x in __version__.split(".")[:3])
    assert f"filevers=({major}, {minor}, {patch}, 0)" in text
    assert f"StringStruct('ProductVersion', '{__version__}')" in text


def test_spec_does_not_import_pyqtgraph_examples():
    text = (ROOT / "packaging" / "simple_pi_calculator.spec").read_text("utf-8")
    assert re.search(r'collect_submodules\("pyqtgraph"\s*\)', text) is None
    assert "pyqtgraph.examples" in text


def test_windows_workflow_smoke_step_is_valid_powershell():
    text = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text("utf-8")
    # Start-Process has no -Timeout parameter (the step used to fail before running the exe)
    assert re.search(r"Start-Process[^\n]*-Timeout", text) is None
    assert "--self-test-report" in text and "WaitForExit" in text
    assert "write_version_info.py" in text and (ROOT / "tools" / "write_version_info.py").is_file()
