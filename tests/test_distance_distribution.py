"""Decap distance distribution (DESIGN.md §2.5.5): truncated-normal sampling, placement, engine,
cache keys and exports."""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core.cavity import CavityCache
from simple_pi_calculator.core.decap_model import DecapModelCache
from simple_pi_calculator.core.distribution import (DistanceDistribution, norm_cdf, norm_ppf,
                                                    row_port_counts, sample_offsets,
                                                    sample_row_distances,
                                                    truncated_standard_normal)
from simple_pi_calculator.core.engine import (compute_project, sample_project_distances,
                                              validate_inputs)
from simple_pi_calculator.core.placement import DecapGroupGeom, place_ports
from simple_pi_calculator.core.types import DecapRow
from simple_pi_calculator.errors import IssueCollector
from simple_pi_calculator.io.export import distance_summary_line, export_csv
from simple_pi_calculator.io.project_io import load_project, to_inputs

MM = 1e-3
W = 0.22369 * MM
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "example_project.spical.json"


def normal(sigma_mm=0.5, seed=12345):
    return DistanceDistribution("normal", sigma_mm * MM, seed)


@pytest.fixture(scope="module")
def base_inputs():
    project, _ = load_project(EXAMPLE)
    inputs = to_inputs(project, str(EXAMPLE))
    return dataclasses.replace(inputs, n_points=61, show_plane_only=True)


def _compute(inputs, **kw):
    results, issues = compute_project(inputs, cache=DecapModelCache(), cavity_cache=None,
                                      workers=1, **kw)
    assert not [i for i in issues if i.severity.name == "ERROR"], issues
    return {r.name: r for r in results}, issues


# ---------------------------------------------------------------------------------------------
# Φ, Φ⁻¹ and the truncated normal
# ---------------------------------------------------------------------------------------------
def test_norm_cdf_and_ppf():
    assert float(norm_cdf(0.0)) == 0.5
    assert float(norm_cdf(1.0)) == pytest.approx(0.8413447460685429, rel=1e-15)
    assert float(norm_ppf(0.975)) == pytest.approx(1.959963984540054, rel=1e-14)
    p = np.array([1e-12, 1e-3, 0.02, 0.1586552539, 0.3, 0.5, 0.7, 0.98, 1 - 1e-9])
    assert np.max(np.abs(norm_cdf(norm_ppf(p)) - p) / p) < 1e-13
    with pytest.raises(ValueError):
        norm_ppf(np.array([0.0, 0.5]))


def test_truncated_normal_bounds_moments_and_reproducibility():
    z = truncated_standard_normal(np.random.default_rng(2024), 200_000)
    assert z.min() >= -1.0 and z.max() <= 1.0
    # truncated N(0,1) on [-1, 1]: mean 0, variance 1 − 2φ(1)/(Φ(1) − Φ(−1)) = 0.29113
    phi1 = math.exp(-0.5) / math.sqrt(2 * math.pi)
    var = 1 - 2 * phi1 / (float(norm_cdf(1.0)) - float(norm_cdf(-1.0)))
    assert abs(z.mean()) < 5 * math.sqrt(var / z.size)
    assert z.var() == pytest.approx(var, rel=0.01)
    a = sample_offsets([3, 0, 4], 12345)
    b = sample_offsets([3, 0, 4], 12345)
    assert [x.tolist() for x in a] == [x.tolist() for x in b]
    assert a[1].size == 0
    # one stream: the rows are consecutive slices of a single draw
    flat = sample_offsets([7], 12345)[0]
    assert np.array_equal(np.concatenate(a), flat)
    # platform-independent values for the default seed (PCG64 + inverse CDF)
    assert flat[:3] == pytest.approx([-0.48496, -0.31889, 0.53307], abs=5e-5)
    assert not np.array_equal(sample_offsets([7], 12346)[0], flat)


def test_sample_row_distances_per_via_set():
    rows = [(10, 8 * MM, False), (5, 5 * MM, True), (0, 3 * MM, False)]
    assert row_port_counts([r[0] for r in rows], [r[2] for r in rows]) == [10, 3, 0]
    assert sample_row_distances(rows, None) == [None, None, None]
    assert sample_row_distances(rows, DistanceDistribution()) == [None, None, None]
    d = sample_row_distances(rows, normal(0.5))
    assert [len(x) for x in d] == [10, 3, 0]
    for (_, dist, _), ds in zip(rows, d):
        assert all(dist - 0.5 * MM <= v <= dist + 0.5 * MM for v in ds)
    assert DistanceDistribution("normal", 0.0, 1).validate()[0][0] == "E_DIST_SIGMA"
    assert DistanceDistribution("normal", 1e-3, -1).validate()[0][0] == "E_DIST_SEED"
    assert DistanceDistribution("uniform").validate()[0][0] == "E_DIST_MODE"


def test_sample_mean_close_to_row_distance():
    rows = [(2000, 10 * MM, False)]
    d = np.array(sample_row_distances(rows, normal(0.5, seed=7))[0])
    # σ_mean = 0.5 mm · 0.5396 / √2000 ≈ 6 µm
    assert d.mean() == pytest.approx(10 * MM, abs=25e-6)
    assert d.min() >= 9.5 * MM and d.max() <= 10.5 * MM


# ---------------------------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------------------------
def test_placement_ports_scattered_d_ref_and_h():
    groups_fixed = [DecapGroupGeom(10, 8 * MM), DecapGroupGeom(4, 15 * MM)]
    dists = sample_row_distances([(10, 8 * MM, False), (4, 15 * MM, False)], normal(0.5))
    groups = [dataclasses.replace(g, port_distances_m=d) for g, d in zip(groups_fixed, dists)]
    fixed = place_ports(60 * MM, groups_fixed, W, W, IssueCollector(), "t")
    pl = place_ports(60 * MM, groups, W, W, IssueCollector(), "t", sigma_m=0.5 * MM)
    all_d = [v for d in dists for v in d]
    assert pl.d_ref_m == 15.5 * MM and pl.height_m == pytest.approx(1.4 * 15.5 * MM, rel=1e-15)
    # without σ (direct caller) the largest given distance is used
    legacy = place_ports(60 * MM, groups, W, W, IssueCollector(), "t")
    assert legacy.d_ref_m == max(all_d)
    assert np.array_equal(pl.xy_m[:, 0], fixed.xy_m[:, 0])  # x pattern unchanged
    y_pad = 0.2 * pl.d_ref_m
    assert pl.xy_m[0, 1] == pytest.approx(y_pad, rel=1e-15)
    assert np.allclose(pl.xy_m[1:, 1], y_pad + np.array(all_d), rtol=0, atol=1e-15)
    assert np.allclose(pl.port_distances_m, all_d, rtol=0, atol=0)
    assert np.all(pl.xy_m[1:, 1] <= pl.height_m - 0.2 * pl.d_ref_m + 1e-12)
    assert len(np.unique(pl.xy_m[1:11, 1])) == 10
    assert np.array_equal(fixed.port_distances_m, [8 * MM] * 10 + [15 * MM] * 4)


def test_placement_sub_rows_and_dummy_sets_keep_pattern():
    # 400 caps on a 10 mm plane crowd into sub-rows: each port = fixed position + (d_j − D)
    geom = DecapGroupGeom(400, 20 * MM, dummy=True)
    fixed = place_ports(10 * MM, [geom], W, W, IssueCollector(), "t")
    (d,) = sample_row_distances([(400, 20 * MM, True)], normal(0.3))
    assert len(d) == 200
    issues = IssueCollector()
    pl = place_ports(10 * MM, [dataclasses.replace(geom, port_distances_m=d)], W, W, issues, "t")
    shift = 0.2 * (pl.d_ref_m - fixed.d_ref_m)
    assert np.array_equal(pl.xy_m[:, 0], fixed.xy_m[:, 0])
    assert np.allclose(pl.xy_m[1:, 1], fixed.xy_m[1:, 1] + shift + (np.array(d) - 20 * MM),
                       rtol=0, atol=1e-12)
    assert pl.caps_per_port.tolist() == fixed.caps_per_port.tolist()
    with pytest.raises(ValueError):
        place_ports(10 * MM, [dataclasses.replace(geom, port_distances_m=d[:5])], W, W,
                    IssueCollector(), "t")


def test_placement_clipping_warning_for_sampled_ports():
    # σ ≥ D: a sample below the PAD row would leave the plane (y < w/2) and is clipped
    d = (-0.5 * MM, 1.9 * MM, 1.0 * MM)
    issues = IssueCollector()
    pl = place_ports(30 * MM, [DecapGroupGeom(3, 1 * MM, port_distances_m=d)], W, W, issues, "t")
    assert pl.d_ref_m == 1.9 * MM
    codes = [i.code for i in issues.issues]
    assert "W_DECAP_TOO_CLOSE" in codes  # min sampled distance < (w + w_pad)/2
    clipped = [i for i in issues.issues if i.code == "W_DECAP_CLIPPED"]
    assert clipped and "1 port(s)" in clipped[0].message
    assert pl.xy_m[1, 1] == pytest.approx(0.5 * W, rel=1e-12)
    assert np.all(pl.xy_m[:, 1] >= 0.5 * W) and np.all(pl.xy_m[:, 1] <= pl.height_m - 0.5 * W)


# ---------------------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------------------
def test_fixed_mode_is_the_default_path(base_inputs):
    explicit = dataclasses.replace(base_inputs, distance=DistanceDistribution("fixed", 7e-3, 99))
    a, ia = _compute(base_inputs)
    b, ib = _compute(explicit)
    for name in a:
        assert np.array_equal(a[name].z_pad, b[name].z_pad)
        assert np.array_equal(a[name].placement.xy_m, b[name].placement.xy_m)
        assert a[name].distance is None and a[name].distance_mode == "fixed"
        assert [d for _, _, d in a[name].sampled_distances] == \
            (a[name].placement.port_distances_m * 1e3).tolist()
    assert "I_DIST_SAMPLED" not in [i.code for i in ia + ib]


def test_tiny_sigma_equals_fixed(base_inputs):
    """σ → 0 reproduces the fixed result. The deviation is physical and linear in σ (the ports
    and H move by ≈ σ): at the sharp VDD_IO plane resonance it is 1.3e-6 per nm, so σ = 0.1 nm
    is used for the 1e-6 bound."""
    fixed, _ = _compute(base_inputs)
    tiny, issues = _compute(dataclasses.replace(base_inputs, distance=normal(1e-7)))
    for name in fixed:
        zf, zt = fixed[name].z_pad, tiny[name].z_pad
        assert np.max(np.abs(zt - zf) / np.abs(zf)) < 1e-6
        assert tiny[name].placement.d_ref_m == pytest.approx(fixed[name].placement.d_ref_m,
                                                             abs=1.1e-10)
    assert [i.code for i in issues].count("I_DIST_SAMPLED") == 2


def test_normal_mode_bounds_reproducible_and_seed_dependent(base_inputs):
    inputs = dataclasses.replace(base_inputs, distance=normal(0.5))
    a, issues = _compute(inputs)
    b, _ = _compute(inputs)
    c, _ = _compute(dataclasses.replace(base_inputs, distance=normal(0.5, seed=1)))
    fixed, _ = _compute(base_inputs)
    rows = inputs.decap_rows
    for name, res in a.items():
        enabled = [r for r in rows if r.enabled and r.pwr_name == name]
        assert res.distance == normal(0.5)
        for k, j, d in res.sampled_distances:
            assert enabled[k].distance_mm - 0.5 <= d <= enabled[k].distance_mm + 0.5
        # per-via-set sampling for the Dummy Cap row (VDD_IO: 4 caps, dummy → 2 samples)
        counts = {}
        for k, _, _ in res.sampled_distances:
            counts[k] = counts.get(k, 0) + 1
        assert [counts.get(k, 0) for k in range(len(enabled))] == \
            [math.ceil(r.count / 2) if r.dummy else r.count for r in enabled]
        # D_ref = max D + σ (review v0.3, F4): seed-independent, bounds every sample
        d_max = max(r.distance_m for r in enabled)
        assert res.placement.d_ref_m == pytest.approx(d_max + 0.5 * MM, rel=1e-15)
        assert res.placement.d_ref_m * 1e3 >= max(d for _, _, d in res.sampled_distances)
        assert res.info["H_m"] == pytest.approx(1.4 * res.placement.d_ref_m, rel=1e-15)
        assert res.placement.height_m == c[name].placement.height_m
        assert np.array_equal(res.z_pad, b[name].z_pad)
        assert res.sampled_distances == b[name].sampled_distances
        assert not np.array_equal(res.z_pad, c[name].z_pad)
        assert np.max(np.abs(res.z_pad - fixed[name].z_pad) / np.abs(fixed[name].z_pad)) > 1e-6
    msgs = [i for i in issues if i.code == "I_DIST_SAMPLED"]
    assert len(msgs) == 2 and "seed 12345" in msgs[0].message and "σ = 0.5 mm" in msgs[0].message
    assert "min/mean/max" in msgs[0].message


def test_one_stream_over_the_decap_table(base_inputs):
    """Rows draw in table order across PWR nets; the engine and the GUI helper agree."""
    dist = normal(0.5)
    sampled = sample_project_distances(base_inputs.decap_rows, dist)
    rows = [(r.count, r.distance_m, r.dummy) for r in base_inputs.decap_rows]
    assert sampled == dict(enumerate(sample_row_distances(rows, dist)))
    results, _ = _compute(dataclasses.replace(base_inputs, distance=dist))
    io_rows = [i for i, r in enumerate(base_inputs.decap_rows) if r.pwr_name == "VDD_IO"]
    io_d = [d for i in io_rows for d in sampled[i]]
    assert [d for _, _, d in results["VDD_IO"].sampled_distances] == \
        pytest.approx([v * 1e3 for v in io_d], rel=1e-15)
    # the PWR order / computing a single net does not change the samples
    only_io = dataclasses.replace(base_inputs, distance=dist,
                                  pwrs=[p for p in base_inputs.pwrs if p.name == "VDD_IO"])
    single, _ = _compute(only_io)
    assert np.array_equal(single["VDD_IO"].z_pad, results["VDD_IO"].z_pad)


def test_invalid_distribution_is_a_global_error(base_inputs):
    bad = dataclasses.replace(base_inputs, distance=DistanceDistribution("normal", -1.0, 5))
    assert "E_DIST_SIGMA" in [i.code for i in validate_inputs(bad)]
    results, issues = compute_project(bad, cavity_cache=None)
    assert results == [] and "E_DIST_SIGMA" in [i.code for i in issues]


def test_large_sigma_warning(base_inputs):
    _, issues = _compute(dataclasses.replace(base_inputs, distance=normal(6.0)))
    assert "W_DIST_SIGMA_LARGE" in [i.code for i in issues]  # VDD_IO row at 5 mm


def test_cache_key_contains_distribution(base_inputs):
    cache = CavityCache()
    kw = dict(cache=DecapModelCache(), cavity_cache=cache, workers=1)
    compute_project(base_inputs, **kw)
    n_fixed = len(cache)
    for dist in (normal(0.5), normal(0.4), normal(0.4, seed=3)):
        warm, _ = compute_project(dataclasses.replace(base_inputs, distance=dist), **kw)
        cold, _ = _compute(dataclasses.replace(base_inputs, distance=dist))
        for r in warm:
            assert np.array_equal(r.z_pad, cold[r.name].z_pad)
    assert len(cache) == n_fixed + 3 * len(base_inputs.pwrs)
    hits = cache.hits
    compute_project(dataclasses.replace(base_inputs, distance=normal(0.5)), **kw)
    assert cache.hits == hits + len(base_inputs.pwrs)


def test_export_headers_carry_distance_summary(base_inputs, tmp_path):
    res, _ = _compute(dataclasses.replace(base_inputs, distance=normal(0.5, seed=42)))
    line = distance_summary_line(res["VDD_CORE"])
    assert "normal" in line and "seed = 42" in line and "sigma = 0.5 mm" in line
    (path,) = export_csv([res["VDD_CORE"]], str(tmp_path), "t")
    header = [ln for ln in Path(path).read_text("utf-8").splitlines() if ln.startswith("#")]
    assert any("Distance distribution: normal" in ln for ln in header)
    fixed, _ = _compute(base_inputs)
    assert distance_summary_line(fixed["VDD_IO"]).startswith("Distance distribution: fixed")


def test_rows_with_zero_count_do_not_break_sampling(base_inputs):
    rows = [dataclasses.replace(r) for r in base_inputs.decap_rows]
    rows.insert(0, DecapRow("VDD_CORE", rows[0].model_file, 3, 4.0, enabled=False))
    sampled = sample_project_distances(rows, normal(0.5))
    assert 0 not in sampled
    assert sampled[1] == sample_project_distances(base_inputs.decap_rows, normal(0.5))[0]
