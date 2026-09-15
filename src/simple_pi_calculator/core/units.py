"""Unit conversions and display scaling (DESIGN.md §1.6, §4.1, §5.5, §5.6).

``core`` works in SI; conversions from mm / nH happen at the io/gui boundary using these helpers.
"""

from __future__ import annotations

import math
import re

MM: float = 1.0e-3  #: metres per millimetre
UM: float = 1.0e-6  #: metres per micrometre
NH: float = 1.0e-9  #: henries per nanohenry
PF: float = 1.0e-12  #: farads per picofarad


def mm_to_m(value_mm: float) -> float:
    """Millimetres → metres."""
    return value_mm * MM


def m_to_mm(value_m: float) -> float:
    """Metres → millimetres."""
    return value_m / MM


def nh_to_h(value_nh: float) -> float:
    """Nanohenries → henries."""
    return value_nh * NH


def h_to_nh(value_h: float) -> float:
    """Henries → nanohenries."""
    return value_h / NH


# ---------------------------------------------------------------------------------------------
# Excel length units (§4.1): factor converting the header unit to millimetres
# ---------------------------------------------------------------------------------------------
LENGTH_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "um": 1.0e-3,
    "mil": 0.0254,
    "m": 1000.0,
    "in": 25.4,
    "inch": 25.4,
}


def length_unit_factor_mm(unit: str | None) -> float:
    """Factor to convert a value in ``unit`` (normalised header unit, §4.1) to mm.

    ``None`` / empty → mm. Raises ``KeyError`` for an unknown unit (caller emits ``E_XL_UNIT``).
    """
    if unit is None or unit == "":
        return 1.0
    return LENGTH_UNIT_TO_MM[unit]


# ---------------------------------------------------------------------------------------------
# Impedance display units (§1.4, §5.6)
# ---------------------------------------------------------------------------------------------
Z_UNIT_SCALE: dict[str, float] = {"ohm": 1.0, "mohm": 1.0e3, "uohm": 1.0e6}
Z_UNIT_LABEL: dict[str, str] = {"ohm": "Ω", "mohm": "mΩ", "uohm": "µΩ"}


def z_scale(unit: str) -> float:
    """Multiplier from Ω to the display unit ``unit`` ∈ {ohm, mohm, uohm}."""
    return Z_UNIT_SCALE[unit]


def z_label(unit: str) -> str:
    """Display symbol of an impedance unit, e.g. ``"mΩ"``."""
    return Z_UNIT_LABEL[unit]


def format_sig(value: float, digits: int = 4) -> str:
    """Format with ``digits`` significant digits, no exponent for ordinary magnitudes (§5.5)."""
    if value == 0 or not math.isfinite(value):
        return f"{value:g}"
    exponent = int(math.floor(math.log10(abs(value))))
    if -4 <= exponent < 9:
        decimals = max(digits - 1 - exponent, 0)
        return f"{value:.{decimals}f}"
    return f"{value:.{digits - 1}e}"


_FREQ_PREFIX = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, "")]


def format_frequency(f_hz: float, digits: int = 4) -> str:
    """Human readable frequency, e.g. ``12.3 MHz``, ``100 kHz``."""
    for scale, prefix in _FREQ_PREFIX:
        if abs(f_hz) >= scale:
            text = f"{f_hz / scale:.{digits}g}"
            return f"{text} {prefix}Hz"
    return f"{f_hz:.{digits}g} Hz"


_FREQ_RE = re.compile(
    r"^\s*([+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(k|K|M|meg|MEG|Meg|G)?\s*(?:Hz|hz|HZ)?\s*$"
)
_FREQ_MULT = {None: 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "meg": 1e6, "MEG": 1e6, "Meg": 1e6, "G": 1e9}


def parse_frequency(text: str) -> float:
    """Engineering frequency parser for the Sweep panel (§5.5), **not** the SPICE parser.

    Case-sensitive suffixes ``k``/``K`` = 1e3, ``M``/``meg``/``MEG`` = 1e6, ``G`` = 1e9, optional
    trailing ``Hz``; lower-case ``m`` is rejected. Raises ``ValueError`` on invalid input.
    """
    match = _FREQ_RE.match(text)
    if not match:
        raise ValueError(f"invalid frequency: {text!r}")
    return float(match.group(1)) * _FREQ_MULT[match.group(2)]
