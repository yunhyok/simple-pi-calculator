"""Regression tests of review v0.3 (docs/REVIEW-v0.3.md): distance distribution statistics,
determinism of the random stream, engine/preview geometry and export headers."""

from __future__ import annotations

import copy
import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core.decap_model import DecapModelCache
from simple_pi_calculator.core.distribution import (DistanceDistribution, norm_cdf, norm_ppf,
                                                    sample_row_distances,
                                                    truncated_standard_normal)
from simple_pi_calculator.core.engine import compute_project
from simple_pi_calculator.io.export import export_csv_combined, export_touchstone_combined
from simple_pi_calculator.io.project_io import load_project, to_inputs

MM = 1e-3
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "example_project.spical.json"


def _phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _q(x: float) -> float:
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def _ppf_bisect(p: float) -> float:
    """Reference inverse of the double ``p`` by bisection on erfc; in the upper half the
    complement 1 − p (exact) is matched against the upper tail Q(x), which does not round."""
    if p <= 0.5:
        def below(m: float) -> bool:
            return _phi(m) < p
    else:
        r = 1.0 - p

        def below(m: float) -> bool:
            return _q(m) > r
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if below(mid):
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def test_norm_ppf_matches_erf_bisection_over_8_sigma():
    # review v0.3: the upper tail (x > 4) was off by up to 8e-9 because Φ(x) rounds to 1
    xs = np.linspace(-8.0, 8.0, 4001)
    ps = np.array([_phi(x) for x in xs])
    ps = ps[(ps > 0.0) & (ps < 1.0)]
    ref = np.array([_ppf_bisect(p) for p in ps])
    assert np.max(np.abs(norm_ppf(ps) - ref)) < 1e-12
    for x in (4.5, 6.0, 7.5, 8.0):
        p = _phi(x)
        assert float(norm_ppf(p)) == pytest.approx(_ppf_bisect(p), abs=1e-12)
        assert float(norm_ppf(_q(x))) == pytest.approx(-x, abs=1e-12)  # well-resolved lower tail


def test_truncated_normal_empirical_cdf_bounds_and_mean():
    n, d, sigma = 200_000, 12 * MM, 0.5 * MM
    z = truncated_standard_normal(np.random.default_rng(12345), n)
    dist = d + sigma * z
    assert dist.min() >= d - sigma and dist.max() <= d + sigma
    lo, hi = float(norm_cdf(-1.0)), float(norm_cdf(1.0))
    zs = np.sort(z)
    cdf = (norm_cdf(zs) - lo) / (hi - lo)
    ks = max(np.max(np.abs(np.arange(1, n + 1) / n - cdf)), np.max(np.abs(np.arange(n) / n - cdf)))
    assert ks < 3e-3
    sd = sigma * math.sqrt(1 - 2 * math.exp(-0.5) / math.sqrt(2 * math.pi) / (hi - lo))
    assert abs(dist.mean() - d) < 5 * sd / math.sqrt(n)
    assert dist.std() == pytest.approx(sd, rel=0.01)
    # the sampler never lands exactly outside after D + σ·z rounding either
    rows = sample_row_distances([(n, d, False)], DistanceDistribution("normal", sigma, 12345))
    arr = np.asarray(rows[0])
    assert arr.min() >= d - sigma and arr.max() <= d + sigma


@pytest.fixture(scope="module")
def example():
    project, _ = load_project(EXAMPLE)
    project.distance.mode = "normal"
    project.distance.sigma_mm = 0.5
    return project


def _run(project, workers=1, n_points=41):
    inputs = dataclasses.replace(to_inputs(project, str(EXAMPLE)), n_points=n_points)
    results, issues = compute_project(inputs, cache=DecapModelCache(), cavity_cache=None,
                                      workers=workers)
    return {r.name: r for r in results}, issues


def test_normal_mode_bit_identical_across_worker_counts(example):
    one, _ = _run(example, workers=1)
    for workers in (2, 4):
        many, _ = _run(example, workers=workers)
        for name, r in one.items():
            assert np.array_equal(r.z_pad, many[name].z_pad)
            assert np.array_equal(r.placement.xy_m, many[name].placement.xy_m)


def test_samples_independent_of_enabled_pwr_nets_and_equal_to_preview(example):
    pytest.importorskip("PySide6")
    from simple_pi_calculator.gui.engine_bridge import EngineBridge

    both, _ = _run(example)
    only_io = copy.deepcopy(example)
    only_io.pwr_rows[0].enabled = False  # VDD_CORE not computed, its decap rows still draw
    io, _ = _run(only_io)
    assert list(io) == ["VDD_IO"]
    assert np.array_equal(io["VDD_IO"].placement.xy_m, both["VDD_IO"].placement.xy_m)
    bridge = EngineBridge()
    for project, results in ((example, both), (only_io, io)):
        for pwr in project.pwr_rows:
            pv = bridge.placement(project, pwr)
            if pwr.name in results:
                pl = results[pwr.name].placement
                assert np.array_equal(pv.xy_m, pl.xy_m)
                assert np.array_equal(pv.port_distances_m, pl.port_distances_m)
                assert pv.d_ref_m == pl.d_ref_m and pv.height_m == pl.height_m


def test_geometry_dref_pad_row_inside_plane_and_dummy_sets(example):
    res, issues = _run(example)
    for r in res.values():
        pl = r.placement
        d_max = max(row.distance_m for row in example.decap_rows
                    if row.enabled and row.pwr_name == r.name)
        # review v0.3 F4: D_ref = max D + σ bounds every sample
        assert pl.d_ref_m == d_max + 0.5 * MM
        assert pl.d_ref_m >= float(pl.port_distances_m.max())
        assert pl.height_m == pytest.approx(1.4 * (d_max + 0.5 * MM), rel=1e-15)
        assert tuple(pl.xy_m[0]) == (0.5 * pl.width_m, 0.2 * pl.d_ref_m)
        half = 0.5 * pl.port_widths_m
        assert np.all(pl.xy_m[:, 0] - half >= 0) and np.all(pl.xy_m[:, 0] + half <= pl.width_m)
        assert np.all(pl.xy_m[:, 1] - half >= -1e-15)
        assert np.all(pl.xy_m[:, 1] + half <= pl.height_m + 1e-15)
    # VDD_IO: Dummy Cap row of 4 → 2 via sets (2 samples), single row of 1 → 1 sample
    io = res["VDD_IO"]
    assert io.placement.caps_per_port.tolist() == [2, 2, 1]
    assert [(k, j) for k, j, _ in io.sampled_distances] == [(0, 0), (0, 1), (1, 0)]
    msgs = [i.message for i in issues if i.code == "I_DIST_SAMPLED"]
    assert len(msgs) == 2
    vals = [d for _, _, d in io.sampled_distances]
    io_msg = next(m for m in msgs if m.startswith("PWR VDD_IO:"))
    assert "σ = 0.5 mm, seed 12345" in io_msg and "over 3 via set(s)" in io_msg
    assert f"min/mean/max = {min(vals):.4f}/{sum(vals) / 3:.4f}/{max(vals):.4f} mm" in io_msg
    assert f"D_ref = max D + σ = {io.placement.d_ref_m * 1e3:.4f} mm" in io_msg


def test_normal_mode_plane_height_identical_across_seeds(example):
    heights = {}
    for seed in (12345, 1, 2024, 99):
        project = copy.deepcopy(example)
        project.distance.seed = seed
        res, _ = _run(project, n_points=11)
        heights[seed] = {n: (r.placement.height_m, r.placement.d_ref_m) for n, r in res.items()}
        assert all(r.info["H_m"] == r.placement.height_m for r in res.values())
    first = heights[12345]
    assert all(h == first for h in heights.values())
    assert first["VDD_CORE"][0] == pytest.approx(1.4 * 15.5 * MM, rel=1e-15)
    assert first["VDD_IO"][0] == pytest.approx(1.4 * 10.5 * MM, rel=1e-15)
    fixed = copy.deepcopy(example)
    fixed.distance.mode = "fixed"
    res, _ = _run(fixed, n_points=11)
    assert res["VDD_CORE"].placement.d_ref_m == 15 * MM  # fixed mode unchanged


def test_combined_csv_and_touchstone_headers(example, tmp_path):
    res, _ = _run(example)
    results = list(res.values())
    csv_path = export_csv_combined(results, str(tmp_path / "all.csv"))
    header = [ln for ln in Path(csv_path).read_text("utf-8").splitlines() if ln.startswith("#")]
    for name in res:
        assert any(ln.startswith(f"# {name}: Distance distribution: normal truncated to +/-1 "
                                 "sigma, sigma = 0.5 mm, seed = 12345; sampled min/mean/max")
                   for ln in header)
    ts = export_touchstone_combined(results, str(tmp_path / "all"), "all", project=example)
    comments = [ln for ln in Path(ts).read_text("utf-8").splitlines() if ln.startswith("!")]
    dist_lines = [ln for ln in comments if "Distance distribution: normal" in ln]
    assert len(dist_lines) == 2 and all("seed = 12345" in ln for ln in dist_lines)
    assert "over 3 via set(s)" in dist_lines[1]
