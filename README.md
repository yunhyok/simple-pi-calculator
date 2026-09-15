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
* the **decoupling capacitors** assigned to that net (distance to the PAD, count, model), and
* the **via geometry** connecting decaps and the PAD to the planes.

It models the PDN as a chain of lumped/via impedances feeding a modal (cavity-resonator) model
of the plane pair, and reduces it to a single frequency-dependent impedance at the PAD using a
Schur-complement port reduction. It is a "what-if" planning tool, **not** a full-wave field
solver — see [Modelling summary](#modelling-summary), [Modelling assumptions](#modelling-assumptions)
and [Limitations](#limitations) below.

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
* **Decap models** — SPICE subcircuits (`.mod`, `.lib`, `.sp`, `.cir`, `.sub`, `.inc`; R, L, C and
  coupled-inductor `K` elements, `.PARAM`/`PARAMS:` expressions, nested subcircuits) solved by AC
  modified nodal analysis, or measured Touchstone v1 `.s2p` two-port data converted to impedance
  for a series-through or shunt-through fixture.
* **Dummy Cap rows** — model two capacitors sharing one via set (common escape-routing pattern).
* **Interactive plots** — log-log |Z(f)| per PWR net plus an overview of all nets, unit selectable
  (Ω / mΩ / µΩ), zoom/pan, hover readout, fixed marker lines at 1 MHz / 10 MHz / 100 MHz with exact
  |Z| readouts (a readout table lists them per net; "n/a" when a marker lies outside the sweep), and
  an optional "plane only (no decaps)" curve.
* **Excel-based import** — stack-up, PWR list, and decap-assignment list import from `.xlsx` with
  fuzzy header matching (units in the header, numbers stored as text, title rows, merged cells,
  extra sheets), or edit directly in the GUI tables.
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

1. Download the latest `SimplePICalculator-Setup-<version>.exe` from Releases (a zipped portable
   build `SimplePICalculator-<version>-win64.zip` is attached as well).
2. Run it — per-user (no admin rights needed) or per-machine install, your choice.
3. Launch **Simple PI Calculator** from the Start menu. Example projects are installed alongside
   the application and linked from the Start menu group.

Supported platform: Windows 10/11 x64.

## Run from source

Requires Python 3.11 (the supported range pinned in `pyproject.toml`: Python 3.11, PySide6 6.6–6.8);
development also works on Linux/macOS.

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

Command line: `simple-pi-calculator [project.spical.json] [--no-restore] [--self-test
[--self-test-report FILE]]`. `--no-restore` starts with defaults instead of the auto-save;
`--self-test` checks the installation (modules, bundled help/examples, a headless computation of
the example project) and exits with 0 on success — the windowed Windows build prints nothing, so
use `--self-test-report` to get the report in a file.

The auto-save (`autosave.spical.json`, with a backup) and the log file `logs/app.log` live in
`%APPDATA%\SimplePICalculator` on Windows and `~/.local/share/SimplePICalculator` on Linux
(override with the environment variable `SPICAL_APPDATA_DIR`).

## Input file formats

Three `.xlsx` inputs plus decap model files. All tables can also be edited in the GUI, and a
project file (`*.spical.json`) stores everything together (tables are embedded, file paths are
stored relative to the project file). Example files for all inputs are bundled in
[`examples/`](examples/); the in-app **Help** documents every rule and message code, and
[`docs/DESIGN.md`](docs/DESIGN.md) §4 is the normative specification.

**Excel rules common to all three tables.** The sheet is the first one whose name contains the
table keyword (`stack`, `pwr`/`power`, `decap`/`cap`), else the active sheet, else the first
other sheet with a recognisable header. The header row is searched in rows 1–20 (title rows above
it are fine). Headers are matched case-insensitively ignoring spaces and punctuation; text in
`( )` or `[ ]` is the unit, and non-ASCII text such as Korean labels is ignored (so
`두께 Thickness (mm)` works). Length
units: mm (default), um/µm/micron, mil/mils/thou, in/inch, m. Numbers may be stored as text
(`"5.8E7"`, `"0,035"`). Completely empty rows are skipped; merged cells count for every cell they
cover. Formula cells need a cached value (open and save the file in Excel once).

**Stack-up** (one row per layer, layer 1 = top):

| Column | Required | Example headers | Content |
|---|---|---|---|
| Layer number | yes | `Layer Number`, `Layer No.`, `Layer #`, `Layer` | integer ≥ 1 |
| Layer name | no | `Layer Name` | text |
| Thickness | yes | `Thickness (mm)`, `Thickness (um)`, `Thk (mil)` | > 0 |
| Conductivity | yes (cells may be empty) | `Conductivity (S/m)`, `Conductivity [S/m]`, `Sigma` | S/m; > 0 → metal, empty/0/`-` → dielectric |
| Dk | yes | `Dk`, `Er`, `εr`, `Dk@1GHz`, `Dielectric Constant` | ≥ 1 for dielectric rows |
| Df | yes | `Df`, `tan δ`, `Loss Tangent`, `Df@1GHz` | 0…1 (missing → 0 with a warning) |

**PWR list:**

| Column | Required | Example headers | Content |
|---|---|---|---|
| PWR name | yes | `PWR Name`, `Net Name`, `Rail` | unique, case-sensitive |
| PWR layer | yes | `Layer Number`, `PWR Layer` | metal layer number |
| GND layer | yes | `GND Layer Number`, `Ground Layer` | metal layer number (the reference plane) |
| Width | yes | `PWR Plane Width`, `Width (mm)` | plane width W > 0 |

Columns containing `height`, `length` or `pad` are ignored with a warning — the plane height and
PAD position are derived (see below).

**Decap assignment list:**

| Column | Required | Example headers | Content |
|---|---|---|---|
| PWR name | yes | `PWR Name`, `Net`, `Rail` | must exist in the PWR list |
| Model file | yes | `Decap File Name`, `Decap Model`, `Model File` | path, absolute or relative |
| Count | yes | `Number of Decaps`, `Decap Count`, `Qty` | integer ≥ 1 |
| Distance | yes | `Distance to PAD (mm)`, `Distance(mm)` | > 0, along the plane from the PAD |
| Subckt | no | `Subckt`, `Subcircuit` | subcircuit name when a file holds several |
| S2P mode | no | `S2P Mode`, `Config` | `series` / `shunt` (empty = project default, series) |
| Dummy Cap | no | `Dummy Cap`, `Dummy` | Yes/No, 1/0, TRUE/FALSE, x; absent = No |

Relative model paths are searched next to the decap Excel file, then next to the project file,
then in the *Model search folder* (Vias ▸ Advanced).

**Decap models.** SPICE: a two-pin `.SUBCKT` (pin 1 = PWR, pin 2 = GND) with R, L, C, K and `X`
instances; SPICE suffixes (`30m`, `0.45nH`, `100nF`, `1MEG`; note `1F` = 1 fF as in SPICE);
comments `*`, `;`, `$`; `+` continuation lines; UTF-8 (with or without BOM) or Latin-1; CRLF or LF.
Touchstone: v1 `.s2p` with `# HZ|KHZ|MHZ|GHZ S MA|DB|RI R z0`.

**Other inputs (GUI):** via drill and anti-pad diameter, via pitch (PWR–GND via spacing, default
1.0 mm), vias per decap (even, default 2), PAD via count (default 1), sweep 100 kHz–1 GHz with 400
log points by default (1 kHz ≤ f ≤ 20 GHz, 10–5000 points).

## Modelling summary

The PDN chain modelled is:

```
Decap model ── decap via loop ── PWR/GND plane pair (cavity) ── PAD via loop ── PAD
```

Key physics, with references (see `docs/DESIGN.md` §10 for full citations):

* **Cavity resonator model** of the plane pair as a 2-D Helmholtz / magnetic-wall planar circuit,
  solved as a modal Green's-function port-impedance matrix — **Okoshi85**, **Lei99**,
  **Swaminathan07**, **Kim01**.
* **Surface impedance / skin effect** of finite-thickness plane conductors — **Ramo94** (skin
  depth also **Wheeler42**).
* **Via-pair loop inductance** via the image partial-inductance method (two-conductor loop above
  the nearer plane + coaxial anti-pad segment through it) — **Grover46**, **Paul10**,
  **Bogatin18**, with an alternative Goldfarb–Pucel via-post model — **Goldfarb91**.
* **Decap SPICE subcircuits** solved by AC modified nodal analysis — **Ho75**, **Vlach94**,
  **Nagel75** (SPICE conventions).
* **Touchstone `.s2p` to impedance** for series-through / shunt-through fixtures via ABCD-parameter
  relations — **Pozar12**, **Novak00**, **Novak07**.
* **Port reduction** of loaded decap ports at the PAD observation port via a Schur complement of
  the cavity impedance matrix — **Swaminathan07**, **Novak07**.

## Modelling assumptions

These are the choices the tool makes for you; read results with them in mind (details and
equations in `docs/DESIGN.md` §2 and in Help ▸ Physics).

* **Synthetic plane and placement.** For each PWR net the plane is a W × H rectangle with the real
  width W and a derived height H = 1.4 × D_ref, where D_ref is the largest decap distance (H = W when
  the net has no decaps). The PAD sits at (W/2, 0.2·D_ref); each decap row lies at
  y = 0.2·D_ref + distance, its via sets spread evenly across the width with a 10 % margin (several
  sub-rows if they do not fit). Plane capacitance and resonances are those of this synthetic plane.
* **All components on the Top side.** Vias run from the top surface to the nearer of the PWR/GND
  planes.
* **Dielectric between the planes** is the series combination of all intermediate dielectric
  layers (exact complex form); intermediate metal layers are assumed cleared (warning).
* **Plane conductors** use the finite-thickness skin-effect surface impedance; copper is smooth.
* **Via pair** = one PWR via + one GND via at the via pitch: image partial-inductance loop from the
  top surface to the nearer plane plus a coaxial anti-pad segment through that plane, and a
  skin-effect barrel resistance (plating 25 µm by default). Several via pairs per decap or at the PAD
  are ideal parallel paths above the planes (no mutual inductance) and widen the cavity port as a via
  cluster on a √2·pitch checkerboard.
* **Cavity ports** are squares with the geometric-mean-distance equivalent width of the via or via
  cluster (1.118 × drill for a single via).
* **Decap ports** are loaded by the decap impedance (plus the optional mounting inductance per
  capacitor, 0 nH by default — optimistic) in series with the via-set impedance. A **Dummy Cap** row
  of N capacitors uses ceil(N/2) via sets carrying two capacitors each (the last one a single
  capacitor when N is odd).
* **Result** = cavity impedance at the PAD port with all decap ports loaded (Schur-complement
  reduction) plus the PAD via impedance. No VRM, package or die: the curve is capacitive at low
  frequency. Marker values are evaluated exactly at 1, 10 and 100 MHz, not interpolated.

## Performance

The engine evaluates the cavity model with grouped BLAS products (ports on a decap row share their
modal factors), computes frequency blocks and PWR nets on worker threads (numpy releases the GIL),
pins BLAS to one thread to avoid oversubscription, computes the exact condition-number SVD only
where a cheap estimate cannot rule out an ill-conditioned reduction, and caches the plane impedance matrix between
runs, so changing only decap models, the via model or the mounting inductance is fast. The thread
count is set in **Vias → Advanced → Worker threads** (Auto = all logical CPUs); it never changes the
results. Details: `docs/DESIGN.md` §3.9.

Measured with `python tools/bench.py` (best of 3, cold caches, 2-core Xeon, numpy 2.4 / OpenBLAS):

| Scenario | v0.1.0 | now, 1 thread | now, Auto (2 threads) | re-run after a decap-only change |
|---|---|---|---|---|
| Bundled example (2 nets) | 0.05 s | 0.023 s | 0.025 s | 0.008 s |
| Large MLO (80 mm plane, 150 caps / 121 ports, 400 points) | 2.9–3.6 s | 0.27 s | 0.19 s | 0.09 s |
| Many nets (6 nets, 3 decap rows each) | 0.22–0.27 s | 0.08 s | 0.06 s | 0.02 s |

Results are identical to v0.1.0 within 2e-10 relative (golden example: 7e-11).

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
