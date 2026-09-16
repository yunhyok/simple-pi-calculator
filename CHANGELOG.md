# Changelog

## 0.3.1 — 2026-09-16

- Fixed: the main splitter could not widen the input panel past the width of the "Curves" checkbox row; the row now wraps, the readout table scrolls, and the results pane has a 320 px minimum.
- Changed: Dk/Df on metal stack-up rows are treated as the fill-in material properties (normal stack-up data) — one informational note per stack-up (`I_STACK_FILL_DKDF`) instead of a note per layer. The PI model does not use them.

## 0.3.0 — 2026-09-15

### Modelling
- **Distance distribution** (Decaps tab, applies to every decap row): *Fixed* (default, the 0.2.0 behaviour, results bit-identical) or *Normal (±1σ)* — every via set of a row is placed at D + σ·z with z from a standard normal distribution truncated to [−1, 1] (σ absolute in mm, default 0.5 mm). Sampling is reproducible for a project-stored integer seed (default 12345, "New seed" button): one random stream per computation, rows in table order, via sets in port order; a Dummy Cap via set (two capacitors) gets one sample. Ports keep their x pattern and move in y by d − D; D_ref = max D + σ, so the plane size does not depend on the seed (DESIGN §2.5.5).
- Project schema 4: `decaps.distance_mode`, `decaps.sigma_mm`, `decaps.seed`; schema-3 files are migrated to fixed mode.

### Results
- `PwrResult.sampled_distances` lists (row, via set, distance mm) of every decap port; the PWR Nets preview shows the scattered via sets with a hover tooltip; `I_DIST_SAMPLED` (σ, seed, min/mean/max per PWR) in Messages; CSV, Touchstone and XLSX headers record the distribution. New codes `E_DIST_MODE`, `E_DIST_SIGMA`, `E_DIST_SEED`, `W_DIST_SIGMA_LARGE`.

### Performance
- The cavity cache key includes mode, σ, seed and the sampled distances. Normal mode cannot group ports of a row by their common y (grouping falls back to exact port factors, usually along x): the large 121-port benchmark takes 0.75 s instead of 0.29 s (static sums 0.07 → 0.53 s); the bundled example is unaffected in practice. `tools/bench.py --distance normal` measures it.

## 0.2.0 — 2026-09-15

### Modelling
- Per-PWR **Number of PADs** column (PWR list, Excel header "Number of PADs" / "PAD Count"). Pads form a row at the PAD end of the plane, each with its own via set, and are combined in parallel at an ideal common node (DESIGN §2.5, §2.8). N_pad = 1 reproduces the 0.1.0 results exactly.
- **Vias per decap pad** replaces "Vias per decap": the number of parallel vias on each decap pad (1, 2, 4 …). Project files from 0.1.0 are migrated automatically (schema 3).

### Performance
- Modal sums grouped per decap row, BLAS-backed Z(f) assembly, worker-thread pools over frequency chunks and PWR nets, result caches between runs, cheap rcond screening. Large planes compute 6–15× faster; new "Worker threads" setting (Advanced).

### Export and plot
- Export results as CSV (one file or per PWR), Touchstone `.s1p` per PWR or one uncoupled `.sNp` (S or Z, RI/MA, R = 1 Ω default), and all plots as PNG/SVG at a chosen size.
- **Reset view** button, View menu action, right-click entry and **Ctrl+D** (also Ctrl+0). Duplicate Row moved to Ctrl+Shift+D.

### Fixes
- Run/Save now commit an open cell editor first (edits typed but not confirmed were previously ignored).
- Dummy Cap / Enabled checkboxes toggle on a click anywhere in the cell.
- Decap model cache keyed by file content hash; nets that fail because of a model file are reported per net and skipped exports are warned.

## 0.1.0 — 2026-09-15
First release.
