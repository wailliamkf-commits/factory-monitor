import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from factory_monitor.config import default_config
from factory_monitor.runtime import RuntimeController, apply_camera_heartbeats, check_source_heartbeat, map_live_views


def _wait_for(controller: RuntimeController, event_type: str, timeout: float = 8) -> list[dict]:
    deadline = time.monotonic() + timeout
    seen: list[dict] = []
    while time.monotonic() < deadline:
        batch = controller.poll()
        seen.extend(batch)
        if any(item["type"] == event_type for item in seen):
            return seen
        time.sleep(0.02)
    return seen


def test_demo_runtime_is_multiprocess_synthetic_and_stops_idempotently(tmp_path: Path):
    config = default_config()
    config["review"]["enabled"] = False
    config["detection"]["model_path"] = str(tmp_path / "deliberately-missing.pt")
    controller = RuntimeController(config, tmp_path / "runtime")

    controller.start("demo")
    events = _wait_for(controller, "frame")

    assert any(event["type"] == "status" and event.get("synthetic") is True for event in events)
    frame = next(event for event in events if event["type"] == "frame")
    assert frame["synthetic"] is True
    assert frame["camera_id"].startswith("CAM")
    assert controller.status()["running"] is True
    assert controller.status()["field_verified"] is False
    assert controller.status()["workers"]["capture"]["pid"] != controller.status()["controller_pid"]

    controller.stop()
    controller.stop()
    assert controller.status()["running"] is False
    assert (tmp_path / "runtime" / "events.sqlite3").is_file()


def test_video_runtime_surfaces_missing_detector_and_never_uses_demo_facts(tmp_path: Path):
    video = tmp_path / "fixture.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    for _ in range(15):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()
    config = default_config()
    config["source"]["calibrated"] = True
    config["source"]["expected_size"] = [64, 48]
    config["source"]["window_title"] = "SYNTHETIC VIDEO FIXTURE"
    config["review"]["enabled"] = False
    config["detection"]["model_path"] = str(tmp_path / "missing.pt")
    controller = RuntimeController(config, tmp_path / "runtime")

    controller.start("video", str(video))
    events = _wait_for(controller, "error")
    controller.stop()

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert any("model file" in event["message"] for event in errors)
    assert not any(event.get("synthetic") is True for event in events)


def test_live_runtime_refuses_uncalibrated_source_before_start(tmp_path: Path):
    controller = RuntimeController(default_config(), tmp_path)

    try:
        controller.start("live")
    except ValueError as exc:
        assert "calibrated" in str(exc)
    else:
        raise AssertionError("uncalibrated live capture started")

    assert controller.status()["running"] is False


def test_live_runtime_refuses_calibration_without_expected_dimensions(tmp_path: Path):
    config = default_config()
    config["source"]["calibrated"] = True
    config["source"]["window_title"] = "Exact test window"
    try:
        RuntimeController(config, tmp_path)
    except ValueError as exc:
        assert "expected_size" in str(exc)
    else:
        raise AssertionError("dimensionless live calibration was accepted")


def test_poll_is_nonblocking_while_stopped(tmp_path: Path):
    controller = RuntimeController(default_config(), tmp_path)

    started = time.monotonic()
    assert controller.poll() == []

    assert time.monotonic() - started < 0.05


def test_runtime_directory_lock_follows_start_stop_lifecycle(tmp_path: Path):
    first = RuntimeController(default_config(), tmp_path)
    second = RuntimeController(default_config(), tmp_path)
    first.start("demo")

    with pytest.raises(RuntimeError, match="already owned"):
        second.start("demo")

    first.stop()
    second.start("demo")
    second.stop()
    first.start("demo")
    first.stop()


def test_terminal_analysis_triggers_retention_without_new_evidence(tmp_path: Path):
    config = default_config()
    config["evidence"]["retain_completed"] = 20
    controller = RuntimeController(config, tmp_path)
    for index in range(21):
        event_id = f"event-{index:02d}"
        controller._store.create_event(
            {
                "id": event_id,
                "camera_id": "CAM01",
                "kind": "material_candidate",
                "triggered_at": float(index),
                "status": "complete",
                "reason": "test",
                "layout_version": 1,
                "recording_status": "complete",
                "analysis_status": "pending",
                "completed_at": float(index + 1),
            }
        )
        controller._review_deadlines[event_id] = time.monotonic() + 10
        controller._handle_message(
            {
                "_kind": "analysis",
                "event_id": event_id,
                "analysis_status": "uncertain",
                "analysis": {"reason": "manual review required"},
                "latency_ms": 1.0,
            }
        )

    assert len(controller._store.list_events(limit=100)) == 20


def test_restart_terminalizes_orphaned_pending_reviews_and_applies_retention(tmp_path: Path):
    config = default_config()
    config["review"]["enabled"] = False
    config["evidence"]["retain_completed"] = 20
    first = RuntimeController(config, tmp_path)
    first.start("demo")
    for index in range(21):
        event_id = f"orphan-{index:02d}"
        first._store.create_event(
            {
                "id": event_id,
                "camera_id": "CAM01",
                "kind": "material_candidate",
                "triggered_at": float(index),
                "status": "complete",
                "reason": "test",
                "layout_version": 1,
                "recording_status": "complete",
                "analysis_status": "pending",
                "completed_at": float(index + 1),
                "evidence_path": str(tmp_path / "evidence" / event_id / "evidence.mp4"),
            }
        )
    first.stop()

    restarted = RuntimeController(config, tmp_path)
    restarted.start("demo")
    retained = restarted._store.list_events(limit=100)
    restarted.stop()

    assert len(retained) == 20
    assert all(event["analysis_status"] == "uncertain" for event in retained)
    assert all("restart" in event["analysis"]["reason"] for event in retained)


def test_start_failure_releases_runtime_directory_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = default_config()
    config["source"].update({"calibrated": True, "expected_size": [640, 480], "window_title": "TEST WINDOW"})
    failed = RuntimeController(config, tmp_path)

    def reject_native_setup():
        raise RuntimeError("native setup failed")

    monkeypatch.setattr(failed, "_build_view_controller", reject_native_setup)
    with pytest.raises(RuntimeError, match="native setup failed"):
        failed.start("live")

    replacement = RuntimeController(default_config(), tmp_path)
    replacement.start("demo")
    replacement.stop()


def test_video_replay_uses_actual_yolo_and_reports_warmup(tmp_path: Path):
    project = Path(__file__).resolve().parents[1]
    model = project / "models" / "yolo11n.pt"
    sample = cv2.imread(str(project / "artifacts" / "public-bus.jpg"))
    if not model.is_file() or sample is None:
        pytest.skip("local public YOLO smoke assets are not installed")
    video = tmp_path / "public-bus.avi"
    height, width = sample.shape[:2]
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 2.0, (width, height))
    for _ in range(8):
        writer.write(sample)
    writer.release()
    config = default_config()
    config["source"].update({"calibrated": True, "expected_size": [width, height], "window_title": "PUBLIC REPLAY FIXTURE"})
    config["cameras"] = [config["cameras"][0]]
    config["cameras"][0]["crop"] = [0, 0, 1, 1]
    config["review"]["enabled"] = False
    config["detection"]["model_path"] = str(model)
    try:
        import torch

        config["detection"]["device"] = "mps" if torch.backends.mps.is_available() else "cpu"
    except ImportError:
        config["detection"]["device"] = "cpu"
    controller = RuntimeController(config, tmp_path / "runtime-yolo")

    controller.start("video", str(video))
    events = _wait_for(controller, "frame", timeout=30)
    controller.stop()

    states = [event.get("state") for event in events if event["type"] == "status" and event.get("worker") == "detection"]
    frame = next(event for event in events if event["type"] == "frame")
    assert "warming" in states
    assert len(frame["people"]) >= 3
    assert frame["synthetic"] is False


def test_synthetic_demo_runs_candidate_through_independent_evidence_process(tmp_path: Path):
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["cameras"][0]["exit_line"] = [[0.1, 0], [0.1, 1]]
    config["review"]["enabled"] = False
    config["evidence"].update({"pre_seconds": 1, "post_seconds": 1, "preview_seconds": 1})
    controller = RuntimeController(config, tmp_path / "runtime-evidence")

    controller.start("demo")
    deadline = time.monotonic() + 10
    events: list[dict] = []
    completed = None
    while time.monotonic() < deadline and completed is None:
        events.extend(controller.poll())
        completed = next(
            (
                event["event"]
                for event in events
                if event["type"] == "event_updated" and event["event"].get("recording_status") == "complete"
            ),
            None,
        )
        time.sleep(0.02)
    controller.stop()

    assert any(event["type"] == "candidate" for event in events)
    assert completed is not None
    assert Path(completed["evidence_path"]).is_file()
    assert Path(completed["preview_path"]).is_file()


def test_verified_detail_view_maps_full_frame_only_to_selected_camera(tmp_path: Path):
    cam01 = np.zeros((40, 140, 3), dtype=np.uint8)
    cam02 = np.zeros((40, 140, 3), dtype=np.uint8)
    cv2.putText(cam01, "CAM01", (4, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.putText(cam02, "CAM02", (4, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    path01, path02 = tmp_path / "detail-CAM01.png", tmp_path / "detail-CAM02.png"
    cv2.imwrite(str(path01), cam01)
    cv2.imwrite(str(path02), cam02)
    cameras = [
        {"id": "CAM01", "enabled": True, "crop": [0, 0, 0.5, 1]},
        {"id": "CAM02", "enabled": True, "crop": [0.5, 0, 0.5, 1]},
    ]
    verification = {
        "detail_identities": {
            "CAM01": {"roi": [0, 0, 1, 1], "template_path": str(path01), "layout_roi": [0, 0, 1, 1], "layout_template_path": str(path01), "min_margin": 0.01},
            "CAM02": {"roi": [0, 0, 1, 1], "template_path": str(path02), "layout_roi": [0, 0, 1, 1], "layout_template_path": str(path02), "min_margin": 0.01},
        }
    }

    mapped, reason = map_live_views(cam02, 1, cameras, (140, 40), verification)

    assert reason == "camera CAM02 identity and detail layout verified"
    assert mapped["CAM01"][0] == "blind" and mapped["CAM01"][1] is None
    assert mapped["CAM02"][0] == "detail"
    assert mapped["CAM02"][1].shape == cam02.shape


def test_source_heartbeat_requires_observed_change_and_expires():
    state: dict = {}
    config = {"roi": [0, 0, 1, 1], "min_pixel_delta": 1.0, "max_unchanged_seconds": 2}
    first = np.zeros((20, 20, 3), dtype=np.uint8)
    changed = first.copy()
    changed[:, :10] = 10

    assert check_source_heartbeat(first, config, state, 10.0)[0] is False
    assert check_source_heartbeat(changed, config, state, 11.0)[0] is True
    assert check_source_heartbeat(changed, config, state, 12.0)[0] is True
    fresh, reason = check_source_heartbeat(changed, config, state, 14.1)
    assert fresh is False
    assert "suspected stale" in reason


def test_camera_heartbeats_fail_independently():
    cameras = [{"id": "CAM01", "enabled": True}, {"id": "CAM02", "enabled": True}]
    config = {
        "camera_heartbeats": {
            "CAM01": {"grid_roi": [0, 0, 1, 1], "detail_roi": [0, 0, 1, 1], "min_pixel_delta": 1, "max_unchanged_seconds": 2},
            "CAM02": {"grid_roi": [0, 0, 1, 1], "detail_roi": [0, 0, 1, 1], "min_pixel_delta": 1, "max_unchanged_seconds": 2},
        }
    }
    black = np.zeros((20, 20, 3), dtype=np.uint8)
    changed = np.full((20, 20, 3), 10, dtype=np.uint8)
    states: dict = {}
    first = {"CAM01": ("observable", black), "CAM02": ("observable", black)}
    apply_camera_heartbeats(first, cameras, config, states, 10.0)
    second = {"CAM01": ("observable", changed), "CAM02": ("observable", black)}

    mapped, reasons = apply_camera_heartbeats(second, cameras, config, states, 11.0)

    assert mapped["CAM01"][0] == "observable"
    assert mapped["CAM02"][0] == "unavailable"
    assert "not yet shown" in reasons["CAM02"]
