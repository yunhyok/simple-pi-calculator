"""Shared, Qt-free input row types (DESIGN.md §4.2–§4.4, §4.7, §5.2, §5.3).

DESIGN.md §5.3 references ``PwrRow`` and ``DecapRow`` (returned by ``io.excel_import`` and used by
the GUI tables and ``core.engine.ProjectInputs``) without assigning them to a module; they live
here so that ``core`` and ``io`` can share them without import cycles.

The rows are **mm-valued** (they mirror the project JSON, §4.7) and expose SI convenience
properties (``width_m``, ``distance_m``, …) for the engine.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from simple_pi_calculator.constants import S2P_EXTENSIONS, SPICE_EXTENSIONS
from simple_pi_calculator.core.stackup import Layer, Stackup
from simple_pi_calculator.core.units import MM


@dataclass
class LayerRow:
    """Stack-up row as stored in the project file (``stackup.layers[]``, §4.7)."""

    number: int
    name: str = ""
    thickness_mm: float = 0.0
    conductivity_s_per_m: float | None = None
    dk: float | None = None
    df: float | None = None

    def to_layer(self) -> Layer:
        """SI :class:`~simple_pi_calculator.core.stackup.Layer` (§1.6)."""
        return Layer(number=self.number, name=self.name, thickness_m=self.thickness_mm * MM,
                     conductivity=self.conductivity_s_per_m, dk=self.dk, df=self.df)

    @classmethod
    def from_layer(cls, layer: Layer) -> "LayerRow":
        return cls(number=layer.number, name=layer.name, thickness_mm=layer.thickness_m / MM,
                   conductivity_s_per_m=layer.conductivity, dk=layer.dk, df=layer.df)


def stackup_from_rows(rows: Iterable[LayerRow]) -> Stackup:
    """Build a sorted SI :class:`Stackup` from mm-valued rows."""
    return Stackup(tuple(row.to_layer() for row in rows))


def rows_from_stackup(stackup: Stackup) -> list[LayerRow]:
    return [LayerRow.from_layer(layer) for layer in stackup.layers]


@dataclass
class PwrRow:
    """PWR list row (§4.3, ``pwr.rows[]`` of §4.7). The plane height is derived, never stored."""

    name: str
    pwr_layer: int
    gnd_layer: int
    width_mm: float
    enabled: bool = True

    @property
    def width_m(self) -> float:
        return self.width_mm * MM


S2pMode = Literal["series", "shunt"]


@dataclass
class DecapRow:
    """Decap assignment row (§4.4, ``decaps.rows[]`` of §4.7)."""

    pwr_name: str
    model_file: str
    count: int
    distance_mm: float
    dummy: bool = False
    subckt: str | None = None
    s2p_mode: S2pMode | None = None
    enabled: bool = True

    @property
    def distance_m(self) -> float:
        return self.distance_mm * MM

    @property
    def path(self) -> str:
        """Alias of ``model_file`` (name used in ``ProjectInputs.decap_rows``, §5.2)."""
        return self.model_file


# ---------------------------------------------------------------------------------------------
# Model-file helpers (§4.4)
# ---------------------------------------------------------------------------------------------
def decap_file_kind(path: str) -> Literal["spice", "s2p"] | None:
    """``"spice"`` for .mod/.lib/.sp/.cir/.sub/.inc, ``"s2p"`` for .s2p, else ``None``
    (caller emits ``E_DECAP_FILE_TYPE``)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in SPICE_EXTENSIONS:
        return "spice"
    if ext in S2P_EXTENSIONS:
        return "s2p"
    return None


def model_path_candidates(model_file: str, search_dirs: Sequence[str | None]) -> list[str]:
    """Candidate absolute paths in the resolution order of §4.4.

    ``search_dirs`` is the ordered list (Excel file folder, project folder, ``model_search_dir``);
    ``None`` entries are skipped. An absolute ``model_file`` is its own single candidate.
    """
    text = os.path.expanduser(model_file.strip())
    if os.path.isabs(text):
        return [os.path.normpath(text)]
    out: list[str] = []
    for folder in search_dirs:
        if folder:
            out.append(os.path.normpath(os.path.join(folder, text)))
    return out


def resolve_model_path(model_file: str, excel_dir: str | None = None,
                       project_dir: str | None = None,
                       search_dir: str | None = None) -> str | None:
    """First existing file among the §4.4 candidates, or ``None`` (→ ``E_DECAP_FILE_NOT_FOUND``)."""
    if not model_file or not model_file.strip():
        return None
    for candidate in model_path_candidates(model_file, [excel_dir, project_dir, search_dir]):
        if os.path.isfile(candidate):
            return candidate
    return None


def ports_for_count(count: int, dummy: bool) -> int:
    """Number of via sets P_k of a decap row (§2.5.2); convenience for GUI derived columns."""
    return int(math.ceil(count / 2)) if dummy else int(count)


__all__ = [
    "LayerRow",
    "PwrRow",
    "DecapRow",
    "S2pMode",
    "stackup_from_rows",
    "rows_from_stackup",
    "decap_file_kind",
    "model_path_candidates",
    "resolve_model_path",
    "ports_for_count",
]
