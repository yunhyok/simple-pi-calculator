"""Touchstone v1 ``.s2p`` reader, S→Z conversion and interpolation (DESIGN.md §2.7.2, §3.8, §4.6),
plus a Touchstone v1 N-port writer used by the results export (§4.8).

Errors are added to the ``IssueCollector`` and raised as :class:`InputError` carrying that issue;
warnings are only added.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

from ..errors import InputError, IssueCollector

__all__ = [
    "TwoPortData",
    "read_s2p",
    "parse_s2p_text",
    "s2p_to_impedance",
    "interpolate_impedance",
    "normalize_s2p_mode",
    "SINGULAR_EPS",
    "touchstone_extension",
    "format_touchstone_v1",
    "write_touchstone_v1",
    "z_to_s",
]

SINGULAR_EPS = 1e-12
_FREQ_UNITS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
_PARAMS = {"s", "y", "z", "h", "g"}
_FORMATS = {"ma", "db", "ri"}
_REL_EDGE = 1e-9   # grid points within this relative distance of the data range count as inside


@dataclass
class TwoPortData:
    """2-port S-parameters (§5.2). ``s[:, i, j]`` = S_(i+1)(j+1); ``s[:, 1, 0]`` = S21."""

    f_hz: np.ndarray        # (F0,)
    s: np.ndarray           # (F0, 2, 2) complex
    z0: float
    source: str = ""


def normalize_s2p_mode(mode: str | None) -> Literal["series", "shunt"]:
    """``None``/empty → ``series`` (project default); prefix match, case-insensitive (§4.4)."""
    if mode is None or not str(mode).strip():
        return "series"
    m = str(mode).strip().lower()
    if "series".startswith(m) and m.startswith("se"):
        return "series"
    if "shunt".startswith(m) and m.startswith("sh"):
        return "shunt"
    if m in ("series-through", "series_through"):
        return "series"
    if m in ("shunt-through", "shunt_through"):
        return "shunt"
    raise ValueError(f"invalid s2p mode {mode!r} (expected 'series' or 'shunt')")


def _error(issues: IssueCollector, code: str, message: str, source: str,
           line: int | None = None) -> InputError:
    issue = issues.error(code, message, source, f"line {line}" if line else None)
    return InputError([issue])


def parse_s2p_text(text: str, issues: IssueCollector, source: str = "<text>") -> TwoPortData:
    """Parse Touchstone v1 2-port text (§4.6)."""
    unit = "ghz"
    param = "s"
    fmt = "ma"
    z0 = 50.0
    seen_option = False
    values: list[float] = []
    value_lines: list[int] = []
    for no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            raise _error(issues, "E_S2P_V2",
                         "Touchstone 2.0 keyword found; only Touchstone v1 files are supported", source, no)
        if line.startswith("#"):
            if seen_option:
                continue
            seen_option = True
            toks = line[1:].split()
            i = 0
            while i < len(toks):
                t = toks[i].lower()
                if t in _FREQ_UNITS:
                    unit = t
                elif t in _PARAMS:
                    if t != "s":
                        raise _error(issues, "E_S2P_PARAM",
                                     f"{t.upper()}-parameters are not supported (S required)", source, no)
                    param = t
                elif t in _FORMATS:
                    fmt = t
                elif t == "r":
                    if i + 1 >= len(toks):
                        raise _error(issues, "E_S2P_FORMAT", "option line: R without reference impedance",
                                     source, no)
                    try:
                        z0 = float(toks[i + 1])
                    except ValueError:
                        raise _error(issues, "E_S2P_FORMAT",
                                     f"option line: invalid reference impedance {toks[i + 1]!r}",
                                     source, no) from None
                    if not (z0 > 0 and np.isfinite(z0)):
                        raise _error(issues, "E_S2P_FORMAT", f"reference impedance must be > 0, got {z0}",
                                     source, no)
                    i += 1
                else:
                    raise _error(issues, "E_S2P_FORMAT", f"option line: unknown token {toks[i]!r}",
                                 source, no)
                i += 1
            continue
        for tok in line.split():
            try:
                values.append(float(tok))
            except ValueError:
                raise _error(issues, "E_S2P_FORMAT", f"invalid numeric token {tok!r}", source, no) from None
            value_lines.append(no)
    _ = param

    records: list[list[float]] = []
    pos = 0
    last_f = -np.inf
    noise = False
    while pos < len(values):
        f = values[pos]
        if f <= last_f:
            noise = True
            break
        if pos + 9 > len(values):
            raise _error(issues, "E_S2P_FORMAT",
                         f"incomplete data record ({len(values) - pos} of 9 values); token count "
                         "is not a multiple of 9", source, value_lines[pos])
        records.append(values[pos:pos + 9])
        last_f = f
        pos += 9
    if noise:
        issues.warning("W_S2P_NOISE_IGNORED", "noise-parameter block ignored", source,
                       f"line {value_lines[pos]}")
    if len(records) < 2:
        raise _error(issues, "E_S2P_FORMAT", "at least 2 frequency points are required", source)

    arr = np.asarray(records, dtype=float)
    f_hz = arr[:, 0] * _FREQ_UNITS[unit]
    a = arr[:, 1::2]
    b = arr[:, 2::2]
    if fmt == "ri":
        sv = a + 1j * b
    else:
        mag = a if fmt == "ma" else 10.0 ** (a / 20.0)
        sv = mag * np.exp(1j * np.deg2rad(b))
    s = np.empty((len(records), 2, 2), dtype=complex)
    s[:, 0, 0] = sv[:, 0]   # v1 order: 11, 21, 12, 22
    s[:, 1, 0] = sv[:, 1]
    s[:, 0, 1] = sv[:, 2]
    s[:, 1, 1] = sv[:, 3]
    if not (np.all(np.isfinite(f_hz)) and np.all(np.isfinite(s))):
        raise _error(issues, "E_S2P_FORMAT", "non-finite values in data", source)
    if f_hz[0] <= 0:
        raise _error(issues, "E_S2P_FORMAT", "frequencies must be > 0", source)
    return TwoPortData(f_hz=f_hz, s=s, z0=z0, source=source)


def read_s2p(path: str | os.PathLike, issues: IssueCollector) -> TwoPortData:
    """Read a Touchstone v1 ``.s2p`` file (§4.6)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return parse_s2p_text(text, issues, os.fspath(path))


def s2p_to_impedance(data: TwoPortData, mode: Literal["series", "shunt"],
                     issues: IssueCollector) -> np.ndarray:
    """DUT impedance at ``data.f_hz`` from S21_avg = (S21 + S12)/2 (§2.7.2)."""
    mode = normalize_s2p_mode(mode)
    z0 = data.z0
    s21 = 0.5 * (data.s[:, 1, 0] + data.s[:, 0, 1])
    if mode == "series":
        d = s21
        small = np.abs(d) < SINGULAR_EPS
        if small.any():
            d = np.where(small, _clamp(d, SINGULAR_EPS), d)
            _warn_singular(issues, data, "series", "|S21|")
        return 2.0 * z0 * (1.0 - d) / d
    d = 1.0 - s21
    small = np.abs(d) < SINGULAR_EPS
    if small.any():
        d = np.where(small, _clamp(d, SINGULAR_EPS), d)
        _warn_singular(issues, data, "shunt", "|1 - S21|")
    return z0 * (1.0 - d) / (2.0 * d)


def _clamp(d: np.ndarray, eps: float) -> np.ndarray:
    mag = np.abs(d)
    unit = np.where(mag > 0, d / np.where(mag > 0, mag, 1.0), 1.0 + 0j)
    return unit * eps


def _warn_singular(issues: IssueCollector, data: TwoPortData, mode: str, what: str) -> None:
    issues.warning("W_S2P_SINGULAR",
                   f"{what} < {SINGULAR_EPS:g} in {mode}-through conversion; value clamped", data.source)


def interpolate_impedance(f_src: np.ndarray, z_src: np.ndarray, f_dst: np.ndarray,
                          issues: IssueCollector, source: str) -> np.ndarray:
    """Map Z from the file frequencies onto ``f_dst`` (§3.8).

    Inside the data range: linear in log10(f) of ln|Z| and of the unwrapped phase. Below: R + 1/(jωC)
    fit to the first point (or hold). Above: R + jωL fit to the last point (or hold).
    """
    fs = np.asarray(f_src, dtype=float)
    zs = np.asarray(z_src, dtype=complex)
    fd = np.atleast_1d(np.asarray(f_dst, dtype=float))
    lf = np.log10(fs)
    mag = np.maximum(np.abs(zs), 1e-300)
    phase = np.unwrap(np.angle(zs))
    lfd = np.log10(fd)
    out = np.exp(np.interp(lfd, lf, np.log(mag))) * np.exp(1j * np.interp(lfd, lf, phase))
    low = fd < fs[0] * (1.0 - _REL_EDGE)
    high = fd > fs[-1] * (1.0 + _REL_EDGE)
    if low.any():
        z1 = zs[0]
        w1 = 2 * np.pi * fs[0]
        w = 2 * np.pi * fd[low]
        if z1.imag < 0:
            cx = -1.0 / (w1 * z1.imag)
            out[low] = z1.real + 1.0 / (1j * w * cx)
        else:
            out[low] = z1
        issues.warning("W_S2P_EXTRAP_LOW",
                       f"{int(low.sum())} frequency point(s) below the data range "
                       f"({fs[0]:.6g} Hz) extrapolated", source)
    if high.any():
        zn = zs[-1]
        wn = 2 * np.pi * fs[-1]
        w = 2 * np.pi * fd[high]
        if zn.imag > 0:
            lx = zn.imag / wn
            out[high] = zn.real + 1j * w * lx
        else:
            out[high] = zn
        issues.warning("W_S2P_EXTRAP_HIGH",
                       f"{int(high.sum())} frequency point(s) above the data range "
                       f"({fs[-1]:.6g} Hz) extrapolated", source)
    return out


# ---------------------------------------------------------------------------------------------
# writer (§4.8)
# ---------------------------------------------------------------------------------------------
_MAX_WRITE_PORTS = 99
_PAIRS_PER_LINE = 4
_WRITE_NUMBER = "{:.16e}"


def touchstone_extension(n_ports: int) -> str:
    """File extension ``.s<N>p`` of Touchstone v1 (``.s1p``, ``.s2p``, … ``.s99p``)."""
    n = int(n_ports)
    if not 1 <= n <= _MAX_WRITE_PORTS:
        raise ValueError(f"Touchstone v1 export supports 1 … {_MAX_WRITE_PORTS} ports, got {n}")
    return f".s{n}p"


def z_to_s(z: np.ndarray, r_ref: float) -> np.ndarray:
    """Reflection coefficient of a 1-port (or of each uncoupled port): S = (Z − R)/(Z + R)."""
    z = np.asarray(z, dtype=complex)
    return (z - r_ref) / (z + r_ref)


def format_touchstone_v1(f_hz: np.ndarray, data: np.ndarray, *, parameter: str = "S",
                         data_format: str = "RI", r_ref: float = 1.0,
                         comments: Sequence[str] = ()) -> str:
    """Touchstone v1 text of an N-port network (N = 1 … 99).

    Follows the *Touchstone(R) File Format Specification, Version 1.1* (EIA/IBIS Open Forum,
    2002; the v1.x rules are restated in "Touchstone File Format Specification Version 2.0",
    IBIS Open Forum, 2009, section "Version 1.x files"):

    * comment lines start with ``!``; one option line ``# <freq unit> <parameter> <format> R <n>``
      precedes the data (here always ``Hz``);
    * one data block per frequency, frequencies strictly increasing;
    * **1-port**: ``f  N11`` on one line;
    * **2-port**: ``f  N11 N21 N12 N22`` on one line — the only case whose order is not
      row-major (21 before 12);
    * **3-port and more**: the matrix is written row by row, each matrix row starts on a new line
      (the first one after the frequency) and holds at most four parameter pairs per line, so for
      N ≥ 5 a matrix row wraps onto continuation lines of four pairs (a 3-port has three lines
      of three pairs, a 5-port rows of 4 + 1 pairs);
    * each parameter is a pair: ``RI`` = real, imaginary; ``MA`` = magnitude, angle in degrees;
    * in v1 files **Z and Y parameters are normalised** to the reference resistance ``R``, so a
      Z value written here is ``Z / R`` (identical to Z for the PDN default R = 1 Ω).

    ``data`` has shape ``(F, N, N)`` (a ``(F,)`` array is taken as a 1-port) and holds the
    un-normalised parameter in Ω for ``Z``, or the dimensionless S-parameters for ``S``.
    """
    param = str(parameter).strip().upper()
    fmt = str(data_format).strip().upper()
    if param not in ("S", "Z"):
        raise ValueError(f"parameter must be 'S' or 'Z', got {parameter!r}")
    if fmt not in ("RI", "MA"):
        raise ValueError(f"data format must be 'RI' or 'MA', got {data_format!r}")
    r = float(r_ref)
    if not (r > 0 and np.isfinite(r)):
        raise ValueError(f"reference resistance must be > 0, got {r_ref!r}")
    f = np.asarray(f_hz, dtype=float).ravel()
    arr = np.asarray(data, dtype=complex)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1, 1)
    if arr.ndim != 3 or arr.shape[1] != arr.shape[2] or arr.shape[0] != f.size:
        raise ValueError(f"data must have shape (F, N, N) with F = {f.size}, got {arr.shape}")
    n = arr.shape[1]
    touchstone_extension(n)  # validates N
    if f.size == 0:
        raise ValueError("no frequency points")
    if not (np.all(np.isfinite(f)) and np.all(f > 0) and np.all(np.diff(f) > 0)):
        raise ValueError("frequencies must be finite, > 0 and strictly increasing")
    if param == "Z":
        arr = arr / r
    if not np.all(np.isfinite(arr)):
        raise ValueError("non-finite parameter values")

    def pair(v: complex) -> str:
        if fmt == "RI":
            a, b = v.real, v.imag
        else:
            a, b = abs(v), float(np.degrees(np.angle(v)))
        return f"{_WRITE_NUMBER.format(a)} {_WRITE_NUMBER.format(b)}"

    out: list[str] = []
    for c in comments:
        for line in str(c).splitlines() or [""]:
            out.append(f"! {line}".rstrip())
    out.append(f"# Hz {param} {fmt} R {r:.12g}")
    for k in range(f.size):
        freq = _WRITE_NUMBER.format(f[k])
        m = arr[k]
        if n == 1:
            out.append(f"{freq} {pair(m[0, 0])}")
        elif n == 2:
            out.append(f"{freq} " + " ".join(pair(m[i, j]) for i, j in ((0, 0), (1, 0), (0, 1),
                                                                        (1, 1))))
        else:
            for i in range(n):
                for start in range(0, n, _PAIRS_PER_LINE):
                    chunk = " ".join(pair(m[i, j]) for j in range(start,
                                                                  min(n, start + _PAIRS_PER_LINE)))
                    lead = freq if (i == 0 and start == 0) else " " * len(freq)
                    out.append(f"{lead} {chunk}")
    return "\n".join(out) + "\n"


def write_touchstone_v1(path: str | os.PathLike, f_hz: np.ndarray, data: np.ndarray, **kwargs
                        ) -> str:
    """Write :func:`format_touchstone_v1` text to ``path`` (UTF-8, ``\\n`` line ends)."""
    text = format_touchstone_v1(f_hz, data, **kwargs)
    folder = os.path.dirname(os.path.abspath(os.fspath(path)))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return os.fspath(path)
