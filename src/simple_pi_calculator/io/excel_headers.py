"""Fuzzy Excel header normalisation and column matching (DESIGN.md §4.1–§4.4, §5.3).

Pure functions, no openpyxl dependency: they operate on already-read cell values so that they can
be unit-tested without workbooks.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from simple_pi_calculator.errors import IssueCollector

ColumnKind = Literal["int", "float", "length", "str", "mode", "bool"]
Predicate = Callable[[str, "str | None"], bool]

#: Header rows scanned for header detection (§4.1)
HEADER_SCAN_ROWS: int = 20

_UNIT_GROUP_RE = re.compile(r"\(([^()]*)\)")
_BASE_KEEP_RE = re.compile(r"[^a-z0-9/#%]")
_UNIT_KEEP_RE = re.compile(r"[^a-z0-9/#%.]")


@dataclass(frozen=True)
class ColumnRule:
    """One column rule (§5.3). ``predicate(base, unit)`` decides whether a header cell matches.

    ``label`` is the human-readable column name used in messages (e.g. ``"Df"``).
    """

    field: str
    required: bool
    predicate: Predicate
    kind: ColumnKind
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.field


def _prepare(text: object) -> str:
    s = unicodedata.normalize("NFKC", str(text)).casefold()
    for src, dst in (("μ", "u"), ("µ", "u"), ("δ", "d"), ("ε", "e"), ("[", "("), ("]", ")")):
        s = s.replace(src, dst)
    return s


def normalize_header(text: object) -> tuple[str, str | None]:
    """Normalise a header cell to ``(base, unit)`` (§4.1).

    str → NFKC → casefold → µ/μ→u, δ→d, ε→e, ``[]``→``()``; the text inside the last parenthesised
    group is the unit (``"Thickness (mm)"`` → ``("thickness", "mm")``); the base keeps only
    ``[a-z0-9/#%]`` (``"Layer No."`` → ``("layerno", None)``, ``"tan δ"`` → ``("tand", None)``).
    """
    if text is None:
        return "", None
    s = _prepare(text)
    groups = _UNIT_GROUP_RE.findall(s)
    unit: str | None = None
    for group in reversed(groups):
        cleaned = _UNIT_KEEP_RE.sub("", group)
        if cleaned:
            unit = cleaned
            break
    base = _BASE_KEEP_RE.sub("", _UNIT_GROUP_RE.sub(" ", s))
    return base, unit


def normalize_sheet_name(name: object) -> str:
    """Sheet-name normalisation for table keyword search (§4.1)."""
    return _BASE_KEEP_RE.sub("", _prepare(name))


# ---------------------------------------------------------------------------------------------
# Predicate helpers
# ---------------------------------------------------------------------------------------------
def _has(base: str, *words: str) -> bool:
    return any(w in base for w in words)


# -- stack-up (§4.2) ---------------------------------------------------------------------------
def _p_layer_name(base: str, unit: str | None) -> bool:
    return "layer" in base and "name" in base


def _p_layer_number(base: str, unit: str | None) -> bool:
    return ("layer" in base and _has(base, "number", "num", "no", "#", "idx", "index")) or base in {
        "layer", "layerno", "no", "#"}


def _p_thickness(base: str, unit: str | None) -> bool:
    # "thk" is accepted in addition to §4.2 so that the listed test header "Thk(mil)" maps.
    return "thick" in base or base in {"t", "th", "thk"}


def _p_conductivity(base: str, unit: str | None) -> bool:
    return _has(base, "conduct", "sigma") or unit == "s/m"


#: "Dk@1GHz" normalises to "dk1ghz", "Df 10 GHz" to "df10ghz": accept a frequency qualifier
_DK_QUALIFIED_RE = re.compile(r"^(dk|er|epsr)\d")
_DF_QUALIFIED_RE = re.compile(r"^(df|tand|tandelta)\d")


def _p_dk(base: str, unit: str | None) -> bool:
    return (base in {"dk", "er", "epsr", "eps"} or _has(base, "permittivity", "dielectricconstant")
            or bool(_DK_QUALIFIED_RE.match(base)))


def _p_df(base: str, unit: str | None) -> bool:
    return (base in {"df", "tand", "tandelta", "losstangent"} or _has(base, "dissipation", "losstan")
            or bool(_DF_QUALIFIED_RE.match(base)))


STACKUP_RULES: tuple[ColumnRule, ...] = (
    ColumnRule("layer_name", False, _p_layer_name, "str", "Layer Name"),
    ColumnRule("layer_number", True, _p_layer_number, "int", "Layer Number"),
    ColumnRule("thickness", True, _p_thickness, "length", "Thickness"),
    ColumnRule("conductivity", True, _p_conductivity, "float", "Conductivity"),
    ColumnRule("dk", True, _p_dk, "float", "Dk"),
    ColumnRule("df", True, _p_df, "float", "Df"),
)


# -- PWR list (§4.3) ---------------------------------------------------------------------------
def _p_gnd_layer(base: str, unit: str | None) -> bool:
    return _has(base, "gnd", "ground") and "layer" in base


def _p_pwr_layer(base: str, unit: str | None) -> bool:
    return "layer" in base


def _p_pwr_name(base: str, unit: str | None) -> bool:
    return (_has(base, "pwr", "power", "net") and "name" in base) or base in {"pwr", "net", "rail"}


def _p_width(base: str, unit: str | None) -> bool:
    return "width" in base or base in {"w", "x"}


def _p_n_pads(base: str, unit: str | None) -> bool:
    """``Number of PADs``, ``PAD Count``, ``# PADs``, ``PADs``, ``No. of PADs``, ``N PADs`` …"""
    if "pads" in base:
        return True
    return "pad" in base and _has(base, "count", "number", "num", "qty", "quantity", "#")


def pwr_ignored_column(base: str, unit: str | None) -> bool:
    """Legacy PWR-list columns ignored with ``W_XL_COLUMN_IGNORED`` (§4.3): plane height/length and
    PAD position columns (but not the PAD count column, schema 3)."""
    if _p_n_pads(base, unit):
        return False
    return _has(base, "height", "length", "pad")


PWR_RULES: tuple[ColumnRule, ...] = (
    ColumnRule("n_pads", False, _p_n_pads, "int", "Number of PADs"),
    ColumnRule("gnd_layer", True, _p_gnd_layer, "int", "GND Layer Number"),
    ColumnRule("pwr_layer", True, _p_pwr_layer, "int", "Layer Number"),
    ColumnRule("pwr_name", True, _p_pwr_name, "str", "PWR Name"),
    ColumnRule("width", True, _p_width, "length", "PWR Plane Width"),
)


# -- decap list (§4.4) -------------------------------------------------------------------------
def _p_subckt(base: str, unit: str | None) -> bool:
    return _has(base, "subckt", "subcircuit")


def _p_s2p_mode(base: str, unit: str | None) -> bool:
    # "mode" must not match "model" (e.g. "Model File"), otherwise the model_file column would be
    # consumed by this earlier rule.
    return _has(base, "s2p", "config") or ("mode" in base.replace("model", ""))


def _p_dummy(base: str, unit: str | None) -> bool:
    return "dummy" in base


def _p_distance(base: str, unit: str | None) -> bool:
    return "dist" in base


def _p_count(base: str, unit: str | None) -> bool:
    return _has(base, "number", "count", "qty", "quantity", "#") or base == "n"


def _p_decap_pwr_name(base: str, unit: str | None) -> bool:
    return _has(base, "pwr", "power", "net", "rail")


def _p_model_file(base: str, unit: str | None) -> bool:
    return _has(base, "file", "model", "decap")


DECAP_RULES: tuple[ColumnRule, ...] = (
    ColumnRule("subckt", False, _p_subckt, "str", "Subckt"),
    ColumnRule("s2p_mode", False, _p_s2p_mode, "mode", "S2P Mode"),
    ColumnRule("dummy", False, _p_dummy, "bool", "Dummy Cap"),
    ColumnRule("distance", True, _p_distance, "length", "Distance to PAD"),
    ColumnRule("count", True, _p_count, "int", "Number of Decaps"),
    ColumnRule("pwr_name", True, _p_decap_pwr_name, "str", "PWR Name"),
    ColumnRule("model_file", True, _p_model_file, "str", "Decap File Name"),
)


# ---------------------------------------------------------------------------------------------
# Matching (§4.1)
# ---------------------------------------------------------------------------------------------
def column_letter(col_index0: int) -> str:
    """0-based column index → Excel letters (``0`` → ``A``)."""
    n = col_index0 + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def cell_ref(row1: int, col_index0: int) -> str:
    """Excel cell reference such as ``C7`` (1-based row, 0-based column)."""
    return f"{column_letter(col_index0)}{row1}"


def match_columns(header_cells: Sequence[object], rules: Sequence[ColumnRule],
                  issues: IssueCollector, sheet: str, row: int,
                  ignored: Predicate | None = None) -> dict[str, tuple[int, str | None]]:
    """Map rule fields to ``(column_index0, unit)`` for one header row (§4.1).

    Rules are applied in order; a cell is consumed by the first rule it matches; each rule takes
    the first matching cell from the left; further cells matching the same rule are ignored with
    ``W_XL_DUP_COLUMN``. Cells for which ``ignored(base, unit)`` is true are skipped with
    ``W_XL_COLUMN_IGNORED`` (PWR list legacy columns, §4.3).
    """
    normalized = [normalize_header(c) for c in header_cells]
    consumed: set[int] = set()

    if ignored is not None:
        for idx, (base, unit) in enumerate(normalized):
            if base and ignored(base, unit):
                consumed.add(idx)
                issues.warning("W_XL_COLUMN_IGNORED",
                               f"Column '{header_cells[idx]}' is ignored: plane height and PAD "
                               "position are derived automatically.", sheet,
                               f"{sheet}!{cell_ref(row, idx)}")

    mapping: dict[str, tuple[int, str | None]] = {}
    for rule in rules:
        for idx, (base, unit) in enumerate(normalized):
            if idx in consumed or not base:
                continue
            if not rule.predicate(base, unit):
                continue
            consumed.add(idx)
            if rule.field not in mapping:
                mapping[rule.field] = (idx, unit)
            else:
                first = mapping[rule.field][0]
                issues.warning("W_XL_DUP_COLUMN",
                               f"Column '{header_cells[idx]}' matches '{rule.display}' again "
                               f"(already column '{header_cells[first]}'); ignored.", sheet,
                               f"{sheet}!{cell_ref(row, idx)}")
    return mapping


@dataclass(frozen=True)
class HeaderMatch:
    """Result of header-row detection."""

    row: int  #: 1-based row number of the header
    mapping: dict[str, tuple[int, str | None]]


def find_header_row(rows: Sequence[Sequence[object]], rules: Sequence[ColumnRule],
                    issues: IssueCollector, sheet: str,
                    ignored: Predicate | None = None,
                    scan_rows: int = HEADER_SCAN_ROWS) -> HeaderMatch | None:
    """Detect the header row among the first ``scan_rows`` rows (§4.1).

    The header row is the first row with the maximum number of matched **required** columns, which
    must equal the number of required rules; otherwise ``E_XL_HEADER_NOT_FOUND`` is added (listing
    the missing columns and the best candidate row) and ``None`` is returned.
    """
    required = [r for r in rules if r.required]
    best_row = 0
    best_count = -1
    best_mapping: dict[str, tuple[int, str | None]] = {}
    for r_idx, cells in enumerate(rows[:scan_rows]):
        scratch = IssueCollector()
        mapping = match_columns(cells, rules, scratch, sheet, r_idx + 1, ignored)
        count = sum(1 for rule in required if rule.field in mapping)
        if count > best_count:
            best_row, best_count, best_mapping = r_idx + 1, count, mapping

    if best_count == len(required) and best_count > 0:
        final = match_columns(rows[best_row - 1], rules, issues, sheet, best_row, ignored)
        return HeaderMatch(best_row, final)

    missing = [rule.display for rule in required if rule.field not in best_mapping]
    if best_count <= 0:
        detail = "no candidate header row found in rows 1–%d" % scan_rows
    else:
        detail = f"best candidate row {best_row}"
    issues.error("E_XL_HEADER_NOT_FOUND",
                 f"Header row not found in sheet '{sheet}': missing column(s) "
                 f"{', '.join(missing)} ({detail}).", sheet,
                 f"{sheet}!{best_row}" if best_count > 0 else None)
    return None


# ---------------------------------------------------------------------------------------------
# Cell value helpers (§4.1, §4.4)
# ---------------------------------------------------------------------------------------------
_TRUE_TEXT = {"yes", "y", "true", "1", "x", "✓"}
_FALSE_TEXT = {"no", "n", "false", "0", ""}


def parse_bool_cell(value: object) -> bool:
    """Boolean cell rules of §4.4. Raises ``ValueError`` for unrecognised text (→ ``E_XL_BOOL``)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        raise ValueError(f"invalid boolean: {value!r}")
    text = str(value).strip().casefold()
    if text in _TRUE_TEXT:
        return True
    if text in _FALSE_TEXT:
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def parse_number_cell(value: object) -> float | None:
    """Numeric cell rules of §4.1: int/float as is; strings via ``float(s.replace(',', '.'))``.

    Empty cells return ``None``. Raises ``ValueError`` on invalid text (→ ``E_XL_NUMBER``).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"boolean is not a number: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    return float(text.replace(",", "."))


def parse_mode_cell(value: object) -> str | None:
    """``series`` / ``shunt`` by case-insensitive prefix match (§4.4); empty → ``None``.

    Raises ``ValueError`` for anything else (ambiguous prefixes such as ``s`` included).
    """
    if value is None:
        return None
    text = str(value).strip().casefold()
    if text == "":
        return None
    hits = [m for m in ("series", "shunt") if m.startswith(text) or text.startswith(m)]
    if len(hits) != 1:
        raise ValueError(f"invalid s2p mode: {value!r}")
    return hits[0]


__all__ = [
    "ColumnRule",
    "HeaderMatch",
    "normalize_header",
    "normalize_sheet_name",
    "match_columns",
    "find_header_row",
    "cell_ref",
    "column_letter",
    "parse_bool_cell",
    "parse_number_cell",
    "parse_mode_cell",
    "pwr_ignored_column",
    "STACKUP_RULES",
    "PWR_RULES",
    "DECAP_RULES",
]
