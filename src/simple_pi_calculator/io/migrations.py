"""Project-file schema migration chain (DESIGN.md §5.8.4).

Each schema bump adds ``MIGRATIONS[n]`` converting a version-``n`` document into version ``n+1``;
functions are pure (they must not mutate their argument). ``migrate`` reads
``CURRENT_SCHEMA_VERSION`` and ``MIGRATIONS`` from this module at call time, so tests can
monkeypatch them.
"""

from __future__ import annotations

import copy
import math
from typing import Callable

from simple_pi_calculator.errors import IssueCollector, ProjectFormatError, ProjectTooNewError

CURRENT_SCHEMA_VERSION: int = 4


def migrate_1_to_2(doc: dict) -> dict:
    """Schema 1 → 2: ``vias.vias_per_decap`` (PWR + GND vias of one decap via set, even, default
    2) becomes ``vias.vias_per_pad`` (parallel vias on each decap pad, default 1).

    n_pad = max(1, ceil(v/2)): 2 → 1, 4 → 2, and an odd v (which the v0.1 GUI rounded up to the
    next even count) → (v+1)/2, so every valid schema-1 project computes identically.
    A non-numeric value is carried over unchanged for the reader to reject.
    """
    out = copy.deepcopy(doc)
    vias = out.get("vias")
    if isinstance(vias, dict) and "vias_per_decap" in vias:
        value = vias.pop("vias_per_decap")
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and math.isfinite(value):
            value = max(1, int(math.ceil(value / 2.0)))
        vias.setdefault("vias_per_pad", value)
    return out


def migrate_2_to_3(doc: dict) -> dict:
    """Schema 2 → 3: every ``pwr.rows[]`` entry gets ``n_pads`` (number of observation pads of the
    net, §2.5.1/§2.8). Schema 2 had exactly one PAD per net, so the value is 1 and every schema-2
    project computes identically. Existing ``n_pads`` keys and malformed rows are left untouched
    for the reader.
    """
    out = copy.deepcopy(doc)
    pwr = out.get("pwr")
    if isinstance(pwr, dict) and isinstance(pwr.get("rows"), list):
        for row in pwr["rows"]:
            if isinstance(row, dict):
                row.setdefault("n_pads", 1)
    return out


def migrate_3_to_4(doc: dict) -> dict:
    """Schema 3 → 4: the ``decaps`` block gets the distance distribution (§2.5.5)
    ``distance_mode = "fixed"``, ``sigma_mm = 0.5`` and ``seed = 12345``. Fixed mode is the
    schema-3 behaviour, so every schema-3 project computes identically. Existing keys and a
    malformed ``decaps`` value are left untouched for the reader.
    """
    out = copy.deepcopy(doc)
    decaps = out.get("decaps")
    if isinstance(decaps, dict):
        decaps.setdefault("distance_mode", "fixed")
        decaps.setdefault("sigma_mm", 0.5)
        decaps.setdefault("seed", 12345)
    return out


MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: migrate_1_to_2, 2: migrate_2_to_3,
                                                 3: migrate_3_to_4}


def schema_version_of(doc: dict) -> int:
    """Return ``doc["schema_version"]``; raises :class:`ProjectFormatError` if missing / not int."""
    version = doc.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ProjectFormatError("'schema_version' is missing or not an integer ≥ 1.")
    return version


def migrate(doc: dict, issues: IssueCollector) -> dict:
    """Bring ``doc`` to ``CURRENT_SCHEMA_VERSION`` (§5.8.4).

    * version == current → returned unchanged (same object).
    * version > current → :class:`ProjectTooNewError`.
    * version < current → steps ``MIGRATIONS[v], MIGRATIONS[v+1], …`` applied to a deep copy;
      info ``I_PROJECT_MIGRATED`` is added. A missing step → :class:`ProjectFormatError`.
    """
    current = CURRENT_SCHEMA_VERSION
    version = schema_version_of(doc)
    if version == current:
        return doc
    if version > current:
        raise ProjectTooNewError(version)

    original = version
    out = copy.deepcopy(doc)
    while version < current:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ProjectFormatError(
                f"No migration step from schema version {version} to {version + 1}.")
        out = step(out)
        version += 1
        out["schema_version"] = version
    issues.info("I_PROJECT_MIGRATED", f"Project converted from schema {original} to schema "
                f"{current}.")
    return out


__all__ = ["CURRENT_SCHEMA_VERSION", "MIGRATIONS", "migrate", "migrate_1_to_2", "migrate_2_to_3", "migrate_3_to_4",
           "schema_version_of",
           "ProjectFormatError", "ProjectTooNewError"]
