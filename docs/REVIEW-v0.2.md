# Independent review — changes since v0.1.0 (v0.2 candidate)

| Item | Value |
|---|---|
| Scope | `git diff v0.1.0..HEAD` (commits 8ed14fb … 72a2ba9): performance restructuring, Touchstone/CSV/image export, reset view, vias per decap pad (schema 2), Number of PADs (schema 3), GUI edit-commit fixes |
| Spec | docs/DESIGN.md v1.4 (now 1.4.1 with the fixes below); earlier review docs/REVIEW-physics.md |
| Date | 2026-09-15 |
| Test suite | 610 passed before → **671 passed, 24 skipped** after (the 24 skips are the scikit-rf validator tests, which run when `scikit-rf` is installed; they passed during the review) |
| Nothing committed | all changes are in the working tree |

## Summary of findings

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| F1 | **Medium** | Cache invalidation | `DecapModelCache` keyed models by (path, mtime, size): a same-size edit of a model file that keeps the modification time (coarse FAT/SMB clocks, fast successive saves, tools that restore mtimes, `git checkout` of files with equal size in the same second) returned the **stale model** — a silently wrong result in the GUI session. | Fixed: key uses a SHA-256 of the file content; superseded versions of the same file are evicted. |
| F2 | **Medium** | GUI / engine | A PWR that failed because of an error whose source is a *file* (e.g. `E_SPICE_EXPR` in a decap model, detected only at compute time) had no issue with `source = "PWR:<name>"`. The GUI therefore showed no "failed" tab for it and all exports left it out without any notice. (Pre-existing in v0.1.0, but it directly affects the new export-all flow.) | Fixed: `E_PWR_FAILED` (source `PWR:<name>`, location = file); exports add `W_EXPORT_SKIPPED` naming the skipped nets. |
| F3 | Low | Placement | Sub-rows are stacked at a pitch of exactly one port width, and rounding of `y_r` (−6.5·10⁻¹⁹ m) made touching footprints look overlapping: spurious `W_PORT_OVERLAP` for crowded PAD rows (e.g. 40 pads × 4 PAD vias on a 20 mm plane) and for multi-sub-row decap rows; the same exact float comparison could raise a spurious `W_PAD_CLIPPED`/`W_DECAP_CLIPPED`. Warnings only — numbers were unaffected. | Fixed: relative tolerance 1e-9 in the overlap and clipping tests. |
| F4 | Info | GUI | The decap table's derived columns (C @ 100 kHz, SRF) memo was keyed by mtime only. Display only. | Size added to the key. |
| F5 | Info | Packaging | `__version__` and the self-test banner still say 0.1.0 although schema 3 is written. | Not changed (release step). |

No defects were found in the N_pad circuit model, the vias-per-pad model, the cavity fast paths, the
rcond screening, the thread pools, cancellation, the Touchstone writer, schema migration or the
PyInstaller build.

## 1. N_pad model and vias per pad

**Independent derivation.** Let the cavity port matrix be partitioned into pads 𝒫 and decap ports
𝒦. Decap port k is terminated by V_k = −Z_L,k I_k, so eliminating 𝒦 gives
V_𝒫 = Z_pp,red I_𝒫 with Z_pp,red = Z_PP − Z_PK (Z_KK + diag Z_L)⁻¹ Z_KP; this holds for *any* pad
currents, so eliminating the decaps first is exact and independent of how the pads are driven.
Each pad p is fed from the common node (voltage V) through its own via set:
V = V_p + Z_via,pad I_p ⇒ (Z_pp,red + Z_via,pad·𝟙) I_𝒫 = V·1, I = 1ᵀI_𝒫 ⇒
**Z_PAD = V/I = 1/(1ᵀ(Z_pp,red + Z_via,pad·𝟙)⁻¹1)**, identical to DESIGN §2.8 and to
`pdn._pads_chunk`. The implementation order (Schur elimination of the decaps in `_reduce`, then
`_combine_pads`) matches; N_pad = 1 keeps the v0.1 path (bit-identical golden values still pass).

**Via impedances and port widths.** `compute_pwr` uses Z_via,pad = Z_pair / `pad_via_count` per pad
and Z_via,dec = Z_pair / `vias_per_pad`; w_pad = GMD cluster width of `pad_via_count` vias on the
√2·s_v checkerboard, w_dec that of `vias_per_pad` vias (§2.4.5). Checked in code and by test.

**Brute-force MNA** (`tests/test_pad_mna.py`, new): a nodal system with explicit unknowns for all
port currents and voltages, one node per decap via set, one current per physical capacitor (dummy
rows with 2 capacitors per via set), mounting inductance, per-pad via branches to a common node
driven by 1 A. N_pad ∈ {1, 2, 5} × (pad vias, vias per decap pad) ∈ {(1,1), (3,2)}, lossy
dielectric, 17 frequencies 100 kHz … 2 GHz: max relative deviation from `compute_pwr`
**≈ 8·10⁻¹¹** (limit 1e-9); plane-only curve ≤ 1e-12.

**Crowded pad rows.** 40 pads on a 20 mm plane, pad vias ∈ {1, 4, 9, 16}, decap distance
2 … 30 mm: all cases either raise `E_DREF_TOO_SMALL` (pad row does not fit, as specified) or compute
finite Z with every port footprint inside the plane; `W_PAD_CLIPPED` is issued exactly when sub-rows
were moved (regression test `test_forty_pads_on_20mm_plane_end_to_end`). Clipped sub-rows can
coincide (identical rows of Z_cav); A = Z_pp,red + Z_via,pad·𝟙 stays non-singular because
Re Z_via,pad > 0. See F3 for the spurious overlap warning.

## 2. Cache invalidation (highest risk)

Caches in the compute path: `CavityCache` (per `EngineBridge`, lives for the GUI session),
module-level `DecapModelCache`, the per-model impedance memo, the rcond probe vectors, the per-model
`CavityModel._T` table; plus the GUI's display-only `DecapModelInfo`.

`tests/test_cache_invalidation.py` (new) drives the **GUI path** (one persistent `EngineBridge`,
`make_inputs` → `compute`) and, for each edit, requires *warm result == cold result* (fresh caches,
rel 1e-12, both curves) and *changed nets differ / unchanged nets identical*, then undoes the edit
and requires the original result from the warm caches:

| Input | Expected effect | Result |
|---|---|---|
| dielectric thickness / Dk / Df inside the cavity, plane σ, plane thickness | changes that pair (thicknesses above a pair also change the other net's via length) | ✓ |
| dielectric above the planes (via length only) | changes affected nets | ✓ |
| layer name | no-op | ✓ (over-invalidates the cavity key, harmless) |
| plane pair, PWR/GND swapped | pair change / no-op | ✓ |
| width, Number of PADs | changes net | ✓ |
| distance, count, dummy, enabled, model file, subckt, row S2P mode, default S2P mode | changes net | ✓ |
| drill, anti-pad, pitch, vias per decap pad, PAD vias, via model, plating, via σ, mounting L | changes both nets | ✓ |
| f_start, f_stop, n_points, plane-only toggle | warm = cold | ✓ |
| worker count, display unit | no-op | ✓ |
| model file content edit (.mod, .s2p), file replaced at the same path | changes | ✓ |
| **same-size edit with unchanged mtime** | changes | ✗ before (F1) → ✓ |

The cavity key (SHA-256 of W, H, full `PlanePair` repr, port xy/widths bytes, evaluation
frequencies, `ModeSettings`, N_pad) covers every input of `CavityModel`; loads and via impedances
are recomputed on every run, so inputs that only enter them cannot be stale.

## 3. Thread safety

* Chunks never touch issue collectors; each net has its own collector; `DecapModelCache.get` and the
  impedance memo are locked; memo hits replay recorded warnings; failed loads/evaluations are not
  stored. `CavityModel._T` is built under a lock before the pool starts.
* `CavityCache.put` happens only after a complete Z-matrix; tests cancel at 6 different poll counts
  with 4 workers and then verify every remaining entry is finite/complete and a warm run equals a
  cold run.
* Worker counts 1, 2, 5, 1, 3 on warm caches and 1, 4 cold: **bit-identical** `z_pad` and marker
  values (`np.array_equal`).
* Six concurrent `compute_project` calls (two different projects) sharing one bounded cavity cache
  (3 entries, forcing evictions) and one model cache: every result equals its cold reference.
* rcond screening: the probe estimate 1/(‖A‖_F·max‖A⁻¹v_j‖) can only overestimate the true rcond by
  ‖A⁻¹‖₂/max‖A⁻¹v_j‖; with 8 complex Gaussian probes the probability of a ≥1e4 underestimate of
  ‖A⁻¹‖ is negligible, and the 8 worst frequencies always get an exact SVD. Sound.

## 4. Touchstone writer

Checked against the Touchstone 1.1 rules: `!` comments, one option line `# Hz <S|Z> <RI|MA> R <r>`,
strictly increasing frequencies (enforced), MA angles in degrees, 2-port order N11 N21 N12 N22,
N ≥ 3 row-major with each matrix row starting on a new line and **at most four pairs per line**
(rows wrap for N ≥ 5, a 3-port has 3 lines of 3 pairs — the implementation is correct; no change),
Z normalised to R in v1. External validation: `scikit-rf 2.1.0` read 1-, 2-, 3-, 4-, 5- and 9-port
files (random non-symmetric matrices) in S/Z × RI/MA × R = 1/50 Ω and recovered f, z0 and every
entry (rel 1e-9); added as `test_touchstone_files_read_by_scikit_rf` (skipped without scikit-rf).
scikit-rf was uninstalled afterwards (not a runtime dependency; it was not collected by PyInstaller).
Residual: PWR names and file names are written into comments as UTF-8; strict ASCII-only v1 readers
could reject non-ASCII names.

## 5. GUI

`tests/test_gui_review_v02.py` (new, real engine, offscreen):

* Reset view after each unit switch and after recompute: a manual zoom survives recompute (session
  behaviour), "⟲ Reset view" fits the new data; a default view stays default across recompute.
* Ctrl+D resets the view with the focus in a spin box line edit (value not modified), in an open
  PWR-table cell editor, on the table and on the plot.
* Exports with a failed PWR (model parse error at compute time): Touchstone per-PWR/combined, CSV,
  All Plots and XLSX export only the good net and each adds `W_EXPORT_SKIPPED` (F2).
* "# PADs" cell editor still open → Run commits it (inputs and result carry N_pad = 3).
* Auto-save files of schema 1 and 2 are migrated on restore (vias_per_decap → vias_per_pad,
  n_pads = 1), compute, and are re-written as schema 3.

## 6. Packaging

`pyinstaller packaging/simple_pi_calculator.spec` on Linux (PyInstaller 6.22.3, PySide6 6.11.2,
numpy 2.4.4) into the scratchpad, twice: (A) as is — `threadpoolctl` collected; (B) with
`threadpoolctl` excluded. Both `--self-test` runs: 37 modules, 15 help pages, **SELF-TEST OK**, same
marker values. Without `threadpoolctl`, `blas_limited` yields `False` and `app.configure_compute_threads`
has already set `OPENBLAS/OMP/MKL_NUM_THREADS=1` before numpy is imported (verified that numpy is not
imported by the entry module). Builds removed afterwards.

## Files changed by the review

* `src/simple_pi_calculator/core/decap_model.py` — content-digest key, eviction of superseded versions (F1)
* `src/simple_pi_calculator/core/engine.py` — `E_PWR_FAILED` (F2)
* `src/simple_pi_calculator/gui/main_window.py` — `W_EXPORT_SKIPPED` (F2)
* `src/simple_pi_calculator/core/placement.py` — rounding tolerance of overlap/clipping warnings (F3)
* `src/simple_pi_calculator/gui/models.py` — display memo key (F4)
* `docs/DESIGN.md` (1.4.1: §3.9 cache table, §4.8, Appendix D issue codes), `help/results.html` (codes)
* Tests: `tests/test_pad_mna.py`, `tests/test_cache_invalidation.py`, `tests/test_gui_review_v02.py`
  (new); additions to `tests/test_placement.py`, `tests/test_export.py`

## Residual risks

1. Model files are hashed on every compute (cheap for .mod; a multi-MB .s2p costs a few ms). A file
   rewritten *between* hashing and parsing is cached under the old digest until the next edit.
2. Clipped PAD sub-rows collapse onto each other (coincident ports); the result is finite and
   warned (`W_PAD_CLIPPED`, `W_PORT_OVERLAP`), but physically meaningless for such inputs — the spec
   clips rather than shifting the block.
3. The ideal common pad node, neglected mutual inductance between parallel via pairs and the
   synthetic spread of pads across the width (§2.5.4, §9) remain modelling approximations.
4. The scikit-rf validation only runs where scikit-rf is installed (CI does not install it).
5. Ctrl+D was verified offscreen on Linux; platform-specific key bindings (e.g. macOS Emacs-style
   Ctrl+D in line edits) were not tested on the target Windows build.
