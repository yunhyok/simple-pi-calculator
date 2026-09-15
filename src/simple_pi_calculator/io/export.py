"""Results export to CSV / XLSX (DESIGN.md §4.8, §5.3).

Works with any object exposing the ``PwrResult`` attributes of §5.2 (``name``, ``f_hz``,
``z_pad``, ``z_plane_only``, ``marker_f_hz``, ``marker_z``, ``info``), so this module does not
import the engine.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

from simple_pi_calculator import __version__

if TYPE_CHECKING:  # pragma: no cover
    from simple_pi_calculator.io.project_io import Project

_SHEET_INVALID = re.compile(r"[\[\]:*?/\\]")
_FILE_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
NUMBER_FORMAT = "%.9e"

COLUMNS = ("Frequency (Hz)", "Re Z (Ohm)", "Im Z (Ohm)", "|Z| (Ohm)")
PLANE_ONLY_COLUMN = "|Z| plane only (Ohm)"


def sheet_name_for(pwr_name: str, used: set[str] | None = None) -> str:
    """Excel sheet name (§4.8): invalid chars ``[]:*?/\\`` → ``_``, max 31 chars; made unique
    (case-insensitive) against ``used`` by a ``~N`` suffix."""
    base = _SHEET_INVALID.sub("_", pwr_name).strip("'") or "_"
    name = base[:31]
    if used is not None:
        n = 1
        while name.casefold() in {u.casefold() for u in used}:
            suffix = f"~{n}"
            name = base[: 31 - len(suffix)] + suffix
            n += 1
        used.add(name)
    return name


def _file_part(text: str) -> str:
    return _FILE_INVALID.sub("_", text).strip() or "_"


def _fmt(value: float) -> str:
    return NUMBER_FORMAT % value


def result_rows(result: Any) -> tuple[list[str], list[list[float]]]:
    """Column headers and numeric rows of one PWR result (§4.8)."""
    f = np.asarray(result.f_hz, dtype=float)
    z = np.asarray(result.z_pad, dtype=complex)
    headers = list(COLUMNS)
    cols = [f, z.real, z.imag, np.abs(z)]
    plane = getattr(result, "z_plane_only", None)
    if plane is not None:
        headers.append(PLANE_ONLY_COLUMN)
        cols.append(np.abs(np.asarray(plane, dtype=complex)))
    rows = np.column_stack(cols).tolist()
    return headers, rows


def marker_readouts(result: Any) -> list[tuple[float, float]]:
    """``(f_marker, |Z|)`` pairs of the exact marker values."""
    f = np.asarray(getattr(result, "marker_f_hz", []), dtype=float)
    z = np.asarray(getattr(result, "marker_z", []), dtype=complex)
    return [(float(fi), float(abs(zi))) for fi, zi in zip(f, z)]


def project_digest(project: "Project | None") -> str:
    """SHA-256 (first 16 hex chars) of the canonical project inputs (no session)."""
    if project is None:
        return "n/a"
    from simple_pi_calculator.io.project_io import project_to_dict

    doc = project_to_dict(project, None, None)
    doc.pop("app_version", None)
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _header_lines(result: Any, stem: str) -> list[str]:
    lines = [f"Simple PI Calculator {__version__} results",
             f"Project: {stem}",
             f"PWR: {result.name}",
             f"Exported (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"]
    for f, zabs in marker_readouts(result):
        lines.append(f"|Z| @ {f:.6g} Hz = {_fmt(zabs)} Ohm")
    info = getattr(result, "info", None) or {}
    for key, value in info.items():
        lines.append(f"{key} = {value}")
    return lines


def export_csv(results: Sequence[Any], folder: str, stem: str) -> list[str]:
    """One CSV per PWR: ``<stem>_<PWR>.csv`` in ``folder`` (§4.8). Returns the written paths.

    A header block of ``#``-prefixed lines precedes the column header row.
    """
    os.makedirs(folder, exist_ok=True)
    written: list[str] = []
    for result in results:
        path = os.path.join(folder, f"{_file_part(stem)}_{_file_part(result.name)}.csv")
        headers, rows = result_rows(result)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            for line in _header_lines(result, stem):
                fh.write(f"# {line}\n")
            writer = csv.writer(fh)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([_fmt(v) for v in row])
        written.append(path)
    return written


def export_xlsx(results: Sequence[Any], path: str, project: "Project | None") -> None:
    """One workbook: sheet ``Summary`` (inputs digest, marker readouts, info) plus one sheet per
    PWR (§4.8). Numbers are stored as floats; the text form in Summary uses ``%.9e``."""
    import openpyxl

    wb = openpyxl.Workbook()
    summary = wb.active
    summary.title = "Summary"
    used: set[str] = {"Summary"}
    stem = os.path.basename(path)
    summary.append(["Simple PI Calculator results"])
    summary.append(["App version", __version__])
    summary.append(["Exported (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")])
    summary.append(["File", stem])
    summary.append(["Inputs digest (SHA-256)", project_digest(project)])
    summary.append([])
    summary.append(["PWR", "Marker frequency (Hz)", "|Z| (Ohm)", "|Z| text"])
    for result in results:
        for f, zabs in marker_readouts(result):
            summary.append([result.name, f, zabs, _fmt(zabs)])
    summary.append([])
    for result in results:
        info = getattr(result, "info", None) or {}
        if info:
            summary.append([f"{result.name} info"])
            for key, value in info.items():
                summary.append(["", str(key), value if isinstance(value, (int, float, str))
                                else str(value)])

    for result in results:
        ws = wb.create_sheet(sheet_name_for(result.name, used))
        headers, rows = result_rows(result)
        ws.append(headers)
        for row in rows:
            ws.append([float(v) for v in row])

    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    wb.save(path)


__all__ = ["export_csv", "export_xlsx", "sheet_name_for", "result_rows", "marker_readouts",
           "project_digest"]
