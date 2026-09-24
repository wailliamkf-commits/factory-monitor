"""Synthetic stop/restart persistence failures with no live writer."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import pytest

from factory_monitor import evidence as evidence_module
from factory_monitor.config import default_config
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore


def _pending(controller: RuntimeController, event_id: str) -> None:
    controller._store.create_event({
        "id": event_id, "camera_id": "CAM01", "kind": "material_candidate",
        "triggered_at": 1.0, "status": "recording", "reason": "synthetic unfinished recording",
        "layout_version": 1, "recording_status": "recording", "analysis_status": "uncertain",
    })


def _stoppable(controller: RuntimeController) -> None:
    controller._acquire_runtime_ownership()
    controller._running = True
    controller._source = "demo"
    controller._stop_event = threading.Event()
    controller._processes = {}
    controller._evidence_commands = queue.Queue()


def test_manifest_write_failure_persists_deferred_stop_and_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    controller = RuntimeController(default_config(), tmp_path)
    _pending(controller, "manifest-failure")
    _stoppable(controller)
    original = evidence_module._atomic_json
    with monkeypatch.context() as context:
        context.setattr(evidence_module, "_atomic_json", lambda *_args: (_ for _ in ()).throw(OSError("synthetic manifest write failure")))
        controller.stop()
    first = controller.status()["last_stop"]
    assert first["confirmed"] is False and first["retry_required"] is True
    assert first["terminalization_deferred"] is True
    assert "manifest-failure" in first["affected_event_ids"]
    assert controller._lock_handle is not None
    assert Path(first["report_path"]).is_file()
    assert controller._store.get_event("manifest-failure")["recording_status"] == "recording"
    assert not (tmp_path / "evidence" / "manifest-failure" / "manifest.json").exists()
    assert evidence_module._atomic_json is original
    controller.stop()
    row = controller._store.get_event("manifest-failure")
    assert row["recording_status"] == "incomplete" and row["completed_at"] is not None
    assert controller.status()["last_stop"]["retry_required"] is False


def test_db_update_failure_keeps_manifest_and_retry_does_not_duplicate_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    controller = RuntimeController(default_config(), tmp_path)
    _pending(controller, "db-failure")
    _stoppable(controller)
    original = controller._store.update_event
    with monkeypatch.context() as context:
        def fail_terminal_update(event_id: str, **fields):
            if event_id == "db-failure" and fields.get("status") == "incomplete":
                raise OSError("synthetic DB terminal update failure")
            return original(event_id, **fields)
        context.setattr(controller._store, "update_event", fail_terminal_update)
        controller.stop()
    first = controller.status()["last_stop"]
    assert first["confirmed"] is False and first["retry_required"] is True
    assert first["terminalization_deferred"] is True
    assert controller._lock_handle is not None
    manifest_path = tmp_path / "evidence" / "db-failure" / "manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert before["recording_status"] == "incomplete"
    assert controller._store.get_event("db-failure")["recording_status"] == "recording"
    controller.stop()
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after["gap_reasons"] == before["gap_reasons"]
    assert after["completed_at"] == before["completed_at"]
    assert controller._store.get_event("db-failure")["recording_status"] == "incomplete"
    assert controller.status()["last_stop"]["retry_required"] is False


def test_restart_terminalizes_db_only_recording_without_inventing_media(tmp_path: Path):
    data_dir = tmp_path / "isolated"
    store = EventStore(data_dir / "events.sqlite3")
    store.create_event({
        "id": "before-command", "camera_id": "CAM01", "kind": "material_candidate",
        "triggered_at": 1.0, "status": "recording", "reason": "synthetic DB-before-command interruption",
        "layout_version": 1, "recording_status": "recording", "analysis_status": "uncertain",
    })
    store.close()
    controller = RuntimeController(default_config(), data_dir)
    try:
        controller.start("demo")
        row = controller._store.get_event("before-command")
        manifest_path = data_dir / "evidence" / "before-command" / "manifest.json"
        assert row["status"] == "incomplete" and row["completed_at"] is not None
        assert row["window_complete"] is False
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["recording_status"] == "incomplete"
        assert manifest["frame_count"] == 0
        assert manifest["window_complete"] is False
    finally:
        controller.stop()
    first_manifest = manifest_path.read_bytes()
    first_completed_at = row["completed_at"]
    restarted = RuntimeController(default_config(), data_dir)
    try:
        restarted.start("demo")
        assert manifest_path.read_bytes() == first_manifest
        assert restarted._store.get_event("before-command")["completed_at"] == first_completed_at
    finally:
        restarted.stop()


def test_recovery_retries_after_startup_manifest_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "isolated"
    store = EventStore(data_dir / "events.sqlite3")
    store.create_event({
        "id": "retry-start", "camera_id": "CAM01", "kind": "material_candidate",
        "triggered_at": 1.0, "status": "recording", "reason": "synthetic startup retry",
        "layout_version": 1, "recording_status": "recording", "analysis_status": "uncertain",
    })
    store.close()
    first = RuntimeController(default_config(), data_dir)
    with monkeypatch.context() as context:
        def fail_manifest(*_args):
            raise OSError("synthetic startup manifest failure")
        context.setattr(evidence_module, "_atomic_json", fail_manifest)
        with pytest.raises(OSError, match="synthetic startup manifest failure"):
            first.start("demo")
    assert first._lock_handle is None
    assert first._store.get_event("retry-start").get("completed_at") is None
    first._store.close()

    restarted = RuntimeController(default_config(), data_dir)
    try:
        restarted.start("demo")
        row = restarted._store.get_event("retry-start")
        manifest = data_dir / "evidence" / "retry-start" / "manifest.json"
        assert row["status"] == "incomplete" and row["completed_at"] is not None
        assert manifest.is_file()
    finally:
        restarted.stop()
