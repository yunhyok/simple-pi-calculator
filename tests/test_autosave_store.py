"""Qt-free auto-save store (DESIGN.md §5.8.1–§5.8.3, §8.10)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from simple_pi_calculator.core.types import DecapRow, LayerRow, PwrRow
from simple_pi_calculator.io.project_io import (
    AutosaveStore,
    PlotView,
    Project,
    Session,
    WindowState,
    project_to_dict,
    resolve_appdata_dir,
)


def _codes(issues) -> list[str]:
    return [i.code for i in issues]


def _project(tmp_path: Path, drill: float = 0.2) -> Project:
    p = Project()
    p.layers = [LayerRow(1, "TOP", 0.035, 5.8e7, None, None), LayerRow(2, "PP", 0.1, None, 4.2, 0.02),
                LayerRow(3, "GND", 0.035, 5.8e7, None, None)]
    p.vias.drill_diameter_mm = drill
    p.pwr_rows = [PwrRow("VDD", 1, 3, 30.0)]
    p.decap_rows = [DecapRow("VDD", str(tmp_path / "models" / "cap.mod"), 4, 5.0, dummy=True)]
    return p


def _session(tmp_path: Path) -> Session:
    return Session(
        project_path=str(tmp_path / "board.spical.json").replace("\\", "/"),
        modified=True,
        recent_files=[str(tmp_path / "board.spical.json").replace("\\", "/")],
        window=WindowState(geometry_b64="AdnQyw==", state_b64="AAAA/w==", splitter_sizes=[440, 660],
                           input_tab=3, result_tab="VDD", decap_filter="VDD",
                           message_dock_visible=False),
        plots={"VDD": PlotView(auto_range=False, x_range_log10=(5.0, 9.0),
                               y_range_log10=(-0.2, 3.5))},
        had_results=True,
    )


def _truncate(path: str) -> None:
    data = Path(path).read_bytes()
    Path(path).write_bytes(data[: len(data) // 2])


# 1 ---------------------------------------------------------------------------------------------
def test_first_run_defaults(tmp_path: Path):
    store = AutosaveStore(str(tmp_path / "appdata"))
    result = store.load()
    assert result.source == "defaults"
    assert result.issues == []
    assert result.project == Project()
    assert result.session == Session()
    assert store.path == str(tmp_path / "appdata" / "autosave.spical.json")


# 2 ---------------------------------------------------------------------------------------------
def test_save_load_identical_and_skip_unchanged(tmp_path: Path):
    store = AutosaveStore(str(tmp_path))
    project, session = _project(tmp_path), _session(tmp_path)
    assert store.save(project, session) is True
    assert session.saved_utc and session.saved_utc.endswith("Z")
    doc = json.loads(Path(store.path).read_text(encoding="utf-8"))
    assert list(doc.keys())[-1] == "session"
    assert os.path.isabs(doc["decaps"]["rows"][0]["model_file"])

    result = AutosaveStore(str(tmp_path)).load()
    assert result.source == "primary"
    assert result.project == project
    assert result.session == session
    assert not result.issues

    mtime = os.stat(store.path).st_mtime_ns
    time.sleep(0.02)
    session.saved_utc = None            # saved_utc is excluded from the comparison
    assert store.save(project, session) is False
    assert os.stat(store.path).st_mtime_ns == mtime
    # a fresh store instance also recognises identical on-disk content
    assert AutosaveStore(str(tmp_path)).save(project, session) is False

    first = Path(store.path).read_text(encoding="utf-8")
    assert store.save(_project(tmp_path, drill=0.3), session) is True
    assert Path(store.backup_path).read_text(encoding="utf-8") == first
    assert json.loads(Path(store.path).read_text(encoding="utf-8"))["vias"][
        "drill_diameter_mm"] == 0.3
    assert not list(tmp_path.glob("*.tmp"))


# 3 ---------------------------------------------------------------------------------------------
def test_truncated_primary_recovers_backup(tmp_path: Path):
    store = AutosaveStore(str(tmp_path))
    store.save(_project(tmp_path, 0.2), _session(tmp_path))
    store.save(_project(tmp_path, 0.3), _session(tmp_path))
    _truncate(store.path)

    result = AutosaveStore(str(tmp_path)).load()
    assert result.source == "backup"
    assert result.project.vias.drill_diameter_mm == 0.2
    assert "W_AUTOSAVE_RECOVERED_BACKUP" in _codes(result.issues)
    assert len(list(tmp_path.glob("autosave.corrupt-*.spical.json"))) == 1
    assert not os.path.exists(store.path)


# 4 ---------------------------------------------------------------------------------------------
def test_both_corrupt_defaults_and_quarantine_limit(tmp_path: Path):
    store = AutosaveStore(str(tmp_path))
    store.save(_project(tmp_path, 0.2), _session(tmp_path))
    store.save(_project(tmp_path, 0.3), _session(tmp_path))
    _truncate(store.path)
    Path(store.backup_path).write_text("{ nope", encoding="utf-8")

    result = store.load()
    assert result.source == "defaults"
    assert "W_AUTOSAVE_CORRUPT" in _codes(result.issues)
    quarantined = list(tmp_path.glob("autosave.corrupt-*.spical.json"))
    assert len(quarantined) == 2
    msg = [i for i in result.issues if i.code == "W_AUTOSAVE_CORRUPT"][0].message
    assert all(q.name in msg for q in quarantined)

    for _ in range(5):                   # 2 + 5 = 7 corrupt events
        Path(store.path).write_text("garbage", encoding="utf-8")
        store.load()
    remaining = store.quarantined_files()
    assert len(remaining) == 5
    assert len(list(tmp_path.glob("autosave.corrupt-*.spical.json"))) == 5


# 5 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("with_backup", [True, False])
def test_newer_primary(tmp_path: Path, with_backup: bool):
    store = AutosaveStore(str(tmp_path))
    store.save(_project(tmp_path, 0.2), _session(tmp_path))
    if with_backup:
        store.save(_project(tmp_path, 0.3), _session(tmp_path))
    doc = json.loads(Path(store.path).read_text(encoding="utf-8"))
    doc["schema_version"] = 99
    Path(store.path).write_text(json.dumps(doc), encoding="utf-8")
    if not with_backup:
        assert not os.path.exists(store.backup_path)

    result = store.load()
    assert "W_AUTOSAVE_NEWER" in _codes(result.issues)
    assert len(list(tmp_path.glob("autosave.v99-newer-*.spical.json"))) == 1
    if with_backup:
        assert result.source == "backup"
        assert result.project.vias.drill_diameter_mm == 0.2
    else:
        assert result.source == "defaults"
    assert "W_AUTOSAVE_CORRUPT" not in _codes(result.issues)


# 6 ---------------------------------------------------------------------------------------------
def test_semantic_problems_are_not_corruption(tmp_path: Path):
    store = AutosaveStore(str(tmp_path))
    project = _project(tmp_path)
    project.decap_rows[0].model_file = str(tmp_path / "does" / "not" / "exist.mod")
    project.pwr_rows[0].pwr_layer = 42           # invalid layer reference
    project.vias.drill_diameter_mm = -1.0        # out of range
    store.save(project, _session(tmp_path))
    result = AutosaveStore(str(tmp_path)).load()
    assert result.source == "primary"
    assert result.project == project
    assert not [i for i in result.issues if i.code.startswith("W_AUTOSAVE")]


# 7 ---------------------------------------------------------------------------------------------
def test_crash_between_rotate_and_replace(tmp_path: Path):
    store = AutosaveStore(str(tmp_path))
    store.save(_project(tmp_path, 0.25), _session(tmp_path))
    os.replace(store.path, store.backup_path)     # crash after step 3 of §5.8.2
    result = AutosaveStore(str(tmp_path)).load()
    assert result.source == "backup"
    assert result.project.vias.drill_diameter_mm == 0.25


# 8 ---------------------------------------------------------------------------------------------
def test_appdata_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SPICAL_APPDATA_DIR", str(tmp_path / "portable"))
    assert resolve_appdata_dir("/qt/default") == str(tmp_path / "portable")
    monkeypatch.delenv("SPICAL_APPDATA_DIR")
    assert resolve_appdata_dir("/qt/default") == "/qt/default"
    assert resolve_appdata_dir().endswith("SimplePICalculator")


def test_unknown_session_keys_silently_ignored(tmp_path: Path):
    store = AutosaveStore(str(tmp_path))
    doc = project_to_dict(_project(tmp_path), None, _session(tmp_path))
    doc["session"]["future_thing"] = {"x": 1}
    doc["session"]["window"]["splitter_sizes"] = "bad"
    Path(store.path).write_text(json.dumps(doc), encoding="utf-8")
    result = store.load()
    assert result.source == "primary"
    assert result.issues == []
    assert result.session.window.splitter_sizes == []
