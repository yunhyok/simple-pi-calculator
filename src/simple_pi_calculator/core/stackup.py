"""Stack-up model and PWR/GND plane-pair derivation (DESIGN.md §2.1, §2.2, §4.2, §4.3, §5.2).

Conventions (§1.6): SI units, layer 1 at the top, z increases downward, z = 0 at the top surface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from simple_pi_calculator.constants import EPS0
from simple_pi_calculator.errors import InputError, Issue, IssueCollector

#: Conductivity range outside which ``W_STACK_SIGMA_RANGE`` is emitted (§4.2) [S/m]
SIGMA_WARN_MIN: float = 1.0e5
SIGMA_WARN_MAX: float = 1.0e8
#: More intermediate layers than this between PWR and GND → ``W_PWR_FAR_GND`` (§4.3)
FAR_GND_MAX_INTERMEDIATE: int = 3


def is_metal_conductivity(conductivity: float | None) -> bool:
    """Row classification of §4.2: metal iff the conductivity is a number > 0 (``math.inf`` allowed)."""
    return conductivity is not None and not math.isnan(conductivity) and conductivity > 0.0


@dataclass(frozen=True)
class Layer:
    """One stack-up row (§2.1, §5.2). SI units."""

    number: int
    name: str
    thickness_m: float
    conductivity: float | None  # S/m; None or 0 → dielectric
    dk: float | None
    df: float | None

    @property
    def is_metal(self) -> bool:
        """True for metal rows (σ > 0), False for dielectric rows (§4.2)."""
        return is_metal_conductivity(self.conductivity)

    @property
    def kind(self) -> str:
        """``"Metal"`` or ``"Dielectric"`` (GUI display, §5.5)."""
        return "Metal" if self.is_metal else "Dielectric"

    @property
    def label(self) -> str:
        return f"Layer {self.number}" + (f" ({self.name})" if self.name else "")


@dataclass(frozen=True)
class Stackup:
    """Ordered stack-up (§2.1). ``layers`` is sorted by layer number on construction."""

    layers: tuple[Layer, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.layers, key=lambda layer: layer.number))
        object.__setattr__(self, "layers", ordered)

    # -- lookup ---------------------------------------------------------------------------------
    def has_layer(self, n: int) -> bool:
        return any(layer.number == n for layer in self.layers)

    def index_of(self, n: int) -> int:
        for idx, layer in enumerate(self.layers):
            if layer.number == n:
                return idx
        raise KeyError(f"layer {n} not found in stack-up")

    def by_number(self, n: int) -> Layer:
        """Layer with number ``n``; raises ``KeyError`` if absent."""
        return self.layers[self.index_of(n)]

    # -- geometry (§2.1) ------------------------------------------------------------------------
    def z_top(self, n: int) -> float:
        """z of the top face of layer ``n``: Σ thickness of all rows above it [m]."""
        idx = self.index_of(n)
        return math.fsum(layer.thickness_m for layer in self.layers[:idx])

    def z_bottom(self, n: int) -> float:
        return self.z_top(n) + self.by_number(n).thickness_m

    def z_center(self, n: int) -> float:
        """z of the centre of layer ``n`` [m]."""
        return self.z_top(n) + 0.5 * self.by_number(n).thickness_m

    @property
    def total_thickness(self) -> float:
        """T_total = Σ t_j [m]."""
        return math.fsum(layer.thickness_m for layer in self.layers)

    @property
    def metal_layers(self) -> tuple[int, ...]:
        return tuple(layer.number for layer in self.layers if layer.is_metal)

    def between(self, a: int, b: int) -> tuple[Layer, ...]:
        """Intermediate rows I = {i : lo < i < hi} (§2.2)."""
        lo, hi = min(a, b), max(a, b)
        return tuple(layer for layer in self.layers if lo < layer.number < hi)

    # -- validation (§4.2) ----------------------------------------------------------------------
    def validate(self, issues: IssueCollector, source: str | None = None) -> None:
        """Emit the stack-up validation issues of §4.2 into ``issues``."""
        if not self.layers:
            issues.error("E_STACK_EMPTY", "The stack-up contains no layers.", source)
            return

        seen: set[int] = set()
        for layer in self.layers:
            if layer.number < 1:
                issues.error("E_STACK_LAYER_NUM",
                             f"Layer number {layer.number} is invalid (must be an integer ≥ 1).",
                             source)
            if layer.number in seen:
                issues.error("E_STACK_LAYER_DUP", f"Duplicate layer number {layer.number}.", source)
            seen.add(layer.number)

        numbers = [layer.number for layer in self.layers]
        unique = sorted(set(numbers))
        if unique and unique != list(range(1, len(unique) + 1)):
            issues.warning("W_STACK_LAYER_GAP",
                           "Layer numbers are not contiguous 1..N "
                           f"({', '.join(str(n) for n in unique)}); rows are sorted, numbering kept.",
                           source)

        previous: Layer | None = None
        for layer in self.layers:
            label = layer.label
            if not (layer.thickness_m > 0.0) or not math.isfinite(layer.thickness_m):
                issues.error("E_STACK_THICKNESS", f"{label}: thickness missing or ≤ 0.", source)
            if layer.is_metal:
                sigma = layer.conductivity
                assert sigma is not None
                if math.isfinite(sigma) and not (SIGMA_WARN_MIN <= sigma <= SIGMA_WARN_MAX):
                    issues.warning("W_STACK_SIGMA_RANGE",
                                   f"{label}: conductivity {sigma:g} S/m is outside "
                                   f"[{SIGMA_WARN_MIN:g}, {SIGMA_WARN_MAX:g}] S/m.", source)
                if layer.dk is not None or layer.df is not None:
                    issues.info("W_STACK_METAL_DKDF",
                                f"{label}: Dk/Df given for a metal layer (ignored).", source)
                if previous is not None and previous.is_metal:
                    issues.info("W_STACK_ADJ_METAL",
                                f"Metal layers {previous.number} and {layer.number} are adjacent "
                                "without dielectric (allowed).", source)
            else:
                if layer.dk is None or not (layer.dk >= 1.0):
                    issues.error("E_STACK_DK", f"{label}: dielectric Dk missing or < 1.", source)
                if layer.df is None:
                    issues.warning("W_STACK_DF_MISSING",
                                   f"{label}: dielectric Df missing; 0 is used.", source)
                elif not (0.0 <= layer.df <= 1.0):
                    issues.error("E_STACK_DF", f"{label}: Df {layer.df:g} outside [0, 1].", source)
            previous = layer


@dataclass(frozen=True)
class PlanePair:
    """PWR/GND plane pair with its equivalent dielectric (§2.2, §5.2)."""

    pwr_layer: Layer
    gnd_layer: Layer
    d_m: float  #: cavity thickness d = Σ_I t_i (incl. intermediate metal) [m]
    er_eff: float  #: Re ε̃_eff
    tand_eff: float  #: −Im ε̃_eff / Re ε̃_eff
    d_dielectric_m: float = 0.0  #: d_d = Σ_{I_d} t_i [m]
    intermediate_metal: tuple[int, ...] = ()  #: numbers of metal rows between the planes

    def plane_capacitance(self, a_m: float, b_m: float) -> float:
        """DC plane capacitance C = ε0 εr_eff a b / d [F] (§2.3), real εr only."""
        return EPS0 * self.er_eff * a_m * b_m / self.d_m

    @property
    def eps_complex(self) -> complex:
        """ε̃_eff = εr_eff (1 − j tanδ_eff)."""
        return complex(self.er_eff, -self.er_eff * self.tand_eff)

    @property
    def nearer_layer(self) -> Layer:
        """The plane closer to the Top surface (§2.6.1)."""
        return self.pwr_layer if self.pwr_layer.number < self.gnd_layer.number else self.gnd_layer


def combine_dielectrics(layers: tuple[Layer, ...] | list[Layer]) -> tuple[float, float, float]:
    """Exact series combination of dielectric rows (§2.2).

    Returns ``(d_d, εr_eff, tanδ_eff)`` with ε̃_eff = d_d / Σ t_i/(εr_i (1 − j tanδ_i)).
    Missing Df counts as 0. Raises ``ValueError`` if a Dk is missing/invalid or no layer is given.
    """
    d_d = math.fsum(layer.thickness_m for layer in layers)
    if not layers or d_d <= 0.0:
        raise ValueError("no dielectric thickness")
    total = 0j
    for layer in layers:
        if layer.dk is None or not (layer.dk > 0.0):
            raise ValueError(f"layer {layer.number}: invalid Dk")
        tand = layer.df or 0.0
        total += layer.thickness_m / (layer.dk * complex(1.0, -tand))
    eps = d_d / total
    er = eps.real
    tand_eff = -eps.imag / eps.real
    return d_d, er, tand_eff


def combine_dielectrics_first_order(layers: tuple[Layer, ...] | list[Layer]
                                    ) -> tuple[float, float, float]:
    """First-order form of §2.2 (documentation / comparison only, not used by the engine).

    εr_eff = d_d / Σ t_i/εr_i, tanδ_eff = Σ (t_i/εr_i)·tanδ_i / Σ (t_i/εr_i). For equal εr_i this is
    the thickness-weighted tanδ; it differs from :func:`combine_dielectrics` by O(tanδ²).
    """
    d_d = math.fsum(layer.thickness_m for layer in layers)
    if not layers or d_d <= 0.0:
        raise ValueError("no dielectric thickness")
    weights = []
    for layer in layers:
        if layer.dk is None or not (layer.dk > 0.0):
            raise ValueError(f"layer {layer.number}: invalid Dk")
        weights.append(layer.thickness_m / layer.dk)
    total = math.fsum(weights)
    tand = math.fsum(w * (layer.df or 0.0) for w, layer in zip(weights, layers)) / total
    return d_d, d_d / total, tand


def check_pwr_layers(stackup: Stackup, pwr_layer: int, gnd_layer: int, issues: IssueCollector,
                     source: str | None) -> list[Issue]:
    """Layer-reference checks of §4.3 (E_PWR_LAYER_NOT_FOUND, E_PWR_SAME_LAYER,
    E_PWR_LAYER_NOT_METAL). Returns the error issues added (empty list if the pair is usable)."""
    added: list[Issue] = []
    for role, n in (("PWR", pwr_layer), ("GND", gnd_layer)):
        if not stackup.has_layer(n):
            added.append(issues.error("E_PWR_LAYER_NOT_FOUND",
                                      f"{role} layer {n} does not exist in the stack-up.", source))
    if pwr_layer == gnd_layer:
        added.append(issues.error("E_PWR_SAME_LAYER",
                                  f"PWR layer and GND layer are the same layer ({pwr_layer}).",
                                  source))
    if added:
        return added
    for role, n in (("PWR", pwr_layer), ("GND", gnd_layer)):
        layer = stackup.by_number(n)
        if not layer.is_metal:
            added.append(issues.error("E_PWR_LAYER_NOT_METAL",
                                      f"{role} layer {n} ({layer.name}) is a dielectric layer, "
                                      "not a metal layer.", source))
    return added


def derive_plane_pair(stackup: Stackup, pwr_layer: int, gnd_layer: int,
                      issues: IssueCollector, source: str) -> PlanePair:
    """Derive the cavity thickness and equivalent dielectric between PWR and GND (§2.2).

    Warnings (``W_STACK_METAL_BETWEEN``, ``W_PWR_FAR_GND``) are added to ``issues``. Errors are
    added to ``issues`` **and** raised as :class:`InputError` because no pair can be returned.
    """
    errors = check_pwr_layers(stackup, pwr_layer, gnd_layer, issues, source)
    if errors:
        raise InputError(errors)

    pwr = stackup.by_number(pwr_layer)
    gnd = stackup.by_number(gnd_layer)
    intermediate = stackup.between(pwr_layer, gnd_layer)

    if len(intermediate) > FAR_GND_MAX_INTERMEDIATE:
        issues.warning("W_PWR_FAR_GND",
                       f"PWR layer {pwr_layer} and GND layer {gnd_layer} are separated by "
                       f"{len(intermediate)} intermediate layers (more than "
                       f"{FAR_GND_MAX_INTERMEDIATE}).", source)

    metal_between = tuple(layer.number for layer in intermediate if layer.is_metal)
    for k in metal_between:
        issues.warning("W_STACK_METAL_BETWEEN",
                       f"Metal layer {k} lies between PWR layer {pwr_layer} and GND layer "
                       f"{gnd_layer}; it is assumed to be fully cleared (filled with dielectric) "
                       "in the plane area.", source)

    dielectrics = [layer for layer in intermediate if not layer.is_metal]
    if not dielectrics:
        err = issues.error("E_STACK_NO_DIELECTRIC",
                           f"No dielectric layer between PWR layer {pwr_layer} and GND layer "
                           f"{gnd_layer}.", source)
        raise InputError([err])

    bad_dk = [layer for layer in dielectrics if layer.dk is None or not (layer.dk >= 1.0)]
    if bad_dk:
        errs = [issues.error("E_STACK_DK", f"{layer.label}: dielectric Dk missing "
                             "or < 1.", source) for layer in bad_dk]
        raise InputError(errs)

    d_m = math.fsum(layer.thickness_m for layer in intermediate)
    d_d, er_eff, tand_eff = combine_dielectrics(dielectrics)
    return PlanePair(pwr_layer=pwr, gnd_layer=gnd, d_m=d_m, er_eff=er_eff, tand_eff=tand_eff,
                     d_dielectric_m=d_d, intermediate_metal=metal_between)


__all__ = [
    "Layer",
    "Stackup",
    "PlanePair",
    "combine_dielectrics",
    "combine_dielectrics_first_order",
    "check_pwr_layers",
    "derive_plane_pair",
    "is_metal_conductivity",
]
