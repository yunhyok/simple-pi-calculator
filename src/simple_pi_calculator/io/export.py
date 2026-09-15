"""Results export to CSV / XLSX / Touchstone v1 (DESIGN.md §4.8, §5.3).

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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

from simple_pi_calculator import __version__
from simple_pi_calculator.core.touchstone import touchstone_extension, write_touchstone_v1, z_to_s

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


_RESERVED_WIN = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


def safe_file_name(text: str, used: set[str] | None = None) -> str:
    """File-name stem safe on Windows/macOS/Linux: invalid characters → ``_``, no trailing dots or
    spaces, reserved device names prefixed, at most 100 characters; made unique
    (case-insensitive) against ``used`` by a ``_N`` suffix."""
    base = _FILE_INVALID.sub("_", str(text)).strip().rstrip(". ") or "_"
    if base.split(".")[0].upper() in _RESERVED_WIN:
        base = "_" + base
    base = base[:100]
    name = base
    if used is not None:
        folded = {u.casefold() for u in used}
        n = 2
        while name.casefold() in folded:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
    return name


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


def result_n_pads(result: Any) -> int:
    """N_pad of a result (``info["n_pads"]``, else the placement, else 1; §2.8)."""
    info = getattr(result, "info", None) or {}
    if "n_pads" in info:
        return int(info["n_pads"])
    return int(getattr(getattr(result, "placement", None), "n_pads", 1) or 1)


def distance_summary_line(result: Any) -> str:
    """One-line description of the decap distance distribution of a result (§2.5.5, §4.8)."""
    dist = getattr(result, "distance", None)
    if dist is None or getattr(dist, "mode", "fixed") == "fixed":
        return "Distance distribution: fixed (every capacitor at its row distance)"
    values = [float(d) for _, _, d in (getattr(result, "sampled_distances", None) or [])]
    stats = (f"sampled min/mean/max = {min(values):.4f}/{sum(values) / len(values):.4f}/"
             f"{max(values):.4f} mm over {len(values)} via set(s)") if values else "no decap ports"
    return (f"Distance distribution: normal truncated to +/-1 sigma, sigma = "
            f"{float(dist.sigma_m) * 1e3:.6g} mm, seed = {int(dist.seed)}; {stats}")


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
             f"Number of PADs: {result_n_pads(result)}",
             distance_summary_line(result),
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


def _common_frequency(results: Sequence[Any]) -> np.ndarray:
    if not results:
        raise ValueError("no results to export")
    f0 = np.asarray(results[0].f_hz, dtype=float)
    for res in results[1:]:
        f = np.asarray(res.f_hz, dtype=float)
        if f.shape != f0.shape or not np.allclose(f, f0, rtol=1e-12, atol=0.0):
            raise ValueError(f"PWR {res.name!r} has a different frequency grid than "
                             f"{results[0].name!r}; export one file per PWR instead")
    return f0


def combined_csv_rows(results: Sequence[Any]) -> tuple[list[str], list[list[float]]]:
    """Headers and rows of the single-file CSV: ``Frequency (Hz)`` then, per PWR,
    ``<PWR> |Z| (Ohm)``, ``<PWR> Re Z (Ohm)``, ``<PWR> Im Z (Ohm)`` (and
    ``<PWR> |Z| plane only (Ohm)`` when present). All PWRs must share one frequency grid."""
    f = _common_frequency(results)
    headers = ["Frequency (Hz)"]
    cols: list[np.ndarray] = [f]
    for res in results:
        z = np.asarray(res.z_pad, dtype=complex)
        headers += [f"{res.name} |Z| (Ohm)", f"{res.name} Re Z (Ohm)", f"{res.name} Im Z (Ohm)"]
        cols += [np.abs(z), z.real, z.imag]
        plane = getattr(res, "z_plane_only", None)
        if plane is not None:
            headers.append(f"{res.name} {PLANE_ONLY_COLUMN}")
            cols.append(np.abs(np.asarray(plane, dtype=complex)))
    return headers, np.column_stack(cols).tolist()


def export_csv_combined(results: Sequence[Any], path: str, stem: str = "results") -> str:
    """All PWRs in one CSV file (§4.8); ``#`` header block with the marker readouts of each PWR."""
    headers, rows = combined_csv_rows(results)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(f"# Simple PI Calculator {__version__} results\n")
        fh.write(f"# Project: {stem}\n")
        fh.write(f"# Exported (UTC): {_utc_now()}\n")
        for res in results:
            fh.write(f"# {res.name}: Number of PADs = {result_n_pads(res)}\n")
            fh.write(f"# {res.name}: {distance_summary_line(res)}\n")
            for f, zabs in marker_readouts(res):
                fh.write(f"# {res.name}: |Z| @ {f:.6g} Hz = {_fmt(zabs)} Ohm\n")
        writer = csv.writer(fh)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_fmt(v) for v in row])
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# Touchstone
# ---------------------------------------------------------------------------------------------
@dataclass
class TouchstoneOptions:
    """Touchstone export settings (§4.8): parameter ``S`` or ``Z``, data format ``RI`` or ``MA``,
    reference resistance ``r_ref`` in Ω (PDN convention: 1 Ω). Frequencies are always in Hz."""

    parameter: str = "S"
    data_format: str = "RI"
    r_ref: float = 1.0


def geometry_summary(project: "Project | None", pwr_name: str) -> list[str]:
    """Short text description of one PWR net's inputs for file comments."""
    if project is None:
        return []
    row = next((r for r in project.pwr_rows if r.name == pwr_name), None)
    if row is None:
        return []
    names = {layer.number: (layer.name or f"L{layer.number}") for layer in project.layers}
    lines = [f"  PWR layer {row.pwr_layer} ({names.get(row.pwr_layer, '?')}), GND layer "
             f"{row.gnd_layer} ({names.get(row.gnd_layer, '?')}), width {row.width_mm:g} mm, "
             f"{int(getattr(row, 'n_pads', 1))} PAD(s)"]
    decaps = [d for d in project.decap_rows if d.pwr_name == pwr_name and d.enabled]
    if decaps:
        parts = [f"{d.count} x {os.path.basename(d.model_file)} @ {d.distance_mm:g} mm"
                 + (" (dummy)" if d.dummy else "") for d in decaps]
        lines.append("  Decaps: " + "; ".join(parts))
    else:
        lines.append("  Decaps: none")
    vias = getattr(project, "vias", None)
    if vias is not None:
        lines.append(f"  Vias: drill {vias.drill_diameter_mm:g} mm, antipad "
                     f"{vias.antipad_diameter_mm:g} mm, pitch {vias.via_pitch_mm:g} mm, "
                     f"{vias.vias_per_pad} per decap pad, {vias.pad_via_count} via pair(s) per "
                     "observation PAD")
    return lines


def _touchstone_comments(results: Sequence[Any], stem: str, options: TouchstoneOptions,
                         project: "Project | None", combined: bool) -> list[str]:
    lines = [f"Simple PI Calculator {__version__}",
             f"Project: {stem}",
             f"Exported (UTC): {_utc_now()}",
             "Quantity: impedance Z at the PAD of each PWR net"]
    if options.parameter.upper() == "S":
        lines.append(f"S = (Z - R)/(Z + R) with R = {options.r_ref:.12g} Ohm")
    else:
        lines.append(f"Z values are normalised to R = {options.r_ref:.12g} Ohm "
                     "(Touchstone v1 convention)")
    if combined:
        lines.append("Ports are UNCOUPLED: off-diagonal parameters are 0 by construction")
        lines.append("(each PWR net was computed separately; plane-to-plane coupling is not "
                     "modelled).")
    for k, res in enumerate(results, start=1):
        lines.append(f"Port {k}: PWR {res.name}" if combined else f"PWR: {res.name}")
        lines.append(f"  Number of PADs: {result_n_pads(res)} (joined at an ideal common node)")
        lines.append(f"  {distance_summary_line(res)}")
        lines.extend(geometry_summary(project, res.name))
        info = getattr(res, "info", None) or {}
        if info:
            lines.append("  " + ", ".join(f"{key}={value}" for key, value in info.items()))
    return lines


def _parameter_matrix(results: Sequence[Any], options: TouchstoneOptions) -> np.ndarray:
    n = len(results)
    f = _common_frequency(results)
    data = np.zeros((f.size, n, n), dtype=complex)
    for k, res in enumerate(results):
        z = np.asarray(res.z_pad, dtype=complex)
        data[:, k, k] = z_to_s(z, options.r_ref) if options.parameter.upper() == "S" else z
    return data


def export_touchstone_per_pwr(results: Sequence[Any], folder: str, stem: str,
                              options: TouchstoneOptions | None = None,
                              project: "Project | None" = None) -> list[str]:
    """One 1-port ``<stem>_<PWR>.s1p`` per PWR (§4.8). Returns the written paths."""
    options = options or TouchstoneOptions()
    os.makedirs(folder, exist_ok=True)
    written: list[str] = []
    used: set[str] = set()
    for res in results:
        name = safe_file_name(f"{stem}_{res.name}", used)
        path = os.path.join(folder, name + touchstone_extension(1))
        write_touchstone_v1(path, np.asarray(res.f_hz, dtype=float),
                            _parameter_matrix([res], options),
                            parameter=options.parameter, data_format=options.data_format,
                            r_ref=options.r_ref,
                            comments=_touchstone_comments([res], stem, options, project, False))
        written.append(path)
    return written


def touchstone_combined_path(path: str, n_ports: int) -> str:
    """``path`` with its extension replaced by ``.s<N>p`` (an existing ``.sNp``-like extension is
    replaced, other extensions are kept and the Touchstone one appended)."""
    ext = touchstone_extension(n_ports)
    root, old = os.path.splitext(path)
    if re.fullmatch(r"\.s\d+p", old, flags=re.IGNORECASE):
        return root + ext
    return path + ext


def export_touchstone_combined(results: Sequence[Any], path: str, stem: str,
                               options: TouchstoneOptions | None = None,
                               project: "Project | None" = None) -> str:
    """One N-port file with N = number of PWRs as uncoupled ports (off-diagonal = 0), extension
    ``.s<N>p``, N ≤ 99 (§4.8). All PWRs must share one frequency grid. Returns the path."""
    options = options or TouchstoneOptions()
    results = list(results)
    if not results:
        raise ValueError("no results to export")
    path = touchstone_combined_path(path, len(results))
    data = _parameter_matrix(results, options)
    write_touchstone_v1(path, np.asarray(results[0].f_hz, dtype=float), data,
                        parameter=options.parameter, data_format=options.data_format,
                        r_ref=options.r_ref,
                        comments=_touchstone_comments(results, stem, options, project, True))
    return path


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
        summary.append([result.name, distance_summary_line(result)])
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


__all__ = ["export_csv", "export_csv_combined", "combined_csv_rows", "export_xlsx",
           "sheet_name_for", "safe_file_name", "result_rows", "marker_readouts", "project_digest",
           "TouchstoneOptions", "export_touchstone_per_pwr", "export_touchstone_combined",
           "touchstone_combined_path", "geometry_summary", "distance_summary_line"]
