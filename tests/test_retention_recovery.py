"""Synthetic retention and crash recovery through the real controller path."""

from pathlib import Path
import hashlib

import cv2
import numpy as np
import pytest

from factory_monitor.config import default_config
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore


def _completed(controller: RuntimeController, event_id: str, completed_at: float) -> Path:
    event_dir = controller.data_dir / "evidence" / event_id
    event_dir.mkdir(parents=True)
    video = event_dir / "evidence.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 2, (16, 16))
    assert writer.isOpened()
    writer.write(np.full((16, 16, 3), int(completed_at) % 256, dtype=np.uint8))
    writer.release()
    capture = cv2.VideoCapture(str(video))
    decoded, _frame = capture.read()
    capture.release()
    assert decoded, "synthetic fixture must be a decodable video"
    (event_dir / "manifest.json").write_text('{"synthetic": true}', encoding="utf-8")
    controller._store.create_event(
        {
            "id": event_id,
            "camera_id": "CAM01",
            "kind": "material_candidate",
            "triggered_at": completed_at - 1,
            "status": "complete",
            "reason": "synthetic fixture",
            "layout_version": 1,
            "recording_status": "complete",
            "analysis_status": "supported",
            "completed_at": completed_at,
            "evidence_path": str(event_dir / "evidence.mp4"),
        }
    )
    return event_dir


def test_media_delete_failure_keeps_event_for_restart_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = default_config()
    config["evidence"]["retain_completed"] = 0
    controller = RuntimeController(config, tmp_path / "isolated-runtime")
    event_dir = _completed(controller, "old", 1.0)

    def interrupted_delete(_path: Path) -> None:
        raise OSError("synthetic deletion interruption")

    monkeypatch.setattr("factory_monitor.runtime.shutil.rmtree", interrupted_delete)
    with pytest.raises(OSError, match="synthetic deletion interruption"):
        controller._prune_completed()

    assert event_dir.is_dir()
    assert controller._store.get_event("old") is not None, "DB identity vanished before media deletion completed"
    intent = controller._store.get_retention_result("old")
    assert intent is not None and intent["result"] is None


def test_retention_keeps_latest_twenty_and_two_inflight_with_hashed_results(tmp_path: Path) -> None:
    config = default_config()
    config["evidence"]["retain_completed"] = 20
    controller = RuntimeController(config, tmp_path / "isolated-runtime")
    originals = {}
    for index in range(25):
        event_id = f"done-{index:02d}"
        event_dir = _completed(controller, event_id, float(index + 1))
        if index < 5:
            originals[event_id] = hashlib.sha256((event_dir / "evidence.mp4").read_bytes()).hexdigest()
    for index in range(2):
        event_id = f"inflight-{index}"
        event_dir = controller.data_dir / "evidence" / event_id
        event_dir.mkdir()
        (event_dir / "active.tmp").write_bytes(b"SYNTHETIC ACTIVE RECORDING")
        controller._store.create_event({
            "id": event_id, "camera_id": "CAM01", "kind": "material_candidate",
            "triggered_at": float(index), "status": "recording",
            "reason": "synthetic fixture", "layout_version": 1,
            "recording_status": "recording",
        })

    controller._prune_completed()

    for index in range(25):
        event_id = f"done-{index:02d}"
        present = controller._store.get_event(event_id) is not None
        assert present is (index >= 5)
        assert (controller.data_dir / "evidence" / event_id).exists() is present
        result = controller._store.get_retention_result(event_id)
        if index < 5:
            assert result is not None and result["result"] == "deleted"
            assert result["reason"] == "retention"
            media = {item["path"]: item for item in result["media"]}
            assert media["evidence.mp4"]["sha256"] == originals[event_id]
        else:
            assert result is None
    for index in range(2):
        event_id = f"inflight-{index}"
        assert controller._store.get_event(event_id) is not None
        assert (controller.data_dir / "evidence" / event_id / "active.tmp").exists()
        assert controller._store.get_retention_result(event_id) is None


def test_partial_media_delete_reconciles_after_controller_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = default_config()
    config["evidence"]["retain_completed"] = 0
    data_dir = tmp_path / "isolated-runtime"
    first = RuntimeController(config, data_dir)
    event_dir = _completed(first, "partial", 1.0)
    original_hash = hashlib.sha256((event_dir / "evidence.mp4").read_bytes()).hexdigest()

    def partial_delete(path: Path) -> None:
        (path / "manifest.json").unlink()
        raise OSError("synthetic partial deletion")

    with monkeypatch.context() as context:
        context.setattr("factory_monitor.runtime.shutil.rmtree", partial_delete)
        with pytest.raises(OSError, match="synthetic partial deletion"):
            first._prune_completed()
    pending = first._store.get_retention_result("partial")
    assert pending is not None and pending["result"] is None
    assert {item["path"] for item in pending["media"]} == {"evidence.mp4", "manifest.json"}
    assert first._store.get_event("partial") is not None
    first._store.close()

    restarted = RuntimeController(config, data_dir)
    restarted._prune_completed()
    result = restarted._store.get_retention_result("partial")
    assert result is not None and result["result"] == "deleted"
    assert next(item for item in result["media"] if item["path"] == "evidence.mp4")["sha256"] == original_hash
    assert restarted._store.get_event("partial") is None
    assert not event_dir.exists()
    restarted._prune_completed()
    assert restarted._store.get_retention_result("partial")["attempts"] == result["attempts"]


def test_store_requires_media_absent_before_finalizing_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    store = EventStore(tmp_path / "events.sqlite3")
    event_dir = root / "event"
    event_dir.mkdir()
    video = event_dir / "evidence.mp4"
    video.write_bytes(b"SYNTHETIC MEDIA")
    store.create_event({
        "id": "event", "camera_id": "CAM01", "kind": "material_candidate",
        "triggered_at": 1.0, "status": "complete", "reason": "synthetic fixture",
        "layout_version": 1, "recording_status": "complete", "completed_at": 2.0,
    })

    intent = store.prepare_retention(0, root)[0]
    assert intent["event_id"] == "event"
    assert intent["media"][0]["sha256"] == hashlib.sha256(b"SYNTHETIC MEDIA").hexdigest()
    with pytest.raises(RuntimeError, match="media still exists"):
        store.complete_retention("event")
    assert store.get_event("event") is not None
    store.fail_retention("event", "synthetic deletion failure")
    store.close()

    recovered = EventStore(tmp_path / "events.sqlite3")
    assert recovered.prepare_retention(0, root)[0]["media"] == intent["media"]
    video.unlink()
    event_dir.rmdir()
    assert recovered.complete_retention("event") is True
    assert recovered.complete_retention("event") is False
    assert recovered.get_event("event") is None
    assert recovered.get_retention_result("event")["attempts"] == 2


def test_analysis_pending_event_is_not_prepared_for_retention(tmp_path: Path) -> None:
    config = default_config()
    config["evidence"]["retain_completed"] = 0
    controller = RuntimeController(config, tmp_path / "isolated-runtime")
    event_dir = _completed(controller, "awaiting-review", 1.0)
    controller._store.update_event("awaiting-review", analysis_status="pending")

    assert controller._store.prepare_retention(0, controller.data_dir / "evidence") == []
    assert event_dir.exists()
    assert controller._store.get_retention_result("awaiting-review") is None


def test_persisted_intent_cannot_delete_same_id_in_another_evidence_root(tmp_path: Path) -> None:
    config = default_config()
    config["evidence"]["retain_completed"] = 0
    controller = RuntimeController(config, tmp_path / "isolated-runtime")
    current_dir = _completed(controller, "same-id", 1.0)
    other_root = tmp_path / "other-evidence"
    other_dir = other_root / "same-id"
    other_dir.mkdir(parents=True)
    (other_dir / "evidence.mp4").write_bytes(b"SYNTHETIC OTHER ROOT")
    controller._store.prepare_retention(0, other_root)

    with pytest.raises(ValueError, match="root"):
        controller._prune_completed()

    assert current_dir.exists() and other_dir.exists()
    assert controller._store.get_event("same-id") is not None


def test_restart_records_result_after_media_was_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = default_config()
    config["evidence"]["retain_completed"] = 0
    data_dir = tmp_path / "isolated-runtime"
    first = RuntimeController(config, data_dir)
    event_dir = _completed(first, "missing-media", 1.0)

    def interrupted_result(_event_id: str) -> bool:
        raise OSError("synthetic result commit interruption")

    with monkeypatch.context() as context:
        context.setattr(first._store, "complete_retention", interrupted_result)
        with pytest.raises(OSError, match="synthetic result commit interruption"):
            first._prune_completed()
    assert not event_dir.exists()
    assert first._store.get_event("missing-media") is not None
    assert first._store.get_retention_result("missing-media")["result"] is None
    first._store.close()

    restarted = RuntimeController(config, data_dir)
    restarted._prune_completed()
    assert restarted._store.get_event("missing-media") is None
    assert restarted._store.get_retention_result("missing-media")["result"] == "deleted"
