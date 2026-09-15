#!/usr/bin/env python
"""Regenerate the bundled example Excel files and example project (DESIGN.md §4.2–§4.4, §4.7, §8).

Writes into ``examples/`` (or ``--out DIR``):

* ``stackup_6L.xlsx``   — sheet ``Stackup``, header in row 1 (§4.2)
* ``pwr_list.xlsx``     — sheet ``PWR`` (§4.3)
* ``decap_list.xlsx``   — sheet ``Decaps`` incl. ``Dummy Cap`` (§4.4)
* ``example_project.spical.json`` — all 11 layers, 2 PWRs, 4 decap rows (§4.7)

The decap model files referenced by name (``cap_0402_100nF.mod``, ``cap_0603_10uF.mod``) and
``cap_0402_100nF_series.s2p`` are maintained separately and are not written by this script.
Output is deterministic (fixed workbook timestamps).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import openpyxl  # noqa: E402

from simple_pi_calculator.core.types import DecapRow, LayerRow, PwrRow  # noqa: E402
from simple_pi_calculator.io.project_io import Project, save_project  # noqa: E402

FIXED_TIME = _dt.datetime(2026, 1, 1, 0, 0, 0)

#: §4.2 example stack-up: (number, name, thickness mm, conductivity S/m or None, Dk, Df)
STACKUP_ROWS: list[tuple[int, str, float, float | None, float, float]] = [
    (1, "TOP", 0.035, 5.8e7, 4.2, 0.02),
    (2, "PP1", 0.1, None, 4.2, 0.02),
    (3, "GND1", 0.035, 5.8e7, 4.3, 0.018),
    (4, "CORE1", 0.1, None, 4.3, 0.018),
    (5, "PWR1", 0.035, 5.8e7, 4.3, 0.018),
    (6, "PP2", 0.8, None, 4.4, 0.02),
    (7, "PWR2", 0.035, 5.8e7, 4.3, 0.018),
    (8, "CORE2", 0.1, None, 4.3, 0.018),
    (9, "GND2", 0.035, 5.8e7, 4.2, 0.02),
    (10, "PP3", 0.1, None, 4.2, 0.02),
    (11, "BOTTOM", 0.035, 5.8e7, 4.2, 0.02),
]

#: §4.3 example PWR list: (name, PWR layer, GND layer, width mm)
PWR_ROWS: list[tuple[str, int, int, float]] = [
    ("VDD_CORE", 5, 3, 60.0),
    ("VDD_IO", 7, 9, 30.0),
]

#: §4.4 example decap list: (PWR, file, count, distance mm, dummy)
DECAP_ROWS: list[tuple[str, str, int, float, bool]] = [
    ("VDD_CORE", "cap_0402_100nF.mod", 10, 8.0, False),
    ("VDD_CORE", "cap_0603_10uF.mod", 4, 15.0, False),
    ("VDD_IO", "cap_0402_100nF.mod", 4, 5.0, True),
    ("VDD_IO", "cap_0603_10uF.mod", 1, 10.0, False),
]


def _new_workbook(sheet: str) -> tuple[openpyxl.Workbook, object]:
    wb = openpyxl.Workbook()
    wb.properties.creator = "Simple PI Calculator tools/make_examples.py"
    wb.properties.created = FIXED_TIME
    wb.properties.modified = FIXED_TIME
    ws = wb.active
    ws.title = sheet
    return wb, ws


def _save_deterministic(wb: openpyxl.Workbook, path: str) -> None:
    """Save, then rewrite the zip with fixed entry timestamps and core-property dates."""
    import re
    import zipfile

    wb.save(path)
    with zipfile.ZipFile(path) as zin:
        entries = [(info.filename, zin.read(info.filename)) for info in zin.infolist()]
    stamp = FIXED_TIME.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries:
            if name == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(<)",
                              rb"\g<1>" + stamp + rb"\g<2>", data)
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zout.writestr(info, data)


def write_stackup_xlsx(path: str) -> None:
    wb, ws = _new_workbook("Stackup")
    ws.append(["Layer Number", "Layer Name", "Thickness(mm)", "Conductivity(S/m)", "Dk", "Df"])
    for number, name, t_mm, sigma, dk, df in STACKUP_ROWS:
        ws.append([number, name, t_mm, sigma, dk, df])
    _save_deterministic(wb, path)


def write_pwr_xlsx(path: str) -> None:
    wb, ws = _new_workbook("PWR")
    ws.append(["PWR Name", "Layer Number", "GND Layer Number", "PWR Plane Width"])
    for name, pwr, gnd, width in PWR_ROWS:
        ws.append([name, pwr, gnd, int(width) if float(width).is_integer() else width])
    _save_deterministic(wb, path)


def write_decap_xlsx(path: str) -> None:
    wb, ws = _new_workbook("Decaps")
    ws.append(["PWR Name", "Decap File Name", "Number of Decaps", "Distance to PAD (mm)",
               "Dummy Cap"])
    for pwr, model, count, dist, dummy in DECAP_ROWS:
        ws.append([pwr, model, count, int(dist) if float(dist).is_integer() else dist,
                   "Yes" if dummy else "No"])
    _save_deterministic(wb, path)


def example_project(folder: str) -> Project:
    """The §4.7 example project with absolute in-memory paths under ``folder``."""
    folder = os.path.abspath(folder)
    project = Project()
    project.vias.vias_per_pad = 1  # one PWR via on the PWR pad + one GND via on the GND pad
    project.vias.pad_via_count = 1  # one PWR/GND via pair at the observation PAD
    project.stackup_source_path = os.path.join(folder, "stackup_6L.xlsx")
    project.layers = [LayerRow(number=n, name=name, thickness_mm=t, conductivity_s_per_m=sigma,
                               dk=dk, df=df) for n, name, t, sigma, dk, df in STACKUP_ROWS]
    project.pwr_source_path = os.path.join(folder, "pwr_list.xlsx")
    project.pwr_rows = [PwrRow(name=name, pwr_layer=p, gnd_layer=g, width_mm=w, enabled=True)
                        for name, p, g, w in PWR_ROWS]
    project.decap_source_path = os.path.join(folder, "decap_list.xlsx")
    project.decap_rows = [DecapRow(pwr_name=pwr, model_file=os.path.join(folder, model),
                                   count=count, distance_mm=dist, dummy=dummy)
                          for pwr, model, count, dist, dummy in DECAP_ROWS]
    return project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(ROOT, "examples"),
                        help="output folder (default: examples/)")
    args = parser.parse_args(argv)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    write_stackup_xlsx(os.path.join(out, "stackup_6L.xlsx"))
    write_pwr_xlsx(os.path.join(out, "pwr_list.xlsx"))
    write_decap_xlsx(os.path.join(out, "decap_list.xlsx"))
    save_project(example_project(out), os.path.join(out, "example_project.spical.json"))
    for name in ("stackup_6L.xlsx", "pwr_list.xlsx", "decap_list.xlsx",
                 "example_project.spical.json"):
        print(os.path.join(out, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
