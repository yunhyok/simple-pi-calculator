"""Dense complex AC modified nodal analysis for flattened decap netlists (DESIGN.md §2.7.1, §3.7).

System ``(A0 + jω A1) x = e`` with unknowns ``[V_1 … V_n, i_1 … i_b]``; pin2 is the reference
node, ``e[pin1] = +1 A`` so that ``Z(ω) = V_pin1``. Inductors and zero-valued resistors carry a
branch-current unknown (L = 0 / R = 0 are exact shorts, no 1/0). C = 0 elements are omitted.
Mutual inductance K stamps −M into the A1 block between the two inductor branch rows.

The solve is batched over frequency for systems with ≤ 200 unknowns, otherwise looped (§3.7 item 5).
After the solve, non-finite results or a reciprocal condition number (of the row/column-equilibrated
matrix) below 1e-14 raise :class:`MnaSingularError` (§3.6/§3.7, review item F12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .spice_parser import Element, Netlist

__all__ = [
    "MnaSingularError",
    "MnaSystem",
    "build_mna",
    "solve_impedance",
    "impedance_two_terminal",
    "impedance_from_elements",
    "impedance_2terminal",
    "RCOND_MIN",
    "BATCH_MAX_UNKNOWNS",
]

RCOND_MIN = 1e-14
BATCH_MAX_UNKNOWNS = 200


class MnaSingularError(ArithmeticError):
    """The MNA system is singular or ill-conditioned at some frequency."""

    def __init__(self, message: str, f_hz: float | None = None):
        super().__init__(message)
        self.f_hz = f_hz


@dataclass
class MnaSystem:
    a0: np.ndarray          # (n, n) real
    a1: np.ndarray          # (n, n) real, multiplies jω
    rhs: np.ndarray         # (n,) real
    out_index: int          # index of V_pin1
    node_index: dict[str, int]
    branch_index: dict[str, int]   # element name → row


def build_mna(elements: Sequence[Element], pin1: str, pin2: str, gmin: float = 1e-12) -> MnaSystem:
    """Assemble the real matrices A0, A1 and the excitation vector (§2.7.1 stamp table)."""
    if pin1 == pin2:
        raise ValueError("pin1 and pin2 must be different nodes")
    stamped = [e for e in elements if e.kind in ("R", "L", "C") and not (e.kind == "C" and e.value == 0.0)]
    node_index: dict[str, int] = {pin1: 0}
    for e in stamped:
        for nd in e.nodes:
            if nd != pin2 and nd not in node_index:
                node_index[nd] = len(node_index)
    n_nodes = len(node_index)
    branch_elems = [e for e in stamped if e.kind == "L" or (e.kind == "R" and e.value == 0.0)]
    branch_index: dict[str, int] = {}
    for b, e in enumerate(branch_elems):
        branch_index[e.name] = n_nodes + b
    n = n_nodes + len(branch_elems)
    a0 = np.zeros((n, n))
    a1 = np.zeros((n, n))

    def idx(nd: str) -> int:
        return -1 if nd == pin2 else node_index[nd]

    def stamp_sym(mat: np.ndarray, p: int, q: int, val: float) -> None:
        if p >= 0:
            mat[p, p] += val
        if q >= 0:
            mat[q, q] += val
        if p >= 0 and q >= 0:
            mat[p, q] -= val
            mat[q, p] -= val

    inductance: dict[str, float] = {}
    for e in stamped:
        p, q = idx(e.nodes[0]), idx(e.nodes[1])
        if e.kind == "C":
            stamp_sym(a1, p, q, e.value)
        elif e.kind == "R" and e.value != 0.0:
            stamp_sym(a0, p, q, 1.0 / e.value)
        else:  # L, or R = 0 as zero-impedance branch
            r = branch_index[e.name]
            if p >= 0:
                a0[p, r] += 1.0
                a0[r, p] += 1.0
            if q >= 0:
                a0[q, r] -= 1.0
                a0[r, q] -= 1.0
            if e.kind == "L":
                a1[r, r] -= e.value
                inductance[e.name] = e.value
    for e in elements:
        if e.kind != "K" or e.value == 0.0:
            continue
        la, lb = e.coupled
        if la not in branch_index or lb not in branch_index:
            raise ValueError(f"coupling {e.name} references unknown inductor(s) {la}, {lb}")
        m = e.value * np.sqrt(inductance[la] * inductance[lb])
        ra, rb = branch_index[la], branch_index[lb]
        a1[ra, rb] -= m
        a1[rb, ra] -= m
    for i in range(n_nodes):
        a0[i, i] += gmin
    rhs = np.zeros(n)
    rhs[0] = 1.0
    return MnaSystem(a0=a0, a1=a1, rhs=rhs, out_index=0, node_index=node_index,
                     branch_index=branch_index)


def _rcond_equilibrated(a: np.ndarray) -> np.ndarray:
    """Batched 2-norm reciprocal condition number after row then column max-abs scaling (0 if non-finite)."""
    finite = np.all(np.isfinite(a), axis=(-2, -1))
    a = np.where(finite[:, None, None], a, 0.0)
    mag = np.abs(a)
    r = mag.max(axis=-1, keepdims=True)
    r[r == 0] = 1.0
    b = a / r
    c = np.abs(b).max(axis=-2, keepdims=True)
    c[c == 0] = 1.0
    b = b / c
    with np.errstate(all="ignore"):
        s = np.linalg.svd(b, compute_uv=False)
        rc = s[..., -1] / s[..., 0]
    return np.where(finite, rc, 0.0)


def solve_impedance(system: MnaSystem, f_hz: np.ndarray, check: bool = True) -> np.ndarray:
    """Z(f) = V_pin1 for a unit current into pin1; complex128, shape (F,)."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    omega = 2.0 * np.pi * f
    n = system.rhs.size
    out = np.empty(f.size, dtype=complex)
    if f.size == 0:
        return out

    def solve_block(sl: slice) -> None:
        a = system.a0[None, :, :] + 1j * omega[sl, None, None] * system.a1[None, :, :]
        b = np.broadcast_to(system.rhs.astype(complex), (a.shape[0], n))[..., None]
        try:
            with np.errstate(all="ignore"):
                x = np.linalg.solve(a, b)[..., 0]
        except np.linalg.LinAlgError:
            fi = _first_bad(a)
            raise MnaSingularError(f"singular MNA matrix at f = {f[sl][fi]:.6g} Hz", f[sl][fi]) from None
        z = x[:, system.out_index]
        if check:
            bad = ~np.isfinite(z)
            rc = _rcond_equilibrated(a)
            bad |= ~(rc >= RCOND_MIN)
            if bad.any():
                fi = int(np.argmax(bad))
                raise MnaSingularError(
                    f"ill-conditioned MNA matrix at f = {f[sl][fi]:.6g} Hz (rcond = {rc[fi]:.3g})",
                    f[sl][fi])
        out[sl] = z

    if n <= BATCH_MAX_UNKNOWNS:
        solve_block(slice(0, f.size))
    else:
        for i in range(f.size):
            solve_block(slice(i, i + 1))
    return out


def _first_bad(a: np.ndarray) -> int:
    for i in range(a.shape[0]):
        try:
            np.linalg.solve(a[i], np.ones(a.shape[-1], dtype=complex))
        except np.linalg.LinAlgError:
            return i
    return 0


def impedance_from_elements(elements: Sequence[Element], pin1: str, pin2: str, f_hz: np.ndarray,
                            gmin: float = 1e-12) -> np.ndarray:
    """Z(f) between ``pin1`` and ``pin2`` of a flat element list."""
    return solve_impedance(build_mna(elements, pin1, pin2, gmin), f_hz)


def impedance_two_terminal(netlist: Netlist, f_hz: np.ndarray, gmin: float = 1e-12) -> np.ndarray:
    """Z(f) between pin1 and pin2, complex128, shape (F,) (§5.2)."""
    return impedance_from_elements(netlist.elements, netlist.pin1, netlist.pin2, f_hz, gmin)


impedance_2terminal = impedance_two_terminal
