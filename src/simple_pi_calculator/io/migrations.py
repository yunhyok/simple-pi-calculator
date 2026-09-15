"""Project-file schema migration chain (DESIGN.md §5.8.4).

Each schema bump adds ``MIGRATIONS[n]`` converting a version-``n`` document into version ``n+1``;
functions are pure (they must not mutate their argument). ``migrate`` reads
``CURRENT_SCHEMA_VERSION`` and ``MIGRATIONS`` from this module at call time, so tests can
monkeypatch them.
"""

from __future__ import annotations

import copy
from typing import Callable

from simple_pi_calculator.errors import IssueCollector, ProjectFormatError, ProjectTooNewError

CURRENT_SCHEMA_VERSION: int = 1
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


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


__all__ = ["CURRENT_SCHEMA_VERSION", "MIGRATIONS", "migrate", "schema_version_of",
           "ProjectFormatError", "ProjectTooNewError"]
