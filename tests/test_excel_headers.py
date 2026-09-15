"""Fuzzy Excel headers and Excel import (DESIGN.md §4.1–§4.4, §8.9)."""

from __future__ import annotations

import pytest

from conftest import EXAMPLE_STACKUP_ROWS
from simple_pi_calculator.errors import InputError, IssueCollector
from simple_pi_calculator.io.excel_headers import (
    DECAP_RULES,
    PWR_RULES,
    STACKUP_RULES,
    match_columns,
    normalize_header,
    parse_bool_cell,
    parse_mode_cell,
    pwr_ignored_column,
)
from simple_pi_calculator.io.excel_import import read_decap_list, read_pwr_list, read_stackup

STACK_HEADER = ["Layer Number", "Layer Name", "Thickness(mm)", "Conductivity(S/m)", "Dk", "Df"]


def _codes(issues: IssueCollector) -> list[str]:
    return [i.code for i in issues.issues]


# ---------------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("Thickness (mm)", ("thickness", "mm")),
    ("tan δ", ("tand", None)),
    ("Conductivity(S/m)", ("conductivity", "s/m")),
    ("Layer No.", ("layerno", None)),
    ("thickness[um]", ("thickness", "um")),
    ("Thickness (µm)", ("thickness", "um")),
    ("εr", ("er", None)),
    ("layer_number", ("layernumber", None)),
    (None, ("", None)),
])
def test_normalize_header(text, expected):
    assert normalize_header(text) == expected


# ---------------------------------------------------------------------------------------------
# Header variants (§4.2)
# ---------------------------------------------------------------------------------------------
def _field_of(header: str, rules, others: list[str]) -> str | None:
    cells = [header] + others
    mapping = match_columns(cells, rules, IssueCollector(), "S", 1)
    for name, (col, _unit) in mapping.items():
        if col == 0:
            return name
    return None


@pytest.mark.parametrize("header, field", [
    ("Layer No.", "layer_number"), ("LAYER #", "layer_number"), ("layer_number", "layer_number"),
    ("Thickness (mm)", "thickness"), ("thickness[um]", "thickness"), ("Thk(mil)", "thickness"),
    ("Conductivity (S/m)", "conductivity"), ("Sigma", "conductivity"), ("DK", "dk"),
    ("Er", "dk"), ("εr", "dk"), ("Df", "df"), ("tan δ", "df"), ("Loss Tangent", "df"),
    ("Dissipation Factor", "df"), ("Layer Name", "layer_name"),
])
def test_stackup_header_variants(header, field):
    assert _field_of(header, STACKUP_RULES, []) == field


def test_units_extracted_for_matched_columns():
    mapping = match_columns(["Layer #", "Thk(mil)", "Sigma", "Er", "tan δ"], STACKUP_RULES,
                            IssueCollector(), "S", 1)
    assert mapping["thickness"] == (1, "mil")


@pytest.mark.parametrize("order", [
    ["Layer Name", "Layer Number"], ["Layer Number", "Layer Name"]])
def test_layer_name_not_captured_as_number(order):
    mapping = match_columns(order + ["Thickness", "Conductivity", "Dk", "Df"], STACKUP_RULES,
                            IssueCollector(), "S", 1)
    assert order[mapping["layer_number"][0]] == "Layer Number"
    assert order[mapping["layer_name"][0]] == "Layer Name"


@pytest.mark.parametrize("order", [
    ["PWR Name", "Layer Number", "GND Layer Number", "PWR Plane Width"],
    ["GND Layer Number", "PWR Plane Width", "Layer Number", "PWR Name"],
    ["Layer Number", "GND Layer Number", "PWR Name", "Width (mm)"],
])
def test_pwr_layer_columns_regardless_of_order(order):
    mapping = match_columns(order, PWR_RULES, IssueCollector(), "S", 1)
    assert order[mapping["gnd_layer"][0]] == "GND Layer Number"
    assert order[mapping["pwr_layer"][0]] == "Layer Number"
    assert order[mapping["pwr_name"][0]] == "PWR Name"
    assert "width" in mapping


def test_decap_header_mapping():
    header = ["PWR Name", "Decap File Name", "Number of Decaps", "Distance to PAD (mm)",
              "Dummy Cap", "Subckt", "S2P Mode"]
    mapping = match_columns(header, DECAP_RULES, IssueCollector(), "S", 1)
    assert {name: header[col] for name, (col, _u) in mapping.items()} == {
        "pwr_name": "PWR Name", "model_file": "Decap File Name", "count": "Number of Decaps",
        "distance": "Distance to PAD (mm)", "dummy": "Dummy Cap", "subckt": "Subckt",
        "s2p_mode": "S2P Mode"}
    # "Model File" must reach model_file, not the s2p_mode rule
    mapping2 = match_columns(["Net", "Model File", "Qty", "Dist"], DECAP_RULES, IssueCollector(),
                             "S", 1)
    assert mapping2["model_file"][0] == 1 and "s2p_mode" not in mapping2


def test_duplicate_column_warning():
    issues = IssueCollector()
    mapping = match_columns(["Dk", "Er", "Layer", "Thickness", "Sigma", "Df"], STACKUP_RULES,
                            issues, "S", 1)
    assert mapping["dk"][0] == 0
    assert "W_XL_DUP_COLUMN" in _codes(issues)


@pytest.mark.parametrize("value, expected", [
    ("Yes", True), ("no", False), ("TRUE", True), ("False", False), ("1", True), ("0", False),
    (True, True), (False, False), (None, False), ("", False), (1, True), (0, False), ("x", True),
    ("✓", True), (" y ", True), ("N", False)])
def test_bool_cells(value, expected):
    assert parse_bool_cell(value) is expected


def test_bool_cell_invalid():
    with pytest.raises(ValueError):
        parse_bool_cell("maybe")


def test_mode_cells():
    assert parse_mode_cell("Series") == "series"
    assert parse_mode_cell("sh") == "shunt"
    assert parse_mode_cell("shunt-through") == "shunt"
    assert parse_mode_cell(None) is None
    with pytest.raises(ValueError):
        parse_mode_cell("s")


def test_pwr_ignored_predicate():
    assert pwr_ignored_column(*normalize_header("PWR Plane Height"))
    assert not pwr_ignored_column(*normalize_header("PWR Plane Width"))


# ---------------------------------------------------------------------------------------------
# Workbook reading
# ---------------------------------------------------------------------------------------------
def _stack_rows():
    return [[n, name, t, sigma, dk, df] for n, name, t, sigma, dk, df in EXAMPLE_STACKUP_ROWS]


def test_read_bundled_examples(examples_dir):
    issues = IssueCollector()
    stackup = read_stackup(examples_dir / "stackup_6L.xlsx", issues)
    assert len(stackup.layers) == 11
    assert stackup.total_thickness == pytest.approx(1.41e-3, rel=1e-12)
    pwr = read_pwr_list(examples_dir / "pwr_list.xlsx", issues)
    assert [(r.name, r.pwr_layer, r.gnd_layer, r.width_mm) for r in pwr] == [
        ("VDD_CORE", 5, 3, 60.0), ("VDD_IO", 7, 9, 30.0)]
    decaps = read_decap_list(examples_dir / "decap_list.xlsx", issues)
    assert [(r.pwr_name, r.count, r.distance_mm, r.dummy) for r in decaps] == [
        ("VDD_CORE", 10, 8.0, False), ("VDD_CORE", 4, 15.0, False), ("VDD_IO", 4, 5.0, True),
        ("VDD_IO", 1, 10.0, False)]
    assert decaps[0].model_file.replace("\\", "/").endswith("cap_0402_100nF.mod")
    assert not issues.has_errors()


def test_header_found_in_row_3(make_xlsx):
    path = make_xlsx([["My board stack-up"], [], STACK_HEADER] + _stack_rows(), sheet="Stackup")
    issues = IssueCollector()
    stackup = read_stackup(path, issues)
    assert len(stackup.layers) == 11
    assert not issues.has_errors()


def test_sheet_selection_by_keyword(make_xlsx):
    path = make_xlsx([STACK_HEADER] + _stack_rows(), sheet="My Stackup",
                     extra_sheets={"Notes": [["nothing here"]]})
    stackup = read_stackup(path, IssueCollector())
    assert len(stackup.layers) == 11


def test_missing_df_column(make_xlsx):
    rows = [STACK_HEADER[:-1]] + [r[:-1] for r in _stack_rows()]
    path = make_xlsx(rows, sheet="Stackup")
    issues = IssueCollector()
    with pytest.raises(InputError):
        read_stackup(path, issues)
    err = [i for i in issues.issues if i.code == "E_XL_HEADER_NOT_FOUND"]
    assert err and "Df" in err[0].message


def test_unit_um_and_mil(make_xlsx):
    for header, value in (("Thickness(um)", 35), ("Thickness(mil)", 1.378)):
        path = make_xlsx([["Layer", "Name", header, "Conductivity", "Dk", "Df"],
                          [1, "TOP", value, 5.8e7, None, None],
                          [2, "D", value, None, 4.0, 0.02],
                          [3, "BOT", value, 5.8e7, None, None]], sheet="Stackup",
                         name=f"u_{header[-4:-1]}.xlsx")
        stackup = read_stackup(path, IssueCollector())
        assert stackup.by_number(1).thickness_m * 1e3 == pytest.approx(0.035, rel=1e-3)


def test_unknown_unit(make_xlsx):
    path = make_xlsx([["Layer", "Name", "Thickness(furlong)", "Conductivity", "Dk", "Df"],
                      [1, "TOP", 1, 5.8e7, None, None]], sheet="Stackup")
    issues = IssueCollector()
    with pytest.raises(InputError):
        read_stackup(path, issues)
    assert "E_XL_UNIT" in _codes(issues)


def test_number_as_text_and_bad_text(make_xlsx):
    path = make_xlsx([STACK_HEADER,
                      [1, "TOP", "0,035", "5.8E7", None, None],
                      [2, "PP", "0.1", "-", "4.2", "abc"],
                      [3, "BOT", 0.035, 5.8e7, None, None]], sheet="Stackup")
    issues = IssueCollector()
    stackup = read_stackup(path, issues)
    assert stackup.by_number(1).conductivity == pytest.approx(5.8e7)
    assert stackup.by_number(1).thickness_m == pytest.approx(35e-6)
    assert not stackup.by_number(2).is_metal
    bad = [i for i in issues.issues if i.code == "E_XL_NUMBER"]
    assert len(bad) == 1 and bad[0].location == "Stackup!F3" and "abc" in bad[0].message


def test_formula_without_cached_value(make_xlsx):
    path = make_xlsx([STACK_HEADER,
                      [1, "TOP", "=0.01+0.025", 5.8e7, None, None],
                      [2, "PP", 0.1, None, 4.2, 0.02],
                      [3, "BOT", 0.035, 5.8e7, None, None]], sheet="Stackup")
    issues = IssueCollector()
    read_stackup(path, issues)
    err = [i for i in issues.issues if i.code == "E_XL_FORMULA_NO_VALUE"]
    assert err and err[0].location == "Stackup!C2"


def test_stackup_row_errors(make_xlsx):
    path = make_xlsx([STACK_HEADER,
                      ["x", "TOP", 0.035, 5.8e7, None, None],
                      [2, "PP", None, None, None, 0.02],
                      [3, "BOT", 0.035, 5.8e7, None, None],
                      [], ["note: end of table"]], sheet="Stackup")
    issues = IssueCollector()
    stackup = read_stackup(path, issues)
    codes = _codes(issues)
    assert "E_STACK_LAYER_NUM" in codes
    assert codes.count("E_STACK_THICKNESS") == 1
    assert "E_STACK_DK" in codes
    assert [layer.number for layer in stackup.layers] == [2, 3]


def test_pwr_list_legacy_height_column(make_xlsx):
    path = make_xlsx([["PWR Name", "Layer Number", "GND Layer Number", "PWR Plane Width",
                       "PWR Plane Height"],
                      ["VDD", 5, 3, 60, 40]], sheet="PWR")
    issues = IssueCollector()
    rows = read_pwr_list(path, issues)
    assert "W_XL_COLUMN_IGNORED" in _codes(issues)
    assert not issues.has_errors()
    assert rows[0].width_mm == 60.0


def test_pwr_list_validation(make_xlsx):
    path = make_xlsx([["PWR Name", "Layer Number", "GND Layer Number", "Width (mm)"],
                      ["VDD", 5, 3, 60], ["VDD", 7, 9, 0], ["X", 4, 4, 10]], sheet="PWR")
    issues = IssueCollector()
    rows = read_pwr_list(path, issues)
    codes = _codes(issues)
    assert {"E_PWR_NAME_DUP", "E_PWR_DIM", "E_PWR_SAME_LAYER"} <= set(codes)
    assert len(rows) == 3


DECAP_HEADER = ["PWR Name", "Decap File Name", "Number of Decaps", "Distance to PAD (mm)",
                "Dummy Cap"]


def test_decap_dummy_values(make_xlsx):
    values = ["Yes", "no", "TRUE", "False", "1", "0", True, False, None]
    expected = [True, False, True, False, True, False, True, False, False]
    rows = [DECAP_HEADER] + [["VDD", "cap.mod", 2, 5, v] for v in values]
    path = make_xlsx(rows, sheet="Decaps")
    issues = IssueCollector()
    decaps = read_decap_list(path, issues)
    assert [d.dummy for d in decaps] == expected
    assert not issues.has_errors()


def test_decap_dummy_invalid(make_xlsx):
    path = make_xlsx([DECAP_HEADER, ["VDD", "cap.mod", 2, 5, "maybe"]], sheet="Decaps")
    issues = IssueCollector()
    read_decap_list(path, issues)
    err = [i for i in issues.issues if i.code == "E_XL_BOOL"]
    assert err and err[0].location == "Decaps!E2"


def test_decap_dummy_column_absent(make_xlsx):
    path = make_xlsx([DECAP_HEADER[:-1], ["VDD", "a.mod", 2, 5], ["VDD", "b.s2p", 1, 7.5]],
                     sheet="Decaps")
    issues = IssueCollector()
    decaps = read_decap_list(path, issues)
    assert [d.dummy for d in decaps] == [False, False]
    assert decaps[1].distance_mm == 7.5
    assert not issues.has_errors()


def test_decap_distance_and_count_errors(make_xlsx):
    path = make_xlsx([DECAP_HEADER + ["S2P Mode", "Subckt"],
                      ["VDD", "a.mod", 0, 0, "No", "shunt", "CAP1"],
                      ["VDD", "a.s2p", 1, 3, "No", "bogus", None]], sheet="Decaps")
    issues = IssueCollector()
    decaps = read_decap_list(path, issues)
    codes = _codes(issues)
    assert {"E_DECAP_DISTANCE", "E_DECAP_COUNT", "E_XL_MODE"} <= set(codes)
    assert decaps[0].s2p_mode == "shunt" and decaps[0].subckt == "CAP1"
