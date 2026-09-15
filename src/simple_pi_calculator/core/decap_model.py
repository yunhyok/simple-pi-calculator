"""Decap models: SPICE subcircuit or Touchstone .s2p, with a load cache (DESIGN.md §2.7, §4.4, §5.2)."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import numpy as np

from ..errors import InputError, Issue, IssueCollector, Severity
from .mna import MnaSingularError, build_mna, solve_impedance
from .spice_parser import Netlist, parse_spice_file
from .touchstone import (TwoPortData, interpolate_impedance, normalize_s2p_mode, read_s2p,
                         s2p_to_impedance)

__all__ = [
    "DecapModel",
    "SpiceDecapModel",
    "S2pDecapModel",
    "SpiceDecap",
    "TouchstoneDecap",
    "DecapModelCache",
    "SPICE_EXTENSIONS",
    "S2P_EXTENSIONS",
    "load_decap_model",
]

SPICE_EXTENSIONS = (".mod", ".lib", ".sp", ".cir", ".sub", ".inc")
S2P_EXTENSIONS = (".s2p",)


@runtime_checkable
class DecapModel(Protocol):
    label: str

    def impedance(self, f_hz: np.ndarray) -> np.ndarray: ...


class SpiceDecapModel:
    """Wraps a flattened :class:`Netlist`; Z(f) by AC MNA (§2.7.1)."""

    def __init__(self, netlist: Netlist, label: str | None = None):
        self.netlist = netlist
        base = os.path.basename(netlist.source_path)
        self.label = label or f"{base}:{netlist.subckt_name.upper()}"
        self.source = netlist.source_path
        self._system = build_mna(netlist.elements, netlist.pin1, netlist.pin2)

    def impedance(self, f_hz: np.ndarray, issues: IssueCollector | None = None) -> np.ndarray:
        """Complex Z(f), shape (F,). Singular MNA → ``InputError`` with ``E_SINGULAR``."""
        try:
            return solve_impedance(self._system, np.asarray(f_hz, dtype=float))
        except MnaSingularError as exc:
            msg = f"decap model {self.label}: {exc}"
            if issues is not None:
                issue = issues.error("E_SINGULAR", msg, self.source)
            else:
                issue = Issue("E_SINGULAR", Severity.ERROR, msg, self.source)
            raise InputError([issue]) from None


class S2pDecapModel:
    """Wraps :class:`TwoPortData` and a fixture mode; Z converted once, interpolated per call (§3.8)."""

    def __init__(self, data: TwoPortData, mode: str | None, issues: IssueCollector,
                 label: str | None = None):
        self.data = data
        self.mode = normalize_s2p_mode(mode)
        self.source = data.source
        self.label = label or f"{os.path.basename(data.source)} ({self.mode})"
        self.z_src = s2p_to_impedance(data, self.mode, issues)

    def impedance(self, f_hz: np.ndarray, issues: IssueCollector | None = None) -> np.ndarray:
        """Complex Z(f), shape (F,). Extrapolation warnings go to ``issues`` if given."""
        sink = issues if issues is not None else IssueCollector()
        return interpolate_impedance(self.data.f_hz, self.z_src, np.asarray(f_hz, dtype=float),
                                     sink, self.source)


SpiceDecap = SpiceDecapModel
TouchstoneDecap = S2pDecapModel


class _Recorder:
    """Forwards ``add``-style calls to a collector and remembers the issues for cache replay."""

    def __init__(self, target: IssueCollector):
        self._target = target
        self.recorded: list[Issue] = []

    def add(self, code, severity, message, source=None, location=None):
        issue = self._target.add(code, severity, message, source, location)
        self.recorded.append(issue)
        return issue

    def error(self, code, message, source=None, location=None):
        return self.add(code, Severity.ERROR, message, source, location)

    def warning(self, code, message, source=None, location=None):
        return self.add(code, Severity.WARNING, message, source, location)

    def info(self, code, message, source=None, location=None):
        return self.add(code, Severity.INFO, message, source, location)


def load_decap_model(path: str | os.PathLike, subckt: str | None, s2p_mode: str | None,
                     issues: IssueCollector) -> DecapModel:
    """Load a model by extension (§4.4) without caching."""
    p = os.fspath(path)
    if not os.path.isfile(p):
        issue = issues.error("E_DECAP_FILE_NOT_FOUND", f"decap model file not found: {p}", p)
        raise InputError([issue])
    ext = os.path.splitext(p)[1].lower()
    try:
        if ext in SPICE_EXTENSIONS:
            return SpiceDecapModel(parse_spice_file(p, subckt, issues))
        if ext in S2P_EXTENSIONS:
            try:
                mode = normalize_s2p_mode(s2p_mode)
            except ValueError as exc:
                issue = issues.error("E_S2P_MODE", str(exc), p)
                raise InputError([issue]) from None
            return S2pDecapModel(read_s2p(p, issues), mode, issues)
    except OSError as exc:  # permission denied, file locked, vanished between check and read
        issue = issues.error("E_DECAP_FILE_READ", f"cannot read decap model file {p}: {exc}", p)
        raise InputError([issue]) from None
    issue = issues.error("E_DECAP_FILE_TYPE",
                         f"unsupported decap model file type {ext or '(none)'!r}: {p} "
                         f"(expected {', '.join(SPICE_EXTENSIONS + S2P_EXTENSIONS)})", p)
    raise InputError([issue])


class DecapModelCache:
    """Cache of loaded decap models keyed by (abs path, mtime, size, subckt, s2p mode).

    Warnings emitted while loading are replayed into the caller's collector on every cache hit, so each
    computation sees the same issues. Failed loads are not cached.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple, tuple[DecapModel, list[Issue]]] = {}

    def get(self, path: str, subckt: str | None, s2p_mode: str | None,
            issues: IssueCollector) -> DecapModel:
        p = os.path.abspath(os.fspath(path))
        try:
            st = os.stat(p)
        except OSError:
            issue = issues.error("E_DECAP_FILE_NOT_FOUND", f"decap model file not found: {p}", p)
            raise InputError([issue]) from None
        ext = os.path.splitext(p)[1].lower()
        sub_key = (subckt or "").strip().lower() if ext in SPICE_EXTENSIONS else ""
        mode_key = (s2p_mode or "").strip().lower() if ext in S2P_EXTENSIONS else ""
        key = (os.path.normcase(p), st.st_mtime_ns, st.st_size, sub_key, mode_key)
        hit = self._cache.get(key)
        if hit is not None:
            model, recorded = hit
            for i in recorded:
                issues.add(i.code, i.severity, i.message, i.source, i.location)
            return model
        rec = _Recorder(issues)
        model = load_decap_model(p, subckt, s2p_mode, rec)  # type: ignore[arg-type]
        self._cache[key] = (model, rec.recorded)
        return model

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
