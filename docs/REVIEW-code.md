# Code Review before the first public release (v0.1.0)

Reviewer role: independent code reviewer and fixer. Baseline: `docs/DESIGN.md` v1.2 (normative)
and `docs/REVIEW-physics.md`. Scope: `src/simple_pi_calculator/**`, `tests/**`, `packaging/**`,
`.github/workflows/**`, `README.md`.

Severity scale: **MAJOR** (wrong result, crash, or a build/release step that cannot work),
**MINOR** (robustness or UX problem with a workaround, small inconsistency), **NOTE** (verified, or
documentation only).

Test suite: 508 tests before the review, **530** after (22 regression tests added:
`tests/test_review_fixes.py` and the last section of `tests/test_gui_smoke.py`), all passing with
`QT_QPA_PLATFORM=offscreen python -m pytest tests -q -p no:cacheprovider`. Each new test was
checked to fail on the pre-review code (`git stash`).

Line numbers refer to the files after the fixes.

---

## Summary

| # | Severity | Area | Finding | Fixed |
|---|---|---|---|---|
| C1 | NOTE | compute | End-to-end compute path verified against an independent hand implementation of §2 | — (test added) |
| C2 | MINOR | compute | Relative model paths resolved next to the decap Excel file in the GUI but not in the engine | yes |
| C3 | MINOR | compute | An unreadable model file (`OSError`) or any unexpected exception in one PWR aborted the whole computation | yes |
| X1 | MINOR | Excel | Merged cells (e.g. one PWR name over several rows) gave `E_XL_MISSING_VALUE` | yes |
| X2 | MINOR | Excel | Table on a sheet other than the keyword/active sheet was not found | yes |
| X3 | MINOR | Excel | `Dk@1GHz` / `Df@1GHz` headers not recognised; `mils`/`micron` units rejected | yes |
| P1 | MINOR | project | Project/auto-save JSON with a UTF-8 BOM (Notepad) rejected as corrupt | yes |
| G1 | MINOR | GUI | Uncaught exceptions (incl. in Qt slots) opened a modal box, not the Messages dock | yes |
| G2 | MINOR | GUI | Inputs edited during a computation: returned results were not marked stale | yes |
| G3 | MINOR | GUI | Remove with nothing selected marked the project modified and scheduled an auto-save | yes |
| G4 | MINOR | GUI | Typing an odd *vias per decap* stored N+1 but the spin box kept showing N | yes |
| G5 | MINOR | GUI/help | Readout showed "—" for out-of-range markers (spec and Help say "n/a"); Help gave the wrong log path | yes |
| K1 | MAJOR | packaging | `collect_submodules("pyqtgraph")` imports `pyqtgraph.examples`, which starts a QApplication: PyInstaller aborts on a headless box and can hang/open a window on Windows | yes |
| K2 | MAJOR | CI | Smoke-test step used `Start-Process … -Timeout 120` (no such parameter): the build job always failed there | yes |
| K3 | MINOR | CI/packaging | `tools/write_version_info.py` missing (exe without version resource); `--self-test` did not check bundled resources or run the engine and produced no output in the windowed exe | yes |
| K4 | MINOR | packaging | `build_windows.ps1` wrote the bundle to `packaging/dist`, but `installer.iss` reads `dist/`; native command failures were not checked | yes |
| K5 | MINOR | CI | `pip install .[dev] \|\| fallback` hid a broken extra; no release notes when `CHANGELOG.md` is absent (RELEASING.md promised them); non-tag artifact name lacked the dev suffix of §7.3 | yes |
| D1 | MINOR | docs | README: missing modelling assumptions, inaccurate/insufficient input tables, wrong statements (PySide6/Python support, "de-embedding", Wheeler42 as Z_s source) | yes |

Verified without change: units (mm→m, µm/mm plating, nH→H), dummy-cap grouping, vias-per-decap
parallelisation and cluster widths, PAD via count, plane-only curve, marker readouts, cancel
handling, `.mod` parsing variants, fixed (non-draggable) marker lines, unit switching of curves and
readout table, frozen resource paths.

---

## 1. Compute path

### C1 — NOTE: end-to-end numbers reproduced independently

`io/project_io.py::to_inputs` → `core/engine.py::compute_project` → `core/pdn.py::compute_pwr` →
`core/cavity.py`, `core/placement.py`, `core/via.py`, `core/decap_model.py` were read against
§2–§3 and Appendix B. Unit handling is consistent: `to_inputs` converts all mm fields with `MM`
(`project_io.py:712-740`), plating thickness is stored in mm (0.025 mm = 25 µm) and converted once,
mounting inductance nH→H; `core` is SI throughout.

Cross-check (`tests/test_review_fixes.py::test_end_to_end_matches_independent_hand_calculation`):
an independent brute-force implementation (no static split, closed-form decap impedances, own
via and surface-impedance code) of the VDD_IO net from the doc equations gives

| f | hand | engine |
|---|---|---|
| 1 MHz | 12.366732 mΩ | 12.366732 mΩ |
| 10 MHz | 62.716741 mΩ | 62.716741 mΩ |
| 100 MHz | 881.676378 mΩ | 881.676375 mΩ (static-split error 3e-9) |
| 1 GHz | 4494.454 mΩ | 4494.060 mΩ (split error 9e-5, within §3.3's bound) |

L_loop = 0.866012 nH, h_near = 1.105 mm, M × N = 403 × 188, plane-only @ 1 MHz 995.13 Ω — all
equal to §8.11. VDD_CORE markers 3.294 / 35.42 / 139.5 mΩ also match §8.11.

Specific checks: dummy grouping `placement.py:76-92` (Σc = N, [2,2,1]); load
`Z_cap/c + Z_via,dec` (`pdn.py:80-92`); `Z_via,dec = Z_pair/(vias_per_decap/2)`, cluster width with
n = vias_per_decap/2 and PAD n = pad_via_count (`pdn.py:211-212, 262-263`); plane-only
`z_cav[0,0] + Z_via,pad` (`pdn.py:281`); markers exact on the grid ∪ marker set
(`pdn.py:160-178`); cancellation polled per PWR, per decap group, per m-row, per frequency chunk
(`cavity.py:251`, `cavity.py:294`, `pdn.py:110`).

### C2 — MINOR: model path resolution differed between GUI and engine

`core/engine.py:115`. The decap table (`gui/models.py::DecapTableModel.resolved_path`) resolves a
relative model file next to the decap Excel file, then the project folder, then the search folder
(§4.4). The engine only tried the project folder and search folder, so a row shown as resolved
(no red cell) failed at compute with `E_DECAP_FILE_NOT_FOUND` (e.g. file name typed into the
table of an untitled project that was imported from Excel).
**Fix:** `ProjectInputs.decap_source_dir` (`engine.py:53`), filled by `to_inputs`
(`project_io.py:737`) and used as the first candidate. Test:
`test_relative_model_resolved_via_excel_folder_at_compute_time` (also uses a Korean folder name).

### C3 — MINOR: failure isolation per PWR was incomplete

`core/decap_model.py:125`, `core/engine.py:202-206`. Only `InputError` was caught per PWR. A model
file that exists but cannot be read (permission, locked, vanished) raised `OSError`, and any
unexpected exception aborted the whole project computation (all results lost, only a traceback
in the dock), contradicting §5.2 "per-PWR errors do not abort other PWRs".
**Fix:** `load_decap_model` maps `OSError` to `E_DECAP_FILE_READ`; `compute_project` catches other
exceptions per PWR, logs the traceback and reports `E_PWR_INTERNAL` for that PWR only. File
handles in `spice_parser.read_spice_source` / `touchstone.read_s2p` are now closed.
Tests: `test_unreadable_model_file_is_an_input_error_not_a_crash`,
`test_unexpected_error_in_one_pwr_does_not_abort_the_others`.

---

## 2. Input robustness

A scratch matrix of header and file variants was run. Already handled correctly before the
review: `Thickness (um)`, `Conductivity [S/m]`, `Layer No.`, `Decap Count`, `Distance(mm)`, Korean
text before/after headers or in a second parenthesis (`Thickness(mm)(두께)`), empty rows, numbers
stored as text (`"5.8E7"`, `" 100 "`, `"4,2"`); `.mod` files with CRLF, CR-only, BOM, tabs,
upper-case suffixes and extensions (`.MOD`), `.subckt … params:` with continuation lines, Latin-1
bytes, `.MODEL`/`.END` lines, missing file (`E_DECAP_FILE_NOT_FOUND`), directory named `.mod`,
non-ASCII (Korean) paths; project-relative model paths (`project_io.py::_model_path_in`).

### X1 — MINOR: merged cells

`io/excel_import.py:86-103, 115`. openpyxl read-only worksheets do not expose merged ranges, so a
PWR name merged over several decap rows produced `E_XL_MISSING_VALUE` for the rows below.
**Fix:** open with `read_only=False` and fill each merged range from its top-left cell
(`_sheet_rows`). Test: `test_merged_cells_are_filled`.

### X2 — MINOR: extra sheets

`io/excel_import.py:120-141`. With a cover/notes sheet active (or a non-ASCII sheet name that cannot
match the keyword) the import failed with `E_XL_HEADER_NOT_FOUND`.
**Fix:** after the §4.1 choice (keyword sheet, active sheet) the other sheets are tried; the
original error is still reported when no sheet has a header. Tests:
`test_table_found_on_a_later_sheet`, `test_header_not_found_still_reported_when_no_sheet_matches`.

### X3 — MINOR: common header spellings

`io/excel_headers.py:105-116`, `core/units.py:48-52`. `Dk@1GHz` normalises to `dk1ghz` and matched
nothing; `Thk (mils)` / `Thickness [microns]` failed with `E_XL_UNIT`.
**Fix:** Dk/Df names followed by a digit (frequency qualifier) match; unit aliases `mils`, `thou`,
`micron(s)`, `inches`. Tests: `test_stackup_header_variants_units_and_korean_suffixes`,
`test_length_unit_aliases`, `test_dk_df_qualified_headers_do_not_steal_other_columns`.

### P1 — MINOR: BOM in JSON

`io/project_io.py:608-609`. A project or auto-save file saved by Notepad with a BOM was reported as
`E_PROJECT_FORMAT` (auto-save: quarantined as corrupt). **Fix:** decode with `utf-8-sig`. Test:
`test_project_file_with_utf8_bom_loads`.

---

## 3. GUI

### G1 — MINOR: uncaught exceptions

`app.py:68-129`, `gui/main_window.py:1187-1196`. The hook showed a modal `QMessageBox` for every
uncaught exception. PySide6 routes exceptions raised in slots/virtuals through `sys.excepthook`;
a modal box from inside a failing paint or timer slot can re-enter the failure and stack dialogs.
Nothing reached the Messages dock.
**Fix:** `make_excepthook` logs the traceback and calls `MainWindow.report_internal_error`, which
adds `E_INTERNAL` (with the log path) to the Messages dock and status bar; re-entrancy guarded;
`threading.excepthook` logs exceptions of Python threads. Test:
`test_uncaught_exception_goes_to_messages_dock_not_modal`.

### G2 — MINOR: editing while compute runs

`gui/main_window.py:491-493, 1168, 1238-1241`. Editing is allowed during a computation (the worker
holds a snapshot, §5.4), but `show_results` cleared the stale flag, so results of the old inputs
looked current. **Fix:** remember edits during the run and mark the new results "(inputs changed)".
Test: `test_edit_during_compute_marks_results_stale`.

### G3 — MINOR: Remove without selection

`gui/models.py:242-251, 451-460, 679-687`. All three `remove_rows` emitted `edited` even when
nothing was removed. **Fix:** return early. Test: `test_remove_without_selection_is_not_an_edit`.

### G4 — MINOR: odd vias per decap

`gui/panels.py:364-369`. The spin box has step 2 but accepts a typed odd value; the project stored
N+1 while the widget showed N. **Fix:** write the rounded value back (signals blocked). Test:
`test_odd_vias_per_decap_is_rounded_and_shown`.

### G5 — MINOR: "n/a" readouts, log path in Help

`gui/main_window.py:1420`: out-of-range markers now read `n/a` (§3.1, Help ▸ Results). Test:
`test_markers_outside_sweep_read_na`. `help/troubleshooting.html` gave `%LOCALAPPDATA%` for the
log; the app writes `<auto-save folder>/logs/app.log` (`%APPDATA%\SimplePICalculator\logs`) — Help
corrected and the new codes `E_DECAP_FILE_READ`, `E_INTERNAL`, `E_PWR_INTERNAL` documented
(`help/input_decaps.html`, `help/troubleshooting.html`, `help/input_stackup.html` for merged
cells, sheet fallback and unit aliases).

### Verified (no change)

Table edits emit `edited` → modified marker + debounced auto-save; Decap filter follows the
selected PWR and *Add* pre-fills the filter PWR; decap import replaces the rows and resolves model
files next to the workbook; unit switch rescales curves, plane-only curves, marker texts and the
readout table and shifts a manual Y range; marker `InfiniteLine`s are `movable=False`; legend and
plane-only visibility; Reset zoom; Help navigation (Back/Forward/Home, contents sync); About box;
first run without auto-save and corrupt auto-save (non-modal `W_AUTOSAVE_CORRUPT`); `--self-test`
never touches the auto-save folder.

---

## 4. Packaging and CI

### K1 — MAJOR: PyInstaller build aborts in `collect_submodules("pyqtgraph")`

`packaging/simple_pi_calculator.spec:45-71, 110-116`. Collecting all pyqtgraph submodules imports
`pyqtgraph.examples`, which creates a QApplication; on this Linux box the isolated collector
process died with SIGABRT ("could not load the Qt platform plugin") and the build failed; on a
Windows runner it may open a window or hang.
**Fix:** `collect_submodules(..., filter=...)` skipping `examples`, `opengl`, `jupyter` and the
templates of other Qt bindings; the same packages are excluded; `collect_submodules
("simple_pi_calculator")` added because the engine is imported lazily via `importlib`.
**Verified:** `pyinstaller --noconfirm --distpath <scratch>/dist --workpath <scratch>/build
packaging/simple_pi_calculator.spec` builds on Linux (datas paths are derived from `SPEC`, so the
build works from any directory); the frozen binary with `QT_QPA_PLATFORM=offscreen --self-test`
exits 0, finds `_internal/simple_pi_calculator/help` (15 pages, no missing image), the icon and
`_internal/examples`, and computes the example (3.294 / 35.42 / 139.5 mΩ) — also with stdout and
stderr closed (as in a `console=False` exe). Build output was deleted afterwards. Test:
`test_spec_does_not_import_pyqtgraph_examples`.

### K2 — MAJOR: smoke-test step could never pass

`.github/workflows/build-windows.yml:102-126`. `Start-Process -FilePath … -Wait -Timeout 120`:
`Start-Process` has no `-Timeout` parameter, so PowerShell fails with a parameter-binding error
before running the exe, and every build (and therefore every release) failed.
**Fix:** `Start-Process … -PassThru`, `WaitForExit(120000)` with kill on timeout, exit code check,
and the report file written by `--self-test-report` is printed (a `console=False` exe prints
nothing, so the step relies only on the exit code and the file). Same logic added to
`packaging/build_windows.ps1`. Test: `test_windows_workflow_smoke_step_is_valid_powershell`.

### K3 — MINOR: version resource and self-test content

`tools/write_version_info.py` (new) generates the PyInstaller `VSVersionInfo` from `__version__`
(FileVersion X.Y.Z.0, §7.3); the workflow no longer silently skips it. `app.py:148-240`:
`--self-test` now checks all modules, help pages and images, icon, examples folder, runs the
example project through the engine, creates the main window and writes `--self-test-report`.
Tests: `test_write_version_info_renders_package_version`, `test_self_test_report_file`.

### K4 — MINOR: local build script

`packaging/build_windows.ps1:83-100`. PyInstaller ran in `packaging/` without `--distpath`, so the
bundle went to `packaging/dist` while `installer.iss` packs `..\dist\SimplePICalculator\*`: the
local installer build failed. Native command exit codes (`pip`, `pytest`, `pyinstaller`) were not
checked. **Fix:** `--distpath ..\dist --workpath ..\build`, explicit `$LASTEXITCODE` checks, smoke
test added.

### K5 — MINOR: workflow details

`build-windows.yml`, `ci.yml`: `pip install -e ".[dev]"` without the silent fallback (verified
here: the wheel builds with setuptools ≥ 68, contains `help/**` incl. 18 images and `resources/**`,
and `pip install --dry-run ".[dev]"` resolves, e.g. PySide6 6.8.3); `cache-dependency-path:
pyproject.toml`; `actions/checkout@v5`, `actions/setup-python@v6`; `pull_request` trigger per §7.4;
artifact version `X.Y.Z-dev+<sha>` on non-tag builds (§7.3); `if-no-files-found: error`;
`generate_release_notes` when there is no `CHANGELOG.md`. The tag check (`refs/tags/v*`,
`v<__version__>` vs `github.ref_name`) was correct. `installer.iss` reviewed: paths relative to
`packaging/`, fixed AppId, icon, examples shortcut to `{app}\_internal\examples` (matches the spec's
`examples` data destination); not compiled here (no Inno Setup on Linux).

---

## 5. Documentation

### D1 — MINOR: README

`README.md` now has detailed input tables (common Excel rules, stack-up/PWR/decap columns with
header examples that were checked against the matcher, model-file rules, GUI inputs), a
**Modelling assumptions** section (synthetic plane and placement, Top side, dielectric
combination, via-pair and cluster model, port width, dummy caps, mounting inductance, result
definition, exact markers), command-line options and the auto-save/log locations. Wrong statements
fixed: PySide6 and Python 3.12 support, "`.s2p` de-embedding" (it is an S→Z conversion), Wheeler42
as the source of the surface impedance, "position" of decaps (it is a distance), `Name` as a layer
name header (only `Layer Name` matches).

`docs/DESIGN.md`: only Appendix D "Implementation notes" appended (markers, §8.4 #5 typo, extra
issue codes, Excel tolerance, path resolution, BOM, logging, stale results, packaging deviations).

---

## 6. Remaining risks (not fixed)

1. **Not run on Windows or on the pinned PySide6 ≤ 6.8**: this environment has PySide6 6.11; the
   Windows workflow, the Inno Setup compile and the windowed exe smoke test are unverified until
   the first CI run. The pinned action versions (`upload-artifact@v4`,
   `softprops/action-gh-release@v2`) still run on Node 20 and may need a bump.
2. **`W_DECAP_PWR_UNKNOWN`** is also emitted for decap rows of a *disabled* PWR (message says "not
   an enabled PWR"); noisy but harmless.
3. **CSV export** is UTF-8 without BOM: Excel on a Korean Windows locale shows non-ASCII PWR names
   garbled (numbers are unaffected). A BOM would break readers of the `#` header lines.
4. **`Open Example Project`** works from source and the frozen build, not for a plain
   `pip install` (examples are not package data).
5. **Excel ambiguities kept per §4.1:** `"1,000"` is read as 1.0 (comma = decimal point); a
   `Part #` column left of the count column would be taken as the count.
6. **Closing during a very long computation** waits 5 s for the worker; if the current
   uncancellable step is longer, Qt may warn about destroying a running thread.
7. Reading workbooks with `read_only=False` (for merged cells) uses more memory on very large,
   heavily formatted sheets.
