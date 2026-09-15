"""Shared pytest fixtures (DESIGN.md §8).

Excel fixtures are generated in ``tmp_path`` with openpyxl (no binary fixtures except the bundled
examples, §8).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Sequence

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # allow running without an editable install
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from simple_pi_calculator.core.stackup import Layer, Stackup  # noqa: E402
from simple_pi_calculator.errors import IssueCollector  # noqa: E402

#: §4.2 example stack-up rows: (number, name, thickness mm, σ S/m or None, Dk, Df)
EXAMPLE_STACKUP_ROWS: list[tuple[int, str, float, float | None, float, float]] = [
    (1, "TOP", 0.035, 5.8e7, 4.2, 0.02),
    (2, "PP1", 0.1, None, 4.2, 0.02),
    (3, "GND1", 0.035, 5.8e7, 4.3, 0.018),
    (4, "CORE1", 0.1, None, 4.3, 0.018),
    (5, "PWR1", 0.035, 5.8e7, 4.3, 0.018),
    (6, "PP2", 0.8, None, 4.4, 0.02),
    (7, "PWR2", 0.035, 5.8e7, 4.3, 0.018),
    (8, "CORE2", 0.1, None, 4.3, 0.018),
    (9, "GND2", 0.035, 5.8e7, 4.2, 0.02),
    (10, "PP3", 0.1, None, 4.2, 0.02),
    (11, "BOTTOM", 0.035, 5.8e7, 4.2, 0.02),
]


def build_stackup(rows: Sequence[tuple[int, str, float, float | None, float | None,
                                      float | None]]) -> Stackup:
    """Stack-up from mm-valued tuples."""
    return Stackup(tuple(Layer(n, name, t * 1e-3, sigma, dk, df)
                         for n, name, t, sigma, dk, df in rows))


@pytest.fixture
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def examples_dir() -> Path:
    return ROOT / "examples"


@pytest.fixture
def example_project_path(examples_dir: Path) -> Path:
    return examples_dir / "example_project.spical.json"


@pytest.fixture
def example_stackup() -> Stackup:
    """The bundled 11-layer stack-up of §4.2, built directly (no Excel)."""
    return build_stackup(EXAMPLE_STACKUP_ROWS)


@pytest.fixture
def issues() -> IssueCollector:
    return IssueCollector()


@pytest.fixture
def make_xlsx(tmp_path: Path) -> Callable[..., Path]:
    """Factory: ``make_xlsx(rows, name="t.xlsx", sheet="Sheet1", extra_sheets=None)``.

    ``rows`` is a list of row lists written from A1; ``extra_sheets`` maps sheet name → rows and
    is written *before* the main sheet.
    """
    import openpyxl

    def _make(rows: Sequence[Sequence[object]], name: str = "table.xlsx", sheet: str = "Sheet1",
              extra_sheets: dict[str, Sequence[Sequence[object]]] | None = None) -> Path:
        wb = openpyxl.Workbook()
        first = wb.active
        if extra_sheets:
            first.title = "tmp_placeholder"
            for extra_name, extra_rows in extra_sheets.items():
                ws = wb.create_sheet(extra_name)
                for r in extra_rows:
                    ws.append(list(r))
            main = wb.create_sheet(sheet)
            wb.remove(first)
        else:
            main = first
            main.title = sheet
        for r in rows:
            main.append(list(r))
        path = tmp_path / name
        wb.save(path)
        return path

    return _make
