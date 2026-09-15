"""Issues (errors / warnings / infos) shared by all layers (DESIGN.md §5.2, Appendix A).

Every validation problem is reported as an :class:`Issue` with a stable code such as
``E_STACK_DK``. Codes starting with ``E_`` are errors, ``W_`` warnings, ``I_`` infos.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable


class Severity(enum.Enum):
    """Issue severity (§5.2)."""

    INFO = 0
    WARNING = 1
    ERROR = 2


@dataclass(frozen=True)
class Issue:
    """One validation message (§5.2)."""

    code: str
    severity: Severity
    message: str
    source: str | None = None
    location: str | None = None

    def __str__(self) -> str:
        parts = [f"[{self.severity.name}] {self.code}: {self.message}"]
        if self.source:
            parts.append(f"source={self.source}")
        if self.location:
            parts.append(f"location={self.location}")
        return " | ".join(parts)


class InputError(Exception):
    """Raised when inputs contain errors; carries the offending issues (§5.2)."""

    def __init__(self, issues: Iterable[Issue]):
        self.issues: list[Issue] = list(issues)
        text = "; ".join(f"{i.code}: {i.message}" for i in self.issues) or "input error"
        super().__init__(text)


@dataclass
class IssueCollector:
    """Accumulates issues during import, validation and computation (§5.2)."""

    issues: list[Issue] = field(default_factory=list)

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        source: str | None = None,
        location: str | None = None,
    ) -> Issue:
        issue = Issue(code, severity, message, source, location)
        self.issues.append(issue)
        return issue

    def error(self, code: str, message: str, source: str | None = None,
              location: str | None = None) -> Issue:
        return self.add(code, Severity.ERROR, message, source, location)

    def warning(self, code: str, message: str, source: str | None = None,
                location: str | None = None) -> Issue:
        return self.add(code, Severity.WARNING, message, source, location)

    def info(self, code: str, message: str, source: str | None = None,
             location: str | None = None) -> Issue:
        return self.add(code, Severity.INFO, message, source, location)

    def extend(self, issues: Iterable[Issue]) -> None:
        self.issues.extend(issues)

    def has_errors(self) -> bool:
        return any(i.severity is Severity.ERROR for i in self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def raise_if_errors(self) -> None:
        errs = self.errors
        if errs:
            raise InputError(errs)

    def __len__(self) -> int:
        return len(self.issues)


# -------------------------------------------------------------------------------------------------
# Project-file exceptions (§5.3, §5.8.4). Defined here (Qt-free, import-cycle-free) and re-exported
# by ``io.project_io`` and ``io.migrations``.
# -------------------------------------------------------------------------------------------------
class ProjectFormatError(Exception):
    """Not JSON / wrong ``format`` / structural type error / missing migration step (``E_PROJECT_FORMAT``)."""

    code = "E_PROJECT_FORMAT"

    def __init__(self, message: str, source: str | None = None):
        super().__init__(message)
        self.issue = Issue(self.code, Severity.ERROR, message, source)


class ProjectTooNewError(Exception):
    """``schema_version`` newer than this application supports (``E_PROJECT_NEWER``)."""

    code = "E_PROJECT_NEWER"

    def __init__(self, schema_version: int, message: str | None = None, source: str | None = None):
        self.schema_version = schema_version
        text = message or (
            f"The project was written with schema version {schema_version}, which is newer than "
            "this application supports."
        )
        super().__init__(text)
        self.issue = Issue(self.code, Severity.ERROR, text, source)
