# Changelog

## 0.4.1 — 2026-09-23

### Fixed
- **Editing a decap cell changed the PWR by itself.** Every edit (a decap cell, the distance distribution, a PWR / via / stack-up value) refreshed the derived values and in the same call set the Decaps `PWR:` filter back to the PWR selected in the PWR Nets table. After editing # Decaps or Distance under "All PWRs" or under another PWR, the filter jumped to that PWR and the edited row could disappear. The filter now follows the PWR Nets table only when the user selects another row there. A model reset or a removed row that keeps the same PWR selected no longer counts as a new selection.
- Editing the PWR Name of a decap row under a `PWR:` filter no longer makes the row vanish from under the cursor, with the current row jumping to another one. The filter is applied when it is set, not on every edit. Setting it again (even to the same PWR) re-applies it.
- **Mouse wheel**: combo boxes and spin boxes change their value on the wheel only after you click into them and while they keep the focus. Otherwise the wheel scrolls the table or panel. Qt's default changes the widget under the pointer, focused or not (Windows), so scrolling past the `PWR:` filter, the distance mode, σ, seed, the via settings, sweep points or an open cell editor could silently change them.
- Table cell editors: **Up / Down / Page Up / Page Down** move to another row and commit the typed value, like a spreadsheet. They used to step the number (e.g. # Decaps 10 → 9) or pick the next PWR in the PWR Name combo. The editable PWR Name combo no longer auto-completes typed text into another PWR name.
- While a table cell editor has the focus, keys without Ctrl/Alt (Esc, letters, digits, Space) always go to the editor and never trigger a window shortcut. Add / Duplicate / Remove Row commit the open editor first. View ▸ Reset View (Ctrl+D / Ctrl+0) is a window shortcut instead of an application shortcut, so it no longer fires from the Help window. It still works while a spin box or cell editor has the focus.
- **Column widths can be changed again.** 0.2.0 had set the numeric and check-box columns to `ResizeToContents`, which cannot be dragged and was recomputed on every data change. Every column of the Stack-up, PWR Nets and Decaps tables, the marker readout table and the Messages dock is now interactive. Automatic widths (fit to contents, the Name / Decap File column filling the free width, readout columns sharing the width) are computed at load time and apply only to columns you have not resized. A width you drag is never overwritten by an edit, a refresh or a project reload. User widths are stored in the auto-save (`session.window.column_widths`) and restored on startup. They are ignored if a table's column count changes.

## 0.4.0 — 2026-09-22

### Results
- **Curve list in the All PWRs tab** replaces the row of check boxes above the plot: a panel to the right of the plot (splitter, ~220 px, collapsible, width remembered) with a filter box ("Filter PWRs…", case-insensitive substring; filtering hides rows without changing what is shown), one row per PWR in table order with check box, curve-colour swatch and elided name (tool tip = full name), multi-selection with **Space** to toggle all selected rows, and **All / None / Invert / Only selected** as buttons and as a right-click menu. Hiding a curve also removes that net from the marker readout table. Which curves are hidden and how wide the panel is are stored in the auto-save (`session.hidden_curves`, `session.window.curve_panel_width`) and survive a recompute and a restart; per-PWR tabs are unchanged.
- **Reset view fits the visible curves only.** The default view is now computed explicitly — frequency over the sweep, |Z| over the minimum and maximum of the visible curves (and their plane-only curves) with 2–5 % padding — instead of relying on pyqtgraph's auto-range, which also saw hidden items. With nothing visible it falls back to all curves. Triggers are unchanged (⟲ Reset view, View ▸ Reset View, Ctrl+D / Ctrl+0, context menu, "View All") and per-PWR tabs use the same routine.
- Hiding or showing a curve, switching the |Z| unit or resizing the plot re-fits the view only while it is still the fitted one; the first pan or zoom freezes it until the next Reset view.
- Exported plot images (All Plots…) keep following the curve visibility, now with the same visible-curve fit.
- The plot legend lists the visible curves only (a hidden curve used to leave an empty legend row) and is hidden on screen by default: the new **View ▸ Show Plot Legend** turns it back on (remembered between sessions). Exported plot images always include the legend, since they have no curve list beside them.

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
