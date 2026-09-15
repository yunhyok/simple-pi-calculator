"""Review v0.2 (docs/REVIEW-v0.2.md) — cache invalidation of the GUI compute path (§3.9).

The GUI keeps one :class:`EngineBridge` (persistent cavity Z-matrix cache) and the module-level
decap model cache for the whole session. For every project input that can affect a result, the
result of the *warm* bridge after the edit must equal a *cold* computation (fresh caches) of the
edited project, and it must differ from the unedited result — or be identical to it for inputs
that are documented not to change results (worker count, layer names, display unit, …).
"""

from __future__ import annotations

import copy
import os
import shutil
import threading
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core.cavity import CavityCache
from simple_pi_calculator.core.decap_model import DecapModelCache
from simple_pi_calculator.core.engine import CancelledError, compute_project
from simple_pi_calculator.core.types import DecapRow
from simple_pi_calculator.gui.engine_bridge import EngineBridge
from simple_pi_calculator.io.project_io import load_project, save_project, to_inputs

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

TWO_SUBCKT_MOD = """* two subcircuits in one library file
.SUBCKT CAP_A 1 2
R1 1 11 20m
L1 11 12 0.40nH
C1 12 2 220nF
.ENDS CAP_A
.SUBCKT CAP_B 1 2
R1 1 11 8m
L1 11 12 0.60nH
C1 12 2 4.7uF
.ENDS CAP_B
"""


@pytest.fixture()
def workspace(tmp_path):
    for name in ("example_project.spical.json", "cap_0402_100nF.mod", "cap_0603_10uF.mod",
                 "cap_0402_100nF_series.s2p"):
        shutil.copy(EXAMPLES / name, tmp_path / name)
    (tmp_path / "lib2.mod").write_text(TWO_SUBCKT_MOD, encoding="utf-8")
    project, _ = load_project(tmp_path / "example_project.spical.json")
    project.sweep.n_points = 41
    project.sweep.show_plane_only = True
    # rows that exercise the subckt and s2p-mode inputs
    project.decap_rows.append(DecapRow("VDD_IO", "lib2.mod", 2, 7.0, subckt="CAP_A"))
    project.decap_rows.append(DecapRow("VDD_CORE", "cap_0402_100nF_series.s2p", 3, 12.0))
    path = tmp_path / "work.spical.json"
    save_project(project, path)
    project, _ = load_project(path)
    return project, str(path)


def _run(bridge, project, path):
    inputs = bridge.make_inputs(project, path)
    results, issues = bridge.compute(inputs, None, None)
    errors = [i for i in issues if i.severity.name == "ERROR"]
    assert not errors, errors
    return {r.name: r for r in results}


def _cold(project, path):
    inputs = to_inputs(project, path)
    results, issues = compute_project(inputs, cache=DecapModelCache(), cavity_cache=None,
                                      workers=1)
    assert not [i for i in issues if i.severity.name == "ERROR"]
    return {r.name: r for r in results}


def _same(a, b, rel=1e-12):
    assert a.keys() == b.keys()
    for k in a:
        za, zb = a[k].z_pad, b[k].z_pad
        assert za.shape == zb.shape
        assert np.allclose(a[k].f_hz, b[k].f_hz, rtol=1e-15, atol=0)
        assert np.max(np.abs(za - zb) / np.abs(zb)) <= rel, k
        if b[k].z_plane_only is not None:
            assert a[k].z_plane_only is not None
            assert np.max(np.abs(a[k].z_plane_only - b[k].z_plane_only)
                          / np.abs(b[k].z_plane_only)) <= rel, k
        else:
            assert a[k].z_plane_only is None


def _differs(a, b, names):
    for k in names:
        za, zb = a[k].z_pad, b[k].z_pad
        if za.shape != zb.shape:
            continue
        assert np.max(np.abs(za - zb) / np.abs(zb)) > 1e-9, f"{k} did not change"


def _layer(p, n):
    return next(lay for lay in p.layers if lay.number == n)


def _pwr(p, name):
    return next(r for r in p.pwr_rows if r.name == name)


# (edit function, PWR nets whose result must change; empty = documented no-op)
EDITS = {
    # stack-up
    "dielectric thickness in cavity": (lambda p: setattr(_layer(p, 4), "thickness_mm", 0.12),
                                       ["VDD_CORE", "VDD_IO"]),  # VDD_IO: via length
    "dielectric Dk in cavity": (lambda p: setattr(_layer(p, 8), "dk", 3.9), ["VDD_IO"]),
    "dielectric Df in cavity": (lambda p: setattr(_layer(p, 4), "df", 0.03), ["VDD_CORE"]),
    "plane conductivity": (lambda p: setattr(_layer(p, 9), "conductivity_s_per_m", 1.0e6),
                           ["VDD_IO"]),
    "plane thickness": (lambda p: setattr(_layer(p, 3), "thickness_mm", 0.018),
                        ["VDD_CORE", "VDD_IO"]),
    "prepreg above the planes (via length only)": (
        lambda p: setattr(_layer(p, 2), "thickness_mm", 0.2), ["VDD_CORE", "VDD_IO"]),
    "thick dielectric above PWR2 (via length only)": (
        lambda p: setattr(_layer(p, 6), "thickness_mm", 0.5), ["VDD_IO"]),
    "layer name": (lambda p: setattr(_layer(p, 4), "name", "RENAMED"), []),
    # PWR list
    "plane pair": (lambda p: (setattr(_pwr(p, "VDD_IO"), "pwr_layer", 5),
                              setattr(_pwr(p, "VDD_IO"), "gnd_layer", 3)), ["VDD_IO"]),
    "PWR/GND swapped": (lambda p: (setattr(_pwr(p, "VDD_IO"), "pwr_layer", 9),
                                   setattr(_pwr(p, "VDD_IO"), "gnd_layer", 7)), []),
    "width": (lambda p: setattr(_pwr(p, "VDD_CORE"), "width_mm", 50.0), ["VDD_CORE"]),
    "number of pads": (lambda p: setattr(_pwr(p, "VDD_IO"), "n_pads", 3), ["VDD_IO"]),
    # decap rows
    "distance": (lambda p: setattr(p.decap_rows[1], "distance_mm", 14.0), ["VDD_CORE"]),
    "count": (lambda p: setattr(p.decap_rows[0], "count", 9), ["VDD_CORE"]),
    "dummy": (lambda p: setattr(p.decap_rows[0], "dummy", True), ["VDD_CORE"]),
    "model file": (lambda p: setattr(p.decap_rows[3], "model_file", "cap_0402_100nF.mod"),
                   ["VDD_IO"]),
    "subckt": (lambda p: setattr(p.decap_rows[4], "subckt", "CAP_B"), ["VDD_IO"]),
    "row s2p mode": (lambda p: setattr(p.decap_rows[5], "s2p_mode", "shunt"), ["VDD_CORE"]),
    "default s2p mode": (lambda p: setattr(p.advanced, "s2p_default_mode", "shunt"),
                         ["VDD_CORE"]),
    "row disabled": (lambda p: setattr(p.decap_rows[2], "enabled", False), ["VDD_IO"]),
    # vias
    "drill": (lambda p: setattr(p.vias, "drill_diameter_mm", 0.25), ["VDD_CORE", "VDD_IO"]),
    "anti-pad": (lambda p: setattr(p.vias, "antipad_diameter_mm", 0.6), ["VDD_CORE", "VDD_IO"]),
    "pitch": (lambda p: setattr(p.vias, "via_pitch_mm", 0.8), ["VDD_CORE", "VDD_IO"]),
    "vias per decap pad": (lambda p: setattr(p.vias, "vias_per_pad", 2), ["VDD_CORE", "VDD_IO"]),
    "PAD vias": (lambda p: setattr(p.vias, "pad_via_count", 2), ["VDD_CORE", "VDD_IO"]),
    "via model": (lambda p: setattr(p.advanced, "via_model", "coax"), ["VDD_CORE", "VDD_IO"]),
    "plating": (lambda p: setattr(p.advanced, "plating_thickness_mm", 0.005),
                ["VDD_CORE", "VDD_IO"]),
    "via conductivity": (lambda p: setattr(p.advanced, "via_conductivity_s_per_m", 1e6),
                         ["VDD_CORE", "VDD_IO"]),
    "mounting inductance": (lambda p: setattr(p.advanced, "mounting_inductance_nh", 0.3),
                            ["VDD_CORE", "VDD_IO"]),
    # sweep (grid changes: compared against the cold run only)
    "f_start": (lambda p: setattr(p.sweep, "f_start_hz", 2e5), []),
    "f_stop": (lambda p: setattr(p.sweep, "f_stop_hz", 2e9), []),
    "n_points": (lambda p: setattr(p.sweep, "n_points", 57), []),
    "plane-only off": (lambda p: setattr(p.sweep, "show_plane_only", False), []),
    # documented no-ops
    "workers": (lambda p: setattr(p.advanced, "workers", 3), []),
    "display unit": (lambda p: setattr(p.display, "z_unit", "ohm"), []),
}
GRID_CHANGES = {"f_start", "f_stop", "n_points"}


@pytest.mark.parametrize("edit", sorted(EDITS))
def test_warm_bridge_equals_cold_after_edit(workspace, edit):
    project, path = workspace
    bridge = EngineBridge()
    base = _run(bridge, project, path)
    _same(base, _cold(project, path))
    fn, changed = EDITS[edit]
    edited = copy.deepcopy(project)
    fn(edited)
    warm = _run(bridge, edited, path)
    _same(warm, _cold(edited, path))
    if edit in GRID_CHANGES:
        return
    if changed:
        _differs(warm, base, changed)
    unchanged = [k for k in base if k not in changed]
    if edit == "plane-only off":
        assert all(r.z_plane_only is None for r in warm.values())
        unchanged = []
        for k in base:
            assert np.array_equal(warm[k].z_pad, base[k].z_pad)
    if unchanged:
        _same({k: warm[k] for k in unchanged}, {k: base[k] for k in unchanged})
    # and back: the original inputs reproduce the original result from the warm caches
    _same(_run(bridge, project, path), base)


def _touch_later(path: Path, text: str) -> None:
    before = os.stat(path).st_mtime_ns
    path.write_text(text, encoding="utf-8")
    if os.stat(path).st_mtime_ns == before:  # coarse file-system clock: force a newer mtime
        os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def test_model_file_edit_invalidates(workspace, tmp_path):
    project, path = workspace
    bridge = EngineBridge()
    base = _run(bridge, project, path)
    mod = tmp_path / "cap_0603_10uF.mod"
    _touch_later(mod, mod.read_text(encoding="utf-8").replace("CNOM=10u", "CNOM=22u"))
    warm = _run(bridge, project, path)
    _same(warm, _cold(project, path))
    _differs(warm, base, ["VDD_CORE", "VDD_IO"])


def test_model_file_edit_same_size_same_mtime_invalidates(workspace, tmp_path):
    """A same-size edit that keeps the modification time (coarse FAT/SMB clocks, tools that
    restore mtimes, fast successive saves) must not return the stale model."""
    project, path = workspace
    bridge = EngineBridge()
    base = _run(bridge, project, path)
    mod = tmp_path / "lib2.mod"
    st = os.stat(mod)
    text = mod.read_text(encoding="utf-8")
    new = text.replace("220nF", "470nF")
    assert len(new) == len(text)
    mod.write_text(new, encoding="utf-8")
    os.utime(mod, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(mod).st_mtime_ns == st.st_mtime_ns and os.stat(mod).st_size == st.st_size
    warm = _run(bridge, project, path)
    _same(warm, _cold(project, path))
    _differs(warm, base, ["VDD_IO"])


def test_s2p_file_edit_invalidates(workspace, tmp_path):
    project, path = workspace
    bridge = EngineBridge()
    base = _run(bridge, project, path)
    s2p = tmp_path / "cap_0402_100nF_series.s2p"
    lines = s2p.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        parts = line.split()
        if parts and not line.startswith(("!", "#")):
            vals = [float(v) for v in parts]
            # S21 = S12 → 0.9·S21 (a different impedance)
            for i in (3, 4, 5, 6):
                vals[i] *= 0.9
            line = " ".join(f"{v:.12e}" for v in vals)
        out.append(line)
    _touch_later(s2p, "\n".join(out) + "\n")
    warm = _run(bridge, project, path)
    _same(warm, _cold(project, path))
    _differs(warm, base, ["VDD_CORE"])


def test_file_replaced_by_other_file_at_same_path(workspace, tmp_path):
    project, path = workspace
    bridge = EngineBridge()
    base = _run(bridge, project, path)
    shutil.copy(tmp_path / "cap_0402_100nF.mod", tmp_path / "tmp.mod")
    os.replace(tmp_path / "tmp.mod", tmp_path / "cap_0603_10uF.mod")
    st = os.stat(tmp_path / "cap_0603_10uF.mod")
    os.utime(tmp_path / "cap_0603_10uF.mod", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    warm = _run(bridge, project, path)
    _same(warm, _cold(project, path))
    _differs(warm, base, ["VDD_CORE", "VDD_IO"])


def test_worker_counts_bit_identical_on_warm_and_cold_caches(workspace):
    project, path = workspace
    inputs = to_inputs(project, path)
    runs = []
    cache = CavityCache()
    for w in (1, 2, 5, 1, 3):
        res, _ = compute_project(inputs, workers=w, cavity_cache=cache, cache=DecapModelCache())
        runs.append(res)
    cold = [compute_project(inputs, workers=w, cavity_cache=None, cache=DecapModelCache())[0]
            for w in (1, 4)]
    for res in runs[1:] + cold:
        for a, b in zip(runs[0], res):
            assert np.array_equal(a.z_pad, b.z_pad), a.name
            assert np.array_equal(a.marker_z, b.marker_z)


def test_cancel_mid_run_leaves_no_partial_cache_entries(workspace):
    """Cancel at many different points of a run: every entry that is in the cache afterwards is
    complete and a later warm run equals a cold run."""
    project, path = workspace
    inputs = to_inputs(project, path)
    cold = {r.name: r for r in compute_project(inputs, cavity_cache=None,
                                               cache=DecapModelCache())[0]}
    cache = CavityCache()
    for stop_after in (1, 3, 8, 20, 40, 80):
        lock = threading.Lock()
        calls = [0]

        def cancel():
            with lock:
                calls[0] += 1
                return calls[0] > stop_after

        try:
            compute_project(inputs, workers=4, cavity_cache=cache, cancel=cancel,
                            cache=DecapModelCache())
        except CancelledError:
            pass
        for z, meta in list(cache._data.values()):
            assert np.all(np.isfinite(z)) and {"M", "N", "capped", "n_dynamic"} <= meta.keys()
    warm = {r.name: r for r in compute_project(inputs, workers=2, cavity_cache=cache,
                                               cache=DecapModelCache())[0]}
    _same(warm, cold)


def test_concurrent_runs_share_caches_safely(workspace):
    """Two projects computed at the same time on one cavity cache and one model cache."""
    project, path = workspace
    other = copy.deepcopy(project)
    other.vias.pad_via_count = 2
    _pwr(other, "VDD_IO").n_pads = 2
    inputs = [to_inputs(project, path), to_inputs(other, path)]
    cold = [{r.name: r for r in compute_project(i, cavity_cache=None, cache=DecapModelCache())[0]}
            for i in inputs]
    cavity, models = CavityCache(max_entries=3), DecapModelCache()
    out: list = [None] * 6
    errors: list = []

    def job(k):
        try:
            res, _ = compute_project(inputs[k % 2], workers=2, cavity_cache=cavity,
                                     cache=models)
            out[k] = {r.name: r for r in res}
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=job, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not errors
    for k in range(6):
        _same(out[k], cold[k % 2])


def test_model_cache_drops_superseded_file_versions(tmp_path):
    from simple_pi_calculator.errors import IssueCollector
    mod = tmp_path / "c.mod"
    cache = DecapModelCache()
    zs = []
    for c in ("100n", "220n", "470n"):
        mod.write_text(f".SUBCKT C 1 2\nC1 1 2 {c}\n.ENDS\n", encoding="utf-8")
        model = cache.get(str(mod), None, None, IssueCollector())
        zs.append(model.impedance(np.array([1e6]))[0])
    assert len(cache) == 1
    assert zs[0] != zs[1] != zs[2]
