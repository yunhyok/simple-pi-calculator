"""Bundled example decap models (DESIGN.md §2.7.1, §4.5) and the DecapModelCache."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from simple_pi_calculator.core.decap_model import (DecapModel, DecapModelCache, S2pDecapModel,
                                                   SpiceDecapModel, load_decap_model)
from simple_pi_calculator.errors import InputError, IssueCollector

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DATA = Path(__file__).resolve().parent / "data"


def srf_and_min(model, f_lo, f_hi, n=40001):
    f = np.logspace(np.log10(f_lo), np.log10(f_hi), n)
    z = model.impedance(f)
    i = int(np.argmin(np.abs(z)))
    return f[i], abs(z[i]), z[i]


def test_example_0402_srf_esr():
    model = load_decap_model(EXAMPLES / "cap_0402_100nF.mod", None, None, IssueCollector())
    assert isinstance(model, SpiceDecapModel) and isinstance(model, DecapModel)
    f0, zmin, z = srf_and_min(model, 10e6, 50e6)
    assert f0 == pytest.approx(23.73e6, rel=1e-3)
    assert zmin == pytest.approx(30e-3, rel=1e-4)
    assert z.real == pytest.approx(30e-3, rel=1e-6)   # ESR


def test_example_0603_srf_esr():
    model = load_decap_model(EXAMPLES / "cap_0603_10uF.mod", None, None, IssueCollector())
    f0, zmin, z = srf_and_min(model, 1e6, 5e6)
    assert f0 == pytest.approx(2.251e6, rel=1e-3)
    assert zmin == pytest.approx(5.0e-3, rel=1e-2)
    assert z.real == pytest.approx(5.0e-3, rel=1e-2)   # RS 3 mΩ + R2 2 mΩ


def test_example_s2p_matches_mod():
    issues = IssueCollector()
    s2p = load_decap_model(EXAMPLES / "cap_0402_100nF_series.s2p", None, None, issues)
    mod = load_decap_model(EXAMPLES / "cap_0402_100nF.mod", None, None, issues)
    assert isinstance(s2p, S2pDecapModel) and s2p.mode == "series"
    assert s2p.data.f_hz.size == 201
    assert s2p.data.f_hz[0] == pytest.approx(1e3) and s2p.data.f_hz[-1] == pytest.approx(3e9)
    # at the file's own points the conversion recovers the model (series-through, 16 digits)
    np.testing.assert_allclose(s2p.z_src, mod.impedance(s2p.data.f_hz), rtol=1e-6)
    f0, zmin, _ = srf_and_min(s2p, 10e6, 50e6, n=4001)
    assert f0 == pytest.approx(23.73e6, rel=2e-2)
    assert zmin == pytest.approx(30e-3, rel=5e-2)
    assert not issues.issues


def test_s2p_extrapolation_warnings_via_model():
    s2p = load_decap_model(EXAMPLES / "cap_0402_100nF_series.s2p", None, "series", IssueCollector())
    issues = IssueCollector()
    z = s2p.impedance(np.array([500.0, 1e6, 5e9]), issues)
    assert np.all(np.isfinite(z))
    assert {"W_S2P_EXTRAP_LOW", "W_S2P_EXTRAP_HIGH"} <= set(issues.codes())


def test_cache_hits_and_mtime_invalidation(tmp_path):
    src = tmp_path / "cap.mod"
    shutil.copy(EXAMPLES / "cap_0603_10uF.mod", src)
    cache = DecapModelCache()
    m1 = cache.get(str(src), None, None, IssueCollector())
    m2 = cache.get(str(src), None, "shunt", IssueCollector())   # s2p mode irrelevant for SPICE
    assert m1 is m2 and len(cache) == 1
    text = src.read_text().replace("R2 N2 PIN2 2m", "R2 N2 PIN2 4m")
    src.write_text(text)
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    m3 = cache.get(str(src), None, None, IssueCollector())
    assert m3 is not m1
    _, zmin, _ = srf_and_min(m3, 1e6, 5e6, n=2001)
    assert zmin == pytest.approx(7e-3, rel=2e-2)


def test_cache_replays_warnings(tmp_path):
    src = tmp_path / "multi.mod"
    src.write_text(".SUBCKT A 1 2\nR1 1 2 1\n.ENDS\n.SUBCKT B 1 2\nR1 1 2 2\n.ENDS\n")
    cache = DecapModelCache()
    i1, i2 = IssueCollector(), IssueCollector()
    cache.get(str(src), None, None, i1)
    cache.get(str(src), None, None, i2)
    assert i1.codes() == i2.codes() == ["W_SPICE_MULTI_TOP"]
    assert cache.get(str(src), "b", None, IssueCollector()) is not cache.get(str(src), None, None,
                                                                            IssueCollector())


def test_file_errors(tmp_path):
    cache = DecapModelCache()
    issues = IssueCollector()
    with pytest.raises(InputError) as exc:
        cache.get(str(tmp_path / "missing.mod"), None, None, issues)
    assert exc.value.issues[0].code == "E_DECAP_FILE_NOT_FOUND"
    bad = tmp_path / "cap.txt"
    bad.write_text("x")
    with pytest.raises(InputError) as exc:
        cache.get(str(bad), None, None, issues)
    assert exc.value.issues[0].code == "E_DECAP_FILE_TYPE"
    assert len(cache) == 0


def test_vendor_style_fixture_model():
    model = load_decap_model(DATA / "vendor_style_0402_104.mod", None, None, IssueCollector())
    f = np.logspace(3, 9.5, 2001)
    z = model.impedance(f)
    i = int(np.argmin(np.abs(z)))
    assert 10e6 < f[i] < 60e6
    assert 10e-3 < abs(z[i]) < 60e-3
    assert model.label.endswith("SYN0402X7R104")
