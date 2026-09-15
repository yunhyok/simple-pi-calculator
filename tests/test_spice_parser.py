"""DESIGN.md §8.6 — SPICE number/expression parser and .mod flattener."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from simple_pi_calculator.core.spice_expr import (SpiceExprError, evaluate_expression,
                                                  parse_spice_number)
from simple_pi_calculator.core.spice_parser import parse_spice_file, parse_spice_text
from simple_pi_calculator.errors import InputError, IssueCollector

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DATA = Path(__file__).resolve().parent / "data"


def parse(text: str, subckt: str | None = None):
    issues = IssueCollector()
    net = parse_spice_text(text, subckt, issues)
    return net, issues


def elems(net):
    return {e.name: e for e in net.elements}


def expect_error(text: str, code: str, subckt: str | None = None) -> InputError:
    issues = IssueCollector()
    with pytest.raises(InputError) as exc:
        parse_spice_text(text, subckt, issues)
    assert [i.code for i in exc.value.issues] == [code]
    assert code in issues.codes()
    return exc.value


# 1 -------------------------------------------------------------------------------------------------
def test_series_rlc_flat():
    net, issues = parse(""".SUBCKT CAP 1 2
R1 1 11 30m
L1 11 12 0.45nH
C1 12 2 100nF
.ENDS
""")
    e = elems(net)
    assert len(net.elements) == 3
    assert e["r1"].kind == "R" and e["r1"].value == pytest.approx(0.03, rel=1e-12)
    assert e["l1"].kind == "L" and e["l1"].value == pytest.approx(0.45e-9, rel=1e-12)
    assert e["c1"].kind == "C" and e["c1"].value == pytest.approx(100e-9, rel=1e-12)
    assert e["l1"].nodes == ("11", "12")
    assert (net.pin1, net.pin2, net.subckt_name) == ("1", "2", "cap")
    assert e["c1"].line == 4
    assert not issues.issues


# 2 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,value", [
    ("10u", 1e-5), ("10uF", 1e-5), ("0.45nH", 0.45e-9), ("30mOhm", 30e-3), ("5mOhm", 5e-3),
    ("1MEG", 1e6), ("1Meg", 1e6), ("1meg", 1e6), ("2.2nH", 2.2e-9), ("1e-9", 1e-9),
    ("3mil", 7.62e-5), ("10Ohm", 10.0), ("1F", 1e-15), ("1Farad", 1e-15), ("0.1", 0.1),
    ("1e3k", 1e6), ("1T", 1e12), ("1G", 1e9), ("1k", 1e3), ("1p", 1e-12), ("-2.5m", -2.5e-3),
    (".5n", 0.5e-9), ("10µF", 1e-5), ("10μF", 1e-5), ("+3", 3.0), ("1.", 1.0),
])
def test_number_suffixes(text, value):
    assert parse_spice_number(text) == pytest.approx(value, rel=1e-12)


@pytest.mark.parametrize("bad", ["", "abc", "n10", "1/2", "--1", "1e3*2"])
def test_number_rejects(bad):
    with pytest.raises(ValueError):
        parse_spice_number(bad)


def test_trailing_units_in_netlist():
    net, _ = parse(""".subckt cap a b
C1 a n1 10uF
L1 n1 n2 0.45nH
R1 n2 n3 30mOhm
R2 n3 b 1Meg
R3 a b 1MEG
C2 a b 1F
R4 a b 3mil
.ends
""")
    e = elems(net)
    assert e["c1"].value == pytest.approx(1e-5, rel=1e-12)
    assert e["l1"].value == pytest.approx(0.45e-9, rel=1e-12)
    assert e["r1"].value == pytest.approx(0.03, rel=1e-12)
    assert e["r2"].value == pytest.approx(1e6, rel=1e-12)
    assert e["r3"].value == pytest.approx(1e6, rel=1e-12)
    assert e["c2"].value == pytest.approx(1e-15, rel=1e-12)
    assert e["r4"].value == pytest.approx(7.62e-5, rel=1e-12)


# 3 -------------------------------------------------------------------------------------------------
def test_lexical_rules():
    text = """* full-line comment
   * indented comment
.SUBCKT Top  P  N
X1 (p mid) SUB
+ PARAMS : RV = 2 ; comment ; more
C1\tmid n2\t1n   $ dollar comment
C2 n2 n
+ C = 1n
R$1 n2 n 5  ; '$' inside a name is not a comment
.ENDS
.SUBCKT sub a b PARAMS: RV=1
R1 a b {RV*2}   ; braces
.ENDS
"""
    net, issues = parse(text)
    e = elems(net)
    assert net.subckt_name == "top"
    assert e["x1.r1"].value == pytest.approx(4.0)
    assert e["x1.r1"].nodes == ("p", "mid")
    assert e["c1"].value == pytest.approx(1e-9)
    assert e["c1"].nodes == ("mid", "n2")
    assert e["c2"].value == pytest.approx(1e-9)
    assert e["r$1"].value == pytest.approx(5.0)
    assert not issues.has_errors()


def test_case_insensitive_nodes_and_names():
    net, _ = parse(""".SUBCKT CAP Pin1 PIN2
r1 PIN1 Mid 1
R2 mid pin2 2
.ENDS cap
""")
    e = elems(net)
    assert e["r1"].nodes == ("pin1", "mid")
    assert e["r2"].nodes == ("mid", "pin2")


def test_latin1_and_utf8_files(tmp_path):
    p = tmp_path / "m.mod"
    p.write_bytes(b".SUBCKT CAP 1 2\n* caf\xe9 \xb5F\nC1 1 2 10\xb5F\n.ENDS\n")  # latin-1 µ
    net = parse_spice_file(p, None, IssueCollector())
    assert net.elements[0].value == pytest.approx(1e-5)
    p.write_text(".SUBCKT CAP 1 2\nC1 1 2 10µF\n.ENDS\n", encoding="utf-8")
    net = parse_spice_file(p, None, IssueCollector())
    assert net.elements[0].value == pytest.approx(1e-5)


# 4 -------------------------------------------------------------------------------------------------
def test_param_arithmetic():
    net, _ = parse(""".PARAM A=2 B={A*3+1} C='sqrt(B)*1n'
.SUBCKT CAP 1 2
C1 1 2 C
R1 1 2 {B}
.ENDS
""")
    assert net.params["b"] == pytest.approx(7.0)
    assert net.params["c"] == pytest.approx(2.6458e-9, rel=1e-4)
    assert elems(net)["c1"].value == pytest.approx(math.sqrt(7) * 1e-9, rel=1e-12)
    assert elems(net)["r1"].value == pytest.approx(7.0)


@pytest.mark.parametrize("expr,value", [
    ("1+2*3", 7.0), ("(1+2)*3", 9.0), ("2^3^2", 512.0), ("2**3", 8.0), ("-2^2", -4.0),
    ("2^-1", 0.5), ("10/4/5", 0.5), ("sqrt(16)", 4.0), ("abs(-3)", 3.0), ("exp(0)", 1.0),
    ("log(exp(2))", 2.0), ("log10(1000)", 3.0), ("pow(2,10)", 1024.0), ("min(3,4)", 3.0),
    ("max(3,4)", 4.0), ("pi", math.pi), ("2*PI", 2 * math.pi), ("1n*2", 2e-9), ("{1meg/2}", 5e5),
    ("'x*2'", 8.0), ("X+1", 5.0), ("-(1)", -1.0), ("1.5e3", 1500.0),
])
def test_expression_values(expr, value):
    assert evaluate_expression(expr, {"x": 4.0}) == pytest.approx(value, rel=1e-12)


@pytest.mark.parametrize("expr", [
    "1/0", "unknown*2", "sqrt(-1)", "log(0)", "foo(1)", "1+", "+-1", "(1", "1 2", "", "min(1)",
    "__import__('os')", "__import__('os').system('echo hi')", "a.b", "1;2", "[1]", "0^-1",
    "exp(1e6)",
])
def test_expression_errors(expr):
    with pytest.raises(SpiceExprError):
        evaluate_expression(expr, {"a": 1.0})


def test_unbraced_param_expression_with_parentheses():
    net, _ = parse(""".PARAM X = (1 + 2) * 2  Y=pow(X, 2)
.SUBCKT CAP 1 2
R1 1 2 Y
.ENDS
""")
    assert elems(net)["r1"].value == pytest.approx(36.0)


# 5 -------------------------------------------------------------------------------------------------
def test_nested_bundled_10uF():
    issues = IssueCollector()
    net = parse_spice_file(EXAMPLES / "cap_0603_10uF.mod", None, issues)
    e = elems(net)
    assert {"x_esl.l1", "x_esl.l2", "x_esl.r1", "c1", "r2", "rleak"} <= set(e)
    assert e["x_esl.l1"].value == pytest.approx(0.25e-9, rel=1e-12)
    assert e["x_esl.l2"].value == pytest.approx(0.25e-9, rel=1e-12)
    assert e["x_esl.r1"].value == pytest.approx(3e-3, rel=1e-12)
    assert e["c1"].value == pytest.approx(10e-6, rel=1e-12)
    assert e["rleak"].value == pytest.approx(100e6, rel=1e-12)
    assert e["x_esl.l1"].nodes == ("pin1", "x_esl.m1")
    assert e["x_esl.r1"].nodes == ("x_esl.m2", "n1")
    k = [x for x in net.elements if x.kind == "K"]
    assert len(k) == 1 and k[0].coupled == ("x_esl.l1", "x_esl.l2") and k[0].value == 0.0
    assert net.subckt_name == "cap_0603_10uf"
    assert not issues.issues


def test_override_and_default_scoping():
    net, issues = parse(""".PARAM G=5
.SUBCKT TOP 1 2 PARAMS: P=3
X1 1 m CHILD PARAMS: A={P*2}
X2 m 2 CHILD
.ENDS
.SUBCKT CHILD a b PARAMS: A=1 B={A+G}
.PARAM LOC={B*10}
R1 a b {LOC}
.ENDS
""")
    e = elems(net)
    # defaults evaluated in caller env: B = 1 + 5 = 6, override A=6 does not change B
    assert e["x1.r1"].value == pytest.approx(60.0)
    assert e["x2.r1"].value == pytest.approx(60.0)
    assert e["x1.r1"].nodes == ("1", "m")


def test_unknown_override_warns():
    net, issues = parse(""".SUBCKT TOP 1 2
X1 1 2 CHILD PARAMS: Q=2
.ENDS
.SUBCKT CHILD a b PARAMS: A=1
R1 a b {A+Q}
.ENDS
""")
    assert "W_SPICE_UNKNOWN_PARAM" in issues.codes()
    assert elems(net)["x1.r1"].value == pytest.approx(3.0)


def test_param_redefinition_error():
    expect_error(""".SUBCKT TOP 1 2 PARAMS: A=1
.PARAM A=2
R1 1 2 A
.ENDS
""", "E_SPICE_PARAM_REDEF")


def test_nested_definition_is_global():
    net, _ = parse(""".SUBCKT TOP 1 2
X1 1 2 INNER
.SUBCKT INNER a b
R1 a b 7
.ENDS INNER
.ENDS TOP
""")
    assert elems(net)["x1.r1"].value == 7.0


def test_m_multiplier_and_ignored_options():
    net, issues = parse(""".SUBCKT CAP 1 2
R1 1 2 10 M=2
L1 1 2 L=4n m=4
C1 1 2 1n M=3 IC=0
.ENDS
""")
    e = elems(net)
    assert e["r1"].value == pytest.approx(5.0)
    assert e["l1"].value == pytest.approx(1e-9)
    assert e["c1"].value == pytest.approx(3e-9)
    assert issues.codes().count("W_SPICE_OPTION_IGNORED") == 1


# 6 -------------------------------------------------------------------------------------------------
def test_k_parsing_pair_and_multi():
    net, issues = parse(""".SUBCKT CAP 1 2
L1 1 a 1n
L2 a b 1n
L3 b 2 1n
K1 L1 L2 0.5
K2 L1 L2 L3 0.2
.ENDS
""")
    k1 = [e for e in net.elements if e.name == "k1"]
    k2 = [e for e in net.elements if e.name == "k2"]
    assert len(k1) == 1 and k1[0].coupled == ("l1", "l2") and k1[0].value == 0.5
    assert len(k2) == 3
    assert {e.coupled for e in k2} == {("l1", "l2"), ("l1", "l3"), ("l2", "l3")}
    assert all(e.value == pytest.approx(0.2) for e in k2)


def test_k_unity_warning():
    _, issues = parse(".SUBCKT CAP 1 2\nL1 1 a 1n\nL2 a 2 1n\nK1 L1 L2 1\n.ENDS\n")
    assert "W_K_UNITY" in issues.codes()


def test_k_must_reference_same_scope():
    expect_error(""".SUBCKT TOP 1 2
X1 1 m SUB
L2 m 2 1n
K1 L1 L2 0.5
.ENDS
.SUBCKT SUB a b
L1 a b 1n
.ENDS
""", "E_SPICE_K")


# 7 -------------------------------------------------------------------------------------------------
def test_error_unknown_subckt():
    err = expect_error(".SUBCKT TOP 1 2\nX1 1 2 NOPE\n.ENDS\n", "E_SPICE_UNKNOWN_SUBCKT")
    assert err.issues[0].location == "line 2"


def test_error_voltage_source():
    err = expect_error(".SUBCKT TOP 1 2\nR1 1 2 1\nV1 1 2 1\n.ENDS\n", "E_SPICE_UNSUPPORTED")
    assert err.issues[0].location == "line 3"
    assert "V1" in err.issues[0].message


def test_error_include():
    err = expect_error(".INCLUDE other.lib\n.SUBCKT TOP 1 2\nR1 1 2 1\n.ENDS\n", "E_SPICE_UNSUPPORTED")
    assert err.issues[0].location == "line 1"


def test_error_missing_ends():
    err = expect_error("* c\n.SUBCKT TOP 1 2\nR1 1 2 1\n", "E_SPICE_SYNTAX")
    assert err.issues[0].location == "line 2"
    assert ".ENDS" in err.issues[0].message


def test_error_three_pin_top():
    expect_error(".SUBCKT TOP 1 2 3\nR1 1 2 1\nR2 2 3 1\n.ENDS\n", "E_SPICE_PIN_COUNT")


def test_error_same_pins():
    expect_error(".SUBCKT TOP 1 1\nR1 1 1 1\n.ENDS\n", "E_SPICE_PIN_COUNT")


def test_error_recursion():
    err = expect_error(".SUBCKT A 1 2\nX1 1 2 A\n.ENDS\n", "E_SPICE_RECURSION")
    assert err.issues[0].location == "line 2"


def test_error_deep_nesting():
    lines = [".SUBCKT S0 a b", "X1 a b S1", ".ENDS"]
    for i in range(1, 25):
        lines += [f".SUBCKT S{i} a b", f"X1 a b S{i + 1}" if i < 24 else "R1 a b 1", ".ENDS"]
    expect_error("\n".join(lines), "E_SPICE_RECURSION")


def test_error_unknown_param():
    err = expect_error(".SUBCKT TOP 1 2\nR1 1 2 {RX*2}\n.ENDS\n", "E_SPICE_EXPR")
    assert err.issues[0].location == "line 2"


def test_error_division_by_zero():
    expect_error(".PARAM Z=0\n.SUBCKT TOP 1 2\nR1 1 2 {1/Z}\n.ENDS\n", "E_SPICE_EXPR")


def test_error_k_greater_than_one():
    err = expect_error(".SUBCKT TOP 1 2\nL1 1 a 1n\nL2 a 2 1n\nK1 L1 L2 1.2\n.ENDS\n", "E_SPICE_K")
    assert err.issues[0].location == "line 4"


def test_error_no_subckt():
    expect_error("* nothing\nR1 1 2 3\n", "E_SPICE_NO_SUBCKT")


def test_error_negative_values():
    expect_error(".SUBCKT TOP 1 2\nC1 1 2 -1n\n.ENDS\n", "E_SPICE_VALUE")
    expect_error(".SUBCKT TOP 1 2\nL1 1 2 -1n\n.ENDS\n", "E_SPICE_VALUE")
    _, issues = parse(".SUBCKT TOP 1 2\nR1 1 2 -1\n.ENDS\n")
    assert "W_SPICE_NEGATIVE_R" in issues.codes()


def test_ignored_statements_warn():
    _, issues = parse(".OPTIONS GMIN=1e-12\n.SUBCKT TOP 1 2\n.MODEL X R\nR1 1 2 1\n.ENDS\n.END\n")
    assert issues.codes().count("W_SPICE_IGNORED") == 2


# 8 -------------------------------------------------------------------------------------------------
TWO_TOPS = """.SUBCKT THREE a b c
R1 a b 1
R2 b c 1
.ENDS
.SUBCKT FIRST 1 2
R1 1 2 1
.ENDS
.SUBCKT SECOND 1 2
R1 1 2 2
.ENDS
"""


def test_top_selection_multi():
    net, issues = parse(TWO_TOPS)
    assert net.subckt_name == "first"
    assert "W_SPICE_MULTI_TOP" in issues.codes()


def test_top_selection_explicit():
    net, issues = parse(TWO_TOPS, subckt="Second")
    assert net.subckt_name == "second"
    assert elems(net)["r1"].value == 2.0
    assert "W_SPICE_MULTI_TOP" not in issues.codes()
    expect_error(TWO_TOPS, "E_SPICE_UNKNOWN_SUBCKT", subckt="missing")


def test_top_selection_skips_instantiated():
    net, issues = parse(".SUBCKT CHILD a b\nR1 a b 1\n.ENDS\n.SUBCKT TOP 1 2\nX1 1 2 CHILD\n.ENDS\n")
    assert net.subckt_name == "top"
    assert "W_SPICE_MULTI_TOP" not in issues.codes()


# 9 -------------------------------------------------------------------------------------------------
def test_global_ground_merged():
    net, issues = parse(""".SUBCKT TOP p n
X1 p n SUB
R1 p 0 5
.ENDS
.SUBCKT SUB a b
C1 a gnd 1n
.ENDS
""")
    e = elems(net)
    assert e["r1"].nodes == ("p", "n")
    assert e["x1.c1"].nodes == ("p", "n")
    assert issues.codes().count("W_SPICE_GLOBAL_GND") == 2


def test_floating_warning():
    _, issues = parse(".SUBCKT TOP 1 2\nR1 1 2 1\nR2 a b 1\n.ENDS\n")
    assert "W_MNA_FLOATING" in issues.codes()
    _, issues = parse(".SUBCKT TOP 1 2\nR1 1 a 1\nC1 a 2 1n\n.ENDS\n")
    assert "W_MNA_FLOATING" not in issues.codes()


# 10 ------------------------------------------------------------------------------------------------
def test_injection_rejected():
    expect_error(".SUBCKT TOP 1 2\nR1 1 2 {__import__('os')}\n.ENDS\n", "E_SPICE_EXPR")
    expect_error(".PARAM A='__import__(\"os\").system(\"x\")'\n.SUBCKT TOP 1 2\nR1 1 2 A\n.ENDS\n",
                 "E_SPICE_EXPR")


# vendor-style fixture ------------------------------------------------------------------------------
def test_vendor_style_fixture_parses():
    issues = IssueCollector()
    net = parse_spice_file(DATA / "vendor_style_0402_104.mod", None, issues)
    e = elems(net)
    assert net.subckt_name == "syn0402x7r104"
    assert (net.pin1, net.pin2) == ("port1", "port2")
    assert e["x1.la"].value == pytest.approx(180e-12)
    assert e["x1.lb"].value == pytest.approx(144e-12)
    assert e["x1.lc"].value == pytest.approx(90e-12)
    assert e["x1.rb"].value == pytest.approx(25e-3)
    assert e["c1"].value == pytest.approx(98e-9)
    assert len([x for x in net.elements if x.kind == "K"]) == 3
    assert issues.codes() == ["W_SPICE_OPTION_IGNORED"]
