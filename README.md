<p align="center">
  <img src="src/simple_pi_calculator/resources/app.png" width="96" height="96" alt="Simple PI Calculator icon">
</p>

<h1 align="center">Simple PI Calculator</h1>

<p align="center">
  A fast, first-order power-distribution-network (PDN) impedance estimator for early
  decoupling-capacitor planning on PCBs and multilayer organic (MLO) substrates.
</p>

---

## What it does

Simple PI Calculator computes and plots the PDN impedance magnitude **|Z(f)|** seen at an IC
**PAD** for each power net (PWR) of a board, given:

* the board/substrate **stack-up** (layer thicknesses, conductivity, Dk/Df),
* the **PWR/GND plane pair** referenced for each net,
* the **decoupling capacitors** assigned to that net (position, count, model), and
* the **via geometry** connecting decaps and the PAD to the planes.

It models the PDN as a chain of lumped/via impedances feeding a modal (cavity-resonator) model
of the plane pair, and reduces it to a single frequency-dependent impedance at the PAD using a
Schur-complement port reduction. It is a "what-if" planning tool, **not** a full-wave field
solver — see [Modelling summary](#modelling-summary) and [Limitations](#limitations) below.

## Features

* **Plane-pair cavity model** — modal Green's-function solution of the parallel-plate resonator
  (magnetic-wall boundary conditions), including dielectric loss (tanδ) and conductor skin-effect
  loss, with mode-count truncation and a quasi-static tail correction for speed and accuracy.
* **Automatic plane geometry** — plane width is a direct input; plane height and PAD/decap
  placement are derived from the decap-to-PAD distances you enter, so you don't need to draw a
  board outline.
* **Via modelling** — image partial-inductance via-pair loops (PWR/GND via pair) with skin-effect
  resistance, via-cluster port widening for multi-via decap/PAD connections, and an optional
  per-capacitor mounting inductance.
* **Decap models** — SPICE `.mod` subcircuits (R, L, C, mutual-inductance `K` elements) solved by
  AC modified nodal analysis, or measured Touchstone `.s2p` two-port data (series-through or
  shunt-through de-embedding).
* **Dummy Cap rows** — model two capacitors sharing one via set (common escape-routing pattern).
* **Interactive plots** — log-log |Z(f)| per PWR net, unit selectable (Ω / mΩ / µΩ), zoom/pan,
  marker readouts at 1 MHz / 10 MHz / 100 MHz, and an optional "plane only (no decaps)" curve.
* **Excel-based import** — stack-up, PWR list, and decap-assignment list import from `.xlsx` with
  fuzzy header matching, or edit directly in the GUI tables.
* **Project files & auto-save** — save/reload a named `*.spical.json` project; the full
  application state is also auto-saved continuously and restored on the next launch.
* **Export** — CSV/XLSX of frequency, Re Z, Im Z, |Z| per PWR net, and PNG export of the plot.
* **Built-in Help** — an in-app help browser covering every input, output, error/warning code, and
  the physics behind the model.

## Screenshots

*(placeholder — add screenshots of the main window, plot view, and stack-up editor here)*

## Install

Pre-built Windows installers are published on the
[**Releases**](../../releases) page of this repository.

1. Download the latest `SimplePICalculator-Setup-<version>.exe` from Releases.
2. Run it — per-user (no admin rights needed) or per-machine install, your choice.
3. Launch **Simple PI Calculator** from the Start menu. Example projects are installed alongside
   the application and linked from the Start menu group.

Supported platform: Windows 10/11 x64.

## Run from source

Requires Python 3.11 (PySide6 6.6–6.8 does not yet support 3.12+ on all platforms; development
also works on Linux/macOS).

```bash
git clone <this-repo-url>
cd simple-pi-calculator
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
python -m simple_pi_calculator
```

To run the test suite headlessly (no display needed):

```bash
pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen pytest -q
```

## Input file formats

Three `.xlsx` inputs (fuzzy column-header matching, so minor header variations are tolerated),
plus decap model files:

| Input | Format | Summary |
|---|---|---|
| Stack-up | `.xlsx` | One row per layer: number, name, thickness, conductivity (metal) or Dk/Df (dielectric) |
| PWR list | `.xlsx` or GUI table | Net name, PWR layer, GND layer, plane width |
| Decap assignment | `.xlsx` or GUI table | PWR net, distance to PAD, count, model file, optional Dummy Cap flag |
| Decap model | SPICE `.mod` (required support) or Touchstone `.s2p` (optional) | Two-terminal subcircuit or measured 2-port impedance |
| Project file | `*.spical.json` | Saves all inputs and file paths together |

Example files for all of the above are bundled in [`examples/`](examples/) and referenced by the
in-app **Help** pages, which document every column, unit, and validation rule in detail. Full
formulas and worked examples are in [`docs/DESIGN.md`](docs/DESIGN.md) §4 (Data formats).

## Modelling summary

The PDN chain modelled is:

```
Decap model ── decap via loop ── PWR/GND plane pair (cavity) ── PAD via loop ── PAD
```

Key physics, with references (see `docs/DESIGN.md` §10 for full citations):

* **Cavity resonator model** of the plane pair as a 2-D Helmholtz / magnetic-wall planar circuit,
  solved as a modal Green's-function port-impedance matrix — **Okoshi85**, **Lei99**,
  **Swaminathan07**, **Kim01**.
* **Surface impedance / skin effect** of finite-thickness plane conductors — **Ramo94**,
  **Wheeler42**.
* **Via-pair loop inductance** via the image partial-inductance method (two-conductor loop above
  the nearer plane + coaxial anti-pad segment through it) — **Grover46**, **Paul10**,
  **Bogatin18**, with an alternative Goldfarb–Pucel via-post model — **Goldfarb91**.
* **Decap SPICE subcircuits** solved by AC modified nodal analysis — **Ho75**, **Vlach94**,
  **Nagel75** (SPICE conventions).
* **Touchstone `.s2p` de-embedding** (series-through / shunt-through) via ABCD-parameter
  relations — **Pozar12**, **Novak00**, **Novak07**.
* **Port reduction** of loaded decap ports at the PAD observation port via a Schur complement of
  the cavity impedance matrix — **Swaminathan07**, **Novak07**.

## Limitations

Simple PI Calculator is a simplified, first-order **pre-design estimate**, not a field solver.
In particular (full list in `docs/DESIGN.md` §9):

* No VRM / DC source model — the impedance curve is capacitive (rises without bound) at low
  frequency; a real PDN flattens out below the VRM's control bandwidth.
* Rectangular, solid plane pairs only, referenced to a single explicit GND layer; no cut-outs,
  split planes, irregular outlines, or multi-cavity coupling.
* The plane outline and component coordinates are **synthesised** from the decap-to-PAD distances
  you enter (plane height = 1.4 × the largest distance), not measured from your real layout — read
  the plane-only curve and resonance frequencies as indicative, not exact.
* Mutual inductance between parallel via pairs, and between the two capacitors of a Dummy Cap
  pair, is neglected (optimistic).
* Components are modelled on the **Top** side only; no IC die/package model; no time-domain or
  target-impedance optimisation in v1.
* Dielectric constants (Dk/Df) are treated as frequency-independent; no surface roughness or
  radiation/fringing effects; validity degrades above a few GHz.

Read `docs/DESIGN.md` (the implementation-ready design document) and the in-app Help pages for the
full physics, validation rules, and numerical methods behind every result.

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Simple PI Calculator contributors.
