"""DESIGN.md §8.4 — plane height, port placement, dummy-cap ports."""

from __future__ import annotations

import numpy as np
import pytest

from simple_pi_calculator.core.cavity import cluster_port_width
from simple_pi_calculator.core.engine import enabled_rows_for_pwr
from simple_pi_calculator.core.placement import (DecapGroupGeom, caps_per_port_for_row,
                                                 place_ports, plane_height, ports_for_row)
from simple_pi_calculator.core.types import DecapRow
from simple_pi_calculator.errors import InputError, IssueCollector

MM = 1e-3
#: §8.4 states "D_drill = 0.2 mm ⇒ w = 0.223690 mm" and its coordinates are computed with that
#: rounded width. The exact GMD rule gives 0.2236886 mm (1.4 nm smaller, §8.3 #11), which shifts
#: some coordinates by up to 1.4e-9 m — just above the abs 1e-9 m tolerance. The tests therefore
#: pass the documented width to place_ports (it takes widths as inputs) and check the exact rule
#: separately in test_port_width.
W_DEC = 0.223690 * MM
ABS = 1e-9


def place(width_mm, rows, issues=None):
    issues = issues if issues is not None else IssueCollector()
    groups = [DecapGroupGeom(n, d * MM, dummy) for n, d, dummy in rows]
    return place_ports(width_mm * MM, groups, W_DEC, W_DEC, issues, "PWR:T"), issues


def test_port_width():
    assert cluster_port_width(1, 0.2 * MM, 1.0 * MM) == pytest.approx(W_DEC, abs=2e-9)
    # coordinates with the exact rule differ from the documented ones by < 3e-9 m
    exact = cluster_port_width(1, 0.2 * MM, 1.0 * MM)
    pl = place_ports(60 * MM, [DecapGroupGeom(10, 8 * MM), DecapGroupGeom(4, 15 * MM)], exact,
                     exact, IssueCollector(), "PWR:T")
    assert pl.xy_m[1, 0] == pytest.approx(8.500660 * MM, abs=3e-9)


def test_plane_height():
    assert plane_height(60 * MM, [8 * MM, 15 * MM]) == pytest.approx((21 * MM, 15 * MM))
    h, d_ref = plane_height(30 * MM, [])
    assert h == pytest.approx(30 * MM) and d_ref == pytest.approx(21.428571 * MM, abs=1e-9)
    pl, issues = place(30, [])
    assert pl.xy_m[0] == pytest.approx([15 * MM, 4.285714 * MM], abs=ABS)
    assert pl.height_m == pytest.approx(30 * MM)
    assert pl.n_ports == 1 and pl.group_index.size == 0


@pytest.mark.parametrize("n, dummy, ports, caps", [
    (1, False, 1, [1]), (1, True, 1, [1]), (2, True, 1, [2]), (4, True, 2, [2, 2]),
    (5, True, 3, [2, 2, 1]), (5, False, 5, [1] * 5), (7, True, 4, [2, 2, 2, 1]),
])
def test_ports_and_caps(n, dummy, ports, caps):
    assert ports_for_row(n, dummy) == ports
    assert caps_per_port_for_row(n, dummy) == caps


def test_dummy_single_info_and_invariant():
    issues = IssueCollector()
    assert caps_per_port_for_row(1, True, issues) == [1]
    assert issues.codes() == ["I_DUMMY_SINGLE"]
    _, issues = place(30, [(1, 5, True)])
    assert "I_DUMMY_SINGLE" in issues.codes()
    for n in range(1, 51):
        for dummy in (False, True):
            caps = caps_per_port_for_row(n, dummy)
            assert sum(caps) == n and len(caps) == ports_for_row(n, dummy)


def test_vdd_core_example():
    pl, issues = place(60, [(10, 8, False), (4, 15, False)])
    assert not issues.has_errors()
    assert pl.height_m == pytest.approx(21 * MM) and pl.d_ref_m == pytest.approx(15 * MM)
    xy = pl.xy_m
    assert xy[0] == pytest.approx([30 * MM, 3 * MM], abs=ABS)
    m_x = W_DEC / 2 + 6 * MM
    assert m_x == pytest.approx(6.111845 * MM, abs=1e-9)
    assert 60 * MM - 2 * m_x == pytest.approx(47.776310 * MM, abs=1e-9)
    row0 = xy[1:11]
    assert np.allclose(row0[:, 1], 11 * MM, atol=ABS)
    assert row0[0, 0] == pytest.approx(8.500660 * MM, abs=ABS)
    assert np.allclose(np.diff(row0[:, 0]), 4.777631 * MM, atol=ABS)
    row1 = xy[11:]
    assert np.allclose(row1[:, 1], 18 * MM, atol=ABS)
    assert row1[:, 0] == pytest.approx(np.array([12.083884, 24.027961, 35.972039, 47.916116]) * MM,
                                       abs=ABS)
    assert pl.group_index.tolist() == [0] * 10 + [1] * 4
    assert pl.caps_per_port.tolist() == [1] * 14
    assert pl.port_widths_m.shape == (15,)


def test_vdd_io_example_dummy():
    pl, _ = place(30, [(4, 5, True), (1, 10, False)])
    assert pl.height_m == pytest.approx(14 * MM)
    assert pl.xy_m[0] == pytest.approx([15 * MM, 2 * MM], abs=ABS)
    expect = np.array([[9.055922, 7], [20.944078, 7], [15, 12]]) * MM
    assert pl.xy_m[1:] == pytest.approx(expect, abs=ABS)
    assert pl.caps_per_port.tolist() == [2, 2, 1]
    assert pl.group_index.tolist() == [0, 0, 1]


def test_multi_row():
    pl, issues = place(10, [(100, 5, False)])
    assert pl.d_ref_m == pytest.approx(5 * MM) and pl.height_m == pytest.approx(7 * MM)
    xy = pl.xy_m[1:]
    ys = np.unique(np.round(xy[:, 1] / MM, 6))
    assert ys == pytest.approx([5.776310, 6.000000, 6.223690], abs=1e-6)
    counts = [int(np.sum(np.isclose(xy[:, 1], y * MM, atol=1e-9))) for y in ys]
    assert counts == [34, 34, 32]
    assert xy[0, 0] == pytest.approx(1.226203 * MM, abs=1e-9)
    # DESIGN.md §8.4 #5 prints 1.233355 mm, but m_x + 0.5·L_x/32 = 1.111845 + 0.1215049 =
    # 1.2333499 mm; the printed value adds the rounded half-cell 0.121510 (rounding artefact,
    # 5.5e-9 m). The exact value is asserted at the document's abs 1e-9 m tolerance.
    assert xy[68, 0] == pytest.approx(1.233350 * MM, abs=1e-9)
    assert "W_PORT_OVERLAP" not in issues.codes()
    assert np.all(xy[:, 0] >= W_DEC / 2) and np.all(xy[:, 0] <= 10 * MM - W_DEC / 2)


def test_width_too_small():
    with pytest.raises(InputError) as exc:
        place(0.5, [(1, 5, False)])
    assert "E_PWR_WIDTH_TOO_SMALL" in [i.code for i in exc.value.issues]
    pl, issues = place(0.6, [(1, 5, False)])
    assert not issues.has_errors()
    assert 0.6 * MM - 2 * (W_DEC / 2 + 0.06 * MM) == pytest.approx(0.2563 * MM, abs=1e-7)


def test_dref_too_small_and_distance():
    with pytest.raises(InputError) as exc:
        place(30, [(1, 0.5, False)])
    assert [i.code for i in exc.value.issues] == ["E_DREF_TOO_SMALL"]
    with pytest.raises(InputError) as exc:
        place(30, [(1, 0.0, False)])
    assert "E_DECAP_DISTANCE" in [i.code for i in exc.value.issues]


def test_too_close_and_overlap():
    _, issues = place(30, [(1, 0.2, False), (1, 5, False)])
    assert "W_DECAP_TOO_CLOSE" in issues.codes()
    _, issues = place(60, [(10, 5.0, False), (10, 5.1, False)])
    assert "W_PORT_OVERLAP" in issues.codes()
    _, issues = place(60, [(10, 5.0, False), (10, 8.0, False)])
    assert "W_PORT_OVERLAP" not in issues.codes()


def test_clipped():
    pl, issues = place(5, [(1000, 5, False)])
    assert "W_DECAP_CLIPPED" in issues.codes()
    y = pl.xy_m[1:, 1]
    assert np.all(y >= W_DEC / 2 - 1e-15) and np.all(y <= pl.height_m - W_DEC / 2 + 1e-15)
    assert pl.n_decap_ports == 1000


def test_disabled_rows_ignored_for_dref():
    rows = [DecapRow("VDD", "a.mod", 2, 8.0), DecapRow("VDD", "b.mod", 2, 20.0, enabled=False),
            DecapRow("OTHER", "c.mod", 2, 30.0)]
    enabled = enabled_rows_for_pwr(rows, "VDD")
    assert [r.model_file for r in enabled] == ["a.mod"]
    h, d_ref = plane_height(30 * MM, [r.distance_m for r in enabled])
    assert d_ref == pytest.approx(8 * MM) and h == pytest.approx(11.2 * MM)
