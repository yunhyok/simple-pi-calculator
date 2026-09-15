# Changelog

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
