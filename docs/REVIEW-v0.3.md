# Independent review — decap distance distribution (v0.3 candidate)

| Item | Value |
|---|---|
| Scope | commit 1887e73 "distance distribution option (truncated normal ±1σ, seeded), schema v4": `core/distribution.py`, `core/placement.py`, `core/engine.py`, `core/pdn.py`, `core/cavity.py`, `gui/panels.py`, `gui/main_window.py`, `gui/engine_bridge.py`, `gui/placement_preview.py`, `io/migrations.py`, `io/project_io.py`, `io/export.py`, DESIGN.md §2.5.5, help `input_decaps.html` |
| Spec | docs/DESIGN.md v1.5, §2.5.5 |
| Date | 2026-09-15 |
| Test suite | 696 passed, 24 skipped before → **705 passed, 24 skipped** after (new: `tests/test_review_v03.py`, `tests/test_gui_review_v03.py`) |
| Nothing committed | all changes are in the working tree |

## Summary of findings

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| F1 | Low | Statistics | `norm_ppf` was inaccurate in the upper tail: the Halley step used Φ(x) − p, and Φ(x) rounds to 1 for x ≳ 4, so Φ⁻¹ was off by up to **8.4·10⁻⁹** (x ≈ 7.5) compared with an erfc bisection; the lower half was accurate (1.8·10⁻¹⁵). The sampler only uses p ∈ [Φ(−1), Φ(1)], so **no sampled distance was affected**. | Fixed: for p > 1 − 0.02425 the step runs on the mirrored lower tail (−x, 1 − p exact). Max error over [−8, 8]σ is now 1.8·10⁻¹⁵. The 200 000 samples for seed 12345 are bit-identical before and after. |
| F2 | Low | GUI | `DecapPanel._write_distance` wrote all three fields (mode, σ, seed) from the widgets whenever any one of them changed. A σ that the spin box cannot show exactly (3 decimals, range 0.01–50 mm), e.g. `sigma_mm: 0.12345` from a project file, was **silently rewritten** (to 0.123) when the user only changed the seed or the mode. | Fixed: each control writes only its own field. |
| F3 | Info | GUI | The σ spin box still *shows* such a value rounded or clamped (0.123 for 0.12345; 50 for 100 mm), while the computation, the preview header and the exports use the stored value. | Not changed (display only; values outside the GUI range only come from hand-edited files). |
| F4 | Info / design | Physics | D_ref = max sampled distance, so H = 1.4·D_ref **changes with the seed**. When one row sets D_ref, the plane size moves by up to ±σ·1.4 and so do the plane resonances (see §4). This follows §2.5.5 as written, but it is the main effect of the option above ~300 MHz, not the port shifts. | **Adopted** (coordinator): normal mode uses D_ref = max_k D_k + σ, which is seed-independent and bounds every sample; fixed mode is unchanged. Seed-to-seed |Z| spread fell from about 2× to ≤ 1.5 % (§4.2). |
| F5 | Info | Messages | `W_DIST_SIGMA_LARGE` numbers the row among the net's enabled rows ("Decap row 1" = first row of that PWR), not the table row. The other placement warnings do the same. | Not changed. |

Everything else checked out. That covers stream order and determinism, Dummy Cap sampling, the
geometry, the cache key, preview = engine, migration, autosave and the export headers.

## 1. Statistics

* **Φ⁻¹ accuracy.** I compared it with a bisection on `math.erfc` over x ∈ [−8, 8] (4001 and 32001
  points). In the upper half, the reference matches the exact complement 1 − p against Q(x) = ½·erfc(x/√2), so the
  reference itself does not round. Before the fix: max |Δ| = 8.4·10⁻⁹ (upper tail), 5.6·10⁻¹⁶ for |x| ≤ 1.
  After the fix: max |Δ| = 1.8·10⁻¹⁵ everywhere (F1). Regression test: `test_norm_ppf_matches_erf_bisection_over_8_sigma`
  (fails on the old code).
* **Truncated-normal sampler.** Seed 12345, N = 200 000, D = 12 mm (checked with 15 mm too), σ = 0.5 mm:
  * The largest KS-style gap between the empirical CDF and the ±1σ truncated normal is **2.95·10⁻³** (< 3·10⁻³).
  * The mean is 0.23 µm from D. The expected standard error is 0.27 mm / √N = 0.6 µm.
  * The standard deviation is 0.2702 mm. Theory gives 0.5396·σ = 0.2698 mm.
  * Every sample lies inside [D − σ, D + σ], and this still holds after the D + σ·z rounding.

  Regression test: `test_truncated_normal_empirical_cdf_bounds_and_mean`.
* **Stream order and determinism.** `compute_project` draws all samples before any net starts. It uses
  one `default_rng(seed)` over the enabled rows of the whole decap table, in table order. Results in normal mode are
  bit-identical for 1, 2 and 4 workers (`z_pad` and `xy_m`). Disabling the VDD_CORE net leaves VDD_IO's
  samples unchanged, because the rows of a disabled net still draw. The preview (`EngineBridge.placement`) calls
  the same `engine.sample_project_distances` on the whole table, so the samples do not depend on the PWR
  selected in the GUI. Regression tests: `test_normal_mode_bit_identical_across_worker_counts` and
  `test_samples_independent_of_enabled_pwr_nets_and_equal_to_preview`.
* **Dummy Cap rows.** `row_port_counts` gives ⌈N/2⌉. In the example, the VDD_IO Dummy row with 4 capacitors has 2 via sets
  and gets 2 samples; with the single 10 µF row that makes 3 via sets. `caps_per_port` = [2, 2, 1].

## 2. Geometry

For both nets of the example in normal mode:

* D_ref = max D + σ (after F4; it was max(`port_distances_m`) as committed) holds exactly and is ≥ every sample.
* H = 1.4·D_ref.
* The PAD sits at (W/2, 0.2·D_ref) exactly.
* Every port footprint lies inside the plane.

Compared with fixed mode, x is identical and y − y_fixed = (d_kj − d_k) + 0.2·ΔD_ref to within 9·10⁻¹⁹ m. The crowded
sub-row pattern with Dummy sets is already covered by `test_placement_sub_rows_and_dummy_sets_keep_pattern`
(400 capacitors on a 10 mm plane).

`cavity_cache_key` hashes (mode, σ, seed, per-port distances) in normal mode and keeps the 0.2.0
key in fixed mode. Warm and cold runs are equal (existing `test_cache_key_contains_distribution`).

The preview's `xy_m`, `port_distances_m`, `d_ref_m` and `height_m` are identical to `PwrResult.placement`,
because both call `place_ports` with the same sampled tuples.

## 3. GUI, persistence, messages and exports

* **Controls.** In Fixed mode, σ, Seed and New seed are disabled; they are enabled in Normal mode. Mode, σ and seed edits
  set `modified`, mark the results stale, refresh the preview and schedule the autosave (`decap_panel.edited` →
  `on_inputs_changed`, which calls `_schedule_autosave`). F2 fixed.
* **Migration 3 → 4 of an autosave.** `tests/data/project_v3.spical.json` placed as `autosave.spical.json`
  restores as fixed / 0.5 mm / 12345 with the controls disabled. After switching to Normal, the flushed autosave is
  schema 4 with `distance_mode: "normal"`. Regression test: `test_v3_autosave_restores_fixed_and_rewrites_schema_4`.
* **`I_DIST_SAMPLED`**, one per net, for example: `PWR VDD_IO: decap distances sampled from a normal
  distribution truncated to ±1σ (σ = 0.5 mm, seed 12345): min/mean/max = 4.9502/6.8273/10.1723 mm over
  3 via set(s).` The statistics match `sampled_distances`.
* **Headers.**
  * Per-PWR CSV: `# Distance distribution: normal truncated to +/-1 sigma, sigma = 0.5 mm, seed = 12345; sampled min/mean/max = … mm over N via set(s)`.
  * Combined CSV: the same line with the prefix `# <PWR>: `.
  * `.s1p` and combined `.s2p`: `!   Distance distribution: …`, one line per port.
  * Fixed mode: `Distance distribution: fixed (every capacitor at its row distance)`.

  Regression test: `test_combined_csv_and_touchstone_headers`.

## 4. Physics sanity: the example project, σ = 0.5 mm

|Z| is in mΩ, with 400 points from 100 kHz to 1 GHz. "rel" is the change against fixed mode.

### 4.1 As committed (D_ref = largest sample)

| PWR | f | fixed | seed 12345 | seed 1 | seed 2024 | spread (max−min)/fixed |
|---|---|---|---|---|---|---|
| VDD_CORE | 1 MHz | 3.2939 | 3.2924 (−0.045 %) | 3.2934 (−0.017 %) | 3.2930 (−0.027 %) | 0.028 % |
| VDD_CORE | 10 MHz | 35.416 | 35.419 (+0.007 %) | 35.405 (−0.032 %) | 35.385 (−0.090 %) | 0.097 % |
| VDD_CORE | 100 MHz | 139.51 | 139.23 (−0.202 %) | 139.39 (−0.089 %) | 139.37 (−0.103 %) | 0.114 % |
| VDD_IO | 1 MHz | 12.367 | 12.372 (+0.045 %) | 12.356 (−0.089 %) | 12.382 (+0.122 %) | 0.212 % |
| VDD_IO | 10 MHz | 62.717 | 62.751 (+0.055 %) | 62.716 (−0.002 %) | 62.589 (−0.204 %) | 0.259 % |
| VDD_IO | 100 MHz | 881.68 | 882.19 (+0.058 %) | 881.22 (−0.052 %) | 881.20 (−0.054 %) | 0.112 % |

Above about 500 MHz, the plane height followed the largest sample, and so did the plane capacitance (F4):

* VDD_IO: D_ref became 10.17, 9.66 and 10.47 mm for the three seeds, so H was 14.24, 13.53 and 14.65 mm.
  The anti-resonance moved from 615.8 MHz to anywhere in 601.8–630.2 MHz.
* At a fixed frequency on the flank of that peak, |Z| changed by up to about 2× between seeds (99 % at 741 MHz).

This peak lies far below the first cavity mode, which is several GHz at H ≈ 14 mm. It is the anti-resonance of the plane
capacitance C ∝ W·H with the decap/via loop inductance, so f ∝ 1/√(LC).

### 4.2 After adopting F4 (D_ref = max D + σ)

| PWR | f | fixed | seed 12345 | seed 1 | seed 2024 | spread (max−min)/fixed |
|---|---|---|---|---|---|---|
| VDD_CORE | 1 MHz | 3.2939 | 3.2922 (−0.053 %) | 3.2923 (−0.048 %) | 3.2913 (−0.078 %) | 0.030 % |
| VDD_CORE | 10 MHz | 35.416 | 35.414 (−0.008 %) | 35.383 (−0.094 %) | 35.349 (−0.190 %) | 0.182 % |
| VDD_CORE | 100 MHz | 139.51 | 139.19 (−0.231 %) | 139.25 (−0.192 %) | 139.13 (−0.274 %) | 0.083 % |
| VDD_IO | 1 MHz | 12.367 | 12.356 (−0.084 %) | 12.320 (−0.381 %) | 12.380 (+0.108 %) | 0.489 % |
| VDD_IO | 10 MHz | 62.717 | 62.724 (+0.012 %) | 62.643 (−0.118 %) | 62.586 (−0.208 %) | 0.220 % |
| VDD_IO | 100 MHz | 881.68 | 882.11 (+0.049 %) | 881.03 (−0.073 %) | 881.19 (−0.055 %) | 0.122 % |

* H is now the same for every seed: VDD_CORE 21.70 mm and VDD_IO 14.70 mm, against 21.00 and 14.00 mm in fixed mode.
* The VDD_IO anti-resonance sits at 601.8 MHz for all seeds. That is a fixed, predictable −2.3 % shift from fixed mode, caused by the +σ margin.
* Seed-to-seed |Z| spread, the maximum over the whole sweep:
  * VDD_CORE: 0.86 % (near 1 GHz); 0.32 % up to 300 MHz.
  * VDD_IO: 1.54 % (at 588 MHz); 0.90 % up to 300 MHz.

These numbers are plausible:

* The ports move by at most 0.5 mm on distances of 5–15 mm.
* The via-loop inductance, which dominates, does not depend on the distance.
* Only the spreading term changes, roughly like ln(d).

So the scatter changes |Z| at the sub-percent to percent level: largest near the inter-bank anti-resonance and
negligible elsewhere.

## 5. Screenshots

These were produced by the extended `make_shots.py` in the session scratchpad `shots/` folder:

* `20_decaps_panel_normal.png`: Decaps tab (all PWRs) in Normal (±1σ) mode, with σ 0.500 mm, seed 12345 and New seed enabled.
* `21_placement_preview_normal.png`: VDD_CORE preview with the 10 + 4 ports scattered in y. The header reads
  "D_ref = 15.50 mm, …, normal ±1σ: σ 0.5 mm, seed 12345".

## 6. Changed files

* `src/simple_pi_calculator/core/distribution.py`: upper-tail Halley step on the mirrored tail (F1).
* `src/simple_pi_calculator/gui/panels.py`: each distance control writes only its own field (F2).
* `src/simple_pi_calculator/core/placement.py`: `place_ports(..., sigma_m=)` gives D_ref = max d_k + σ (F4).
* `src/simple_pi_calculator/core/pdn.py`: passes σ in normal mode; `I_DIST_SAMPLED` now ends with `D_ref = max D + σ = … mm`.
* `src/simple_pi_calculator/gui/engine_bridge.py`: the preview passes σ as well; `gui/models.py` updates the D_ref column tooltip.
* `docs/DESIGN.md` §2.5.5, §2.5.1 note, test plan and pipeline step: mirrored Halley step and the D_ref = max D + σ rule.
* `help/input_decaps.html`: geometry bullet and the `I_DIST_SAMPLED` description.
* `tests/test_distance_distribution.py`: expectations updated to max D + σ, plus H equality across seeds.
* `tests/test_review_v03.py` (new): Φ⁻¹ vs bisection, KS/mean/bounds, worker determinism, preview = engine
  with a disabled net, geometry/Dummy/`I_DIST_SAMPLED`, combined CSV and Touchstone headers, H identical across seeds = 1.4·(max D + σ).
* `tests/test_gui_review_v03.py` (new): σ preserved on seed/mode edits, v3 autosave migration and rewrite.
