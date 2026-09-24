"""Queue accounting regressions; all images in this module are synthetic."""

from __future__ import annotations

import queue
import json
import multiprocessing as mp
import sqlite3
import time
from pathlib import Path

import numpy as np
import cv2
import psutil
import pytest

from factory_monitor import runtime as runtime_module
from factory_monitor.config import default_config
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore


def _own_process_cleanup(processes: list[mp.Process], queues: list[mp.Queue]) -> None:
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
    for item in queues:
        item.cancel_join_thread()
        item.close()


def _event(event_id: str, trigger: float, camera_id: str = "CAM01") -> dict:
    return {
        "id": event_id, "camera_id": camera_id, "kind": "material_candidate",
        "triggered_at": trigger, "status": "candidate",
        "reason": "synthetic queue audit fixture", "layout_version": 1,
    }


def _one_camera_config() -> dict:
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["review"]["enabled"] = False
    config["evidence"].update({"pre_seconds": 1, "post_seconds": 2, "preview_seconds": 1})
    return config


def _require_memory() -> None:
    if psutil.virtual_memory().available < 2 * 1024**3:
        pytest.skip("R-018 resource guard: 2 GiB free required before spawn")


def _write_case(path: Path, controller: RuntimeController, event_ids: list[str]) -> dict:
    rows = {event_id: controller._store.get_event(event_id) for event_id in event_ids}
    manifests = {}
    for event_id in event_ids:
        target = controller.data_dir / "evidence" / event_id / "manifest.json"
        manifests[event_id] = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else None
    result = {"stop": controller.status()["last_stop"], "rows": rows, "manifests": manifests}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _wait_for_row(controller: RuntimeController, event_id: str, status: str, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = controller._store.get_event(event_id)
        if row is not None and row.get("recording_status") == status:
            return row
        time.sleep(0.05)
    raise AssertionError(f"{event_id} did not reach {status}")


def _decoded_frames(path: Path) -> int:
    media = cv2.VideoCapture(str(path))
    assert media.isOpened(), path
    count = 0
    while True:
        ok, _ = media.read()
        if not ok:
            break
        count += 1
    media.release()
    return count


def test_spawn_normal_stop_reconciles_frames_and_immediate_command(tmp_path: Path):
    _require_memory()
    controller = RuntimeController(_one_camera_config(), tmp_path / "normal")
    event = _event("immediate-command", time.time())
    try:
        controller.start("demo")
        controller._accept_candidate(event, [])
        controller.stop()
        case = _write_case(tmp_path / "normal-case.json", controller, [event["id"]])
    finally:
        controller.stop()
    audit = case["stop"]["queue_audit"]
    assert audit["state"] == "reconciled", audit
    assert all(part["stop_unaccounted"] == 0 for part in audit["streams"].values())
    assert audit["streams"]["evidence_commands"]["accepted"] == 1
    assert audit["streams"]["evidence_commands"]["processed"] == 1
    assert case["rows"][event["id"]]["recording_status"] == "incomplete"
    assert case["manifests"][event["id"]]["recording_status"] == "incomplete"
    assert case["rows"][event["id"]]["window_complete"] is False
    assert case["manifests"][event["id"]]["window_complete"] is False


def test_missing_worker_snapshot_is_unknown_not_zero(tmp_path: Path):
    controller = RuntimeController(_one_camera_config(), tmp_path)
    audit = controller._reconcile_queue_audit()
    assert audit["state"] == "unknown"
    assert all(part["state"] == "unknown" for part in audit["streams"].values())
    assert all("stop_unaccounted" not in part for part in audit["streams"].values())


def test_summary_consistent_stream_cannot_hide_later_mismatch(tmp_path: Path):
    controller = RuntimeController(_one_camera_config(), tmp_path)
    detection_offer, detection_done = {}, {}
    evidence_offer, evidence_done = {}, {}
    runtime_module._count_identity(detection_offer, "attempted", 1)
    runtime_module._count_identity(detection_offer, "accepted", 1)
    runtime_module._count_identity(detection_done, "processed", 1)
    detection_offer["trace_truncated"] = 1
    runtime_module._count_identity(evidence_offer, "attempted", 1)
    runtime_module._count_identity(evidence_offer, "accepted", 1)
    controller._queue_audits = {
        "capture": {"complete": True, "streams": {
            "detection_frames": detection_offer, "evidence_frames": evidence_offer,
        }},
        "detection": {"complete": True, "streams": {"detection_frames": detection_done}},
        "evidence": {"complete": True, "streams": {
            "evidence_frames": evidence_done, "evidence_commands": {},
        }},
    }
    audit = controller._reconcile_queue_audit()
    assert audit["streams"]["detection_frames"]["state"] == "summary_consistent"
    assert audit["streams"]["evidence_frames"]["state"] == "mismatch"
    assert audit["state"] == "mismatch"


def test_existing_event_database_migrates_window_coverage_without_guessing_old_rows(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    payload = _event("legacy-unknown", 100.0)
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE events (
            id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, kind TEXT NOT NULL,
            triggered_at REAL NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
            layout_version INTEGER NOT NULL, analysis_status TEXT, analysis TEXT,
            recording_status TEXT, evidence_path TEXT, preview_path TEXT, gaps TEXT,
            review_label TEXT, completed_at REAL, latency_ms REAL, payload TEXT NOT NULL
        )""")
        connection.execute(
            "INSERT INTO events(id,camera_id,kind,triggered_at,status,reason,layout_version,payload) VALUES(?,?,?,?,?,?,?,?)",
            (payload["id"], payload["camera_id"], payload["kind"], payload["triggered_at"],
             payload["status"], payload["reason"], payload["layout_version"], json.dumps(payload)),
        )
    store = EventStore(database)
    try:
        assert store.get_event(payload["id"]).get("window_complete") is None
        store.update_event(payload["id"], window_complete=False)
        assert store.get_event(payload["id"])["window_complete"] is False
    finally:
        store.close()


def test_spawn_backlog_reconciles_replaced_frames_and_queued_command(tmp_path: Path):
    """Real Windows spawn and mp.Queue feeder behavior, with delayed consumers."""
    _require_memory()
    config = _one_camera_config()
    config["detection"]["fps"] = 5
    data_dir = tmp_path / "backlog"
    controller = RuntimeController(config, data_dir)
    controller._source = "demo"
    event = _event("queued-command", time.time() + 0.5)
    controller._store.create_event({**event, "recording_status": "recording", "analysis_status": "uncertain"})
    runtime_module._count_identity(controller._command_ledger, "attempted", event["id"])
    runtime_module._count_identity(controller._command_ledger, "accepted", event["id"])
    context = mp.get_context("spawn")
    detection_frames = context.Queue(maxsize=3)
    evidence_frames = context.Queue(maxsize=3)
    preview_frames = context.Queue(maxsize=2)
    commands = context.Queue(maxsize=4)
    messages = context.Queue(maxsize=200)
    stop = context.Event()
    view_state = context.Value("i", -1)
    queues = [detection_frames, evidence_frames, preview_frames, commands, messages]
    commands.put({"action": "start", "event": event})
    capture = context.Process(
        target=runtime_module._capture_process,
        args=("demo", config, None, detection_frames, evidence_frames, preview_frames, messages, stop),
    )
    detection = context.Process(
        target=runtime_module._detection_process,
        args=("demo", config, detection_frames, messages, stop, view_state),
    )
    evidence = context.Process(
        target=runtime_module._evidence_process,
        args=(config, str(data_dir / "evidence"), evidence_frames, commands, messages, stop, view_state, "demo"),
    )
    processes = [capture, detection, evidence]
    seen = []
    try:
        capture.start()
        time.sleep(1.5)  # bounded delay creates real frame and command backlog
        detection.start()
        evidence.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                seen.append(messages.get(timeout=0.05))
            except queue.Empty:
                pass
        stop.set()
        capture.join(timeout=3)
        detection.join(timeout=3)
        commands.put({"_protocol": "eos", "stream": "evidence_commands"}, timeout=1)
        evidence.join(timeout=4)
        while True:
            try:
                seen.append(messages.get_nowait())
            except queue.Empty:
                break
        assert all(not process.is_alive() and process.exitcode == 0 for process in processes)
        for item in seen:
            if item.get("_kind") in {"queue_audit", "evidence_started", "evidence_update"}:
                controller._handle_message(dict(item))
        audit = controller._reconcile_queue_audit()
        assert audit["state"] == "reconciled", audit
        assert audit["streams"]["detection_frames"]["attempted"] > 3
        assert audit["streams"]["detection_frames"]["evicted"] + audit["streams"]["detection_frames"]["queue_rejected"] > 0
        assert audit["streams"]["evidence_commands"]["accepted"] == audit["streams"]["evidence_commands"]["processed"] == 1
        manifest = json.loads((data_dir / "evidence" / event["id"] / "manifest.json").read_text(encoding="utf-8"))
        row = controller._store.get_event(event["id"])
        assert row is not None and row["recording_status"] == manifest["recording_status"]
        first_frame = sorted((data_dir / "evidence" / event["id"] / "frames").glob("*.jpg"))[0]
        first_timestamp = float(first_frame.stem.split("-", 1)[1])
        assert abs(manifest["gaps"][0][0] - (event["triggered_at"] - config["evidence"]["pre_seconds"])) < 1e-5
        assert abs(manifest["gaps"][0][1] - first_timestamp) < 1e-5
        assert manifest["window_complete"] is False and row["window_complete"] is False
        (tmp_path / "backlog-case.json").write_text(json.dumps({
            "audit": audit, "event_id": event["id"], "row": row, "manifest": manifest,
            "worker_messages": [item for item in seen if item.get("_kind") in {"worker", "evidence_started", "evidence_update", "stats", "error"}],
            "exitcodes": {name: process.exitcode for name, process in zip(("capture", "detection", "evidence"), processes)},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        stop.set()
        _own_process_cleanup(processes, queues)


def test_spawn_overlapping_recordings_keep_separate_windows_and_audit(tmp_path: Path):
    _require_memory()
    controller = RuntimeController(_one_camera_config(), tmp_path / "overlap")
    first_id, second_id = "overlap-first", "overlap-second"
    try:
        controller.start("demo")
        time.sleep(1.2)  # make an actual pre-event cache before both triggers
        first_at = time.time()
        controller._accept_candidate(_event(first_id, first_at), [])
        _wait_for_row(controller, first_id, "recording")
        time.sleep(0.45)
        second_at = time.time()
        controller._accept_candidate(_event(second_id, second_at), [])
        _wait_for_row(controller, second_id, "recording")
        _wait_for_row(controller, first_id, "complete", timeout=7)
        _wait_for_row(controller, second_id, "complete", timeout=7)
        controller.stop()
        case = _write_case(tmp_path / "overlap-case.json", controller, [first_id, second_id])
        for event_id in (first_id, second_id):
            manifest = case["manifests"][event_id]
            row = case["rows"][event_id]
            assert manifest["recording_status"] == row["recording_status"] == "complete"
            assert manifest["window_complete"] is row["window_complete"] is (not manifest["gaps"])
            assert manifest["event_id"] == event_id
            media_path = Path(row["evidence_path"])
            assert media_path.parent.name == event_id
            assert _decoded_frames(media_path) == manifest["frame_count"]
            timestamps = [float(path.name.split("-", 1)[1][:-4]) for path in sorted((media_path.parent / "frames").glob("*.jpg"))]
            assert len(timestamps) == manifest["frame_count"]
            assert min(timestamps) >= row["triggered_at"] - 1 - 0.25
            assert max(timestamps) <= row["triggered_at"] + 2 + 0.25
        assert case["stop"]["queue_audit"]["state"] == "reconciled"
        assert case["stop"]["queue_audit"]["streams"]["evidence_commands"]["processed"] == 2
    finally:
        controller.stop()


def test_spawn_inflight_capacity_rejects_second_event_without_damaging_first(tmp_path: Path):
    _require_memory()
    config = _one_camera_config()
    config["evidence"].update({"max_inflight": 1, "post_seconds": 60})
    controller = RuntimeController(config, tmp_path / "capacity")
    first_id, second_id = "capacity-first", "capacity-second"
    try:
        controller.start("demo")
        controller._accept_candidate(_event(first_id, time.time()), [])
        _wait_for_row(controller, first_id, "recording")
        controller._accept_candidate(_event(second_id, time.time()), [])
        _wait_for_row(controller, second_id, "error")
        first_before_stop = controller._store.get_event(first_id)
        controller.stop()
        case = _write_case(tmp_path / "capacity-case.json", controller, [first_id, second_id])
        assert first_before_stop is not None and first_before_stop["recording_status"] == "recording"
        assert case["rows"][first_id]["recording_status"] == case["manifests"][first_id]["recording_status"] == "incomplete"
        assert case["rows"][first_id]["window_complete"] is case["manifests"][first_id]["window_complete"] is False
        assert case["rows"][second_id]["recording_status"] == "error"
        assert case["rows"][second_id]["window_complete"] is False
        assert "inflight limit" in case["rows"][second_id]["gaps"][0]
        assert case["manifests"][second_id] is None  # recorder rejected before creating a directory
        command_audit = case["stop"]["queue_audit"]["streams"]["evidence_commands"]
        assert command_audit["state"] == "reconciled"
        assert command_audit["consumer"]["recording_rejected"] == 1
        assert command_audit["consumer"]["started"] == 1
    finally:
        controller.stop()


def test_spawn_forced_evidence_worker_loss_reports_unknown(tmp_path: Path):
    _require_memory()
    config = _one_camera_config()
    config["evidence"]["post_seconds"] = 60
    controller = RuntimeController(config, tmp_path / "forced")
    event_id = "forced-during-recording"
    try:
        controller.start("demo")
        controller._accept_candidate(_event(event_id, time.time()), [])
        _wait_for_row(controller, event_id, "recording")
        worker = controller._processes["evidence"]
        worker_pid = worker.pid
        worker.terminate()  # only this test-owned child process
        worker.join(timeout=2)
        assert not worker.is_alive()
        controller.stop()
        case = _write_case(tmp_path / "forced-case.json", controller, [event_id])
        case["injected_worker_pid"] = worker_pid
        (tmp_path / "forced-case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        assert case["stop"]["confirmed"] is False
        assert case["stop"]["queue_audit"]["state"] == "unknown"
        assert case["stop"]["queue_audit"]["streams"]["evidence_frames"]["state"] == "unknown"
        assert "stop_unaccounted" not in case["stop"]["queue_audit"]["streams"]["evidence_frames"]
        assert case["rows"][event_id]["recording_status"] == "incomplete"
        assert case["manifests"][event_id]["recording_status"] == "incomplete"
        assert case["rows"][event_id]["window_complete"] is case["manifests"][event_id]["window_complete"] is False
    finally:
        controller.stop()


def test_spawn_interrupted_recording_recovers_same_event_on_restart(tmp_path: Path):
    _require_memory()
    config = _one_camera_config()
    config["evidence"]["post_seconds"] = 60
    data_dir = tmp_path / "restart"
    data_dir.mkdir()
    event = _event("restarted-same-event", time.time())
    store = EventStore(data_dir / "events.sqlite3")
    store.create_event({**event, "recording_status": "recording", "analysis_status": "pending"})
    store.close()
    context = mp.get_context("spawn")
    frames = context.Queue(maxsize=4)
    commands = context.Queue(maxsize=4)
    messages = context.Queue(maxsize=20)
    stop = context.Event()
    view_state = context.Value("i", -1)
    process = context.Process(
        target=runtime_module._evidence_process,
        args=(config, str(data_dir / "evidence"), frames, commands, messages, stop, view_state, "demo"),
    )
    queues = [frames, commands, messages]
    before = None
    try:
        commands.put({"action": "start", "event": event})
        frames.put({
            "frame_seq": 0, "image": np.zeros((540, 960, 3), dtype=np.uint8),
            "timestamp": event["triggered_at"], "monotonic": 1.0, "synthetic": True,
        })
        process.start()
        manifest_path = data_dir / "evidence" / event["id"] / "manifest.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if manifest_path.is_file():
                before = json.loads(manifest_path.read_text(encoding="utf-8"))
                if before["frame_count"] >= 1:
                    break
            time.sleep(0.05)
        assert before is not None and before["recording_status"] == "recording" and before["frame_count"] >= 1
        interrupted_pid = process.pid
        process.terminate()  # test-owned writer dies before EOS or final audit
        process.join(timeout=2)
        assert not process.is_alive()
    finally:
        _own_process_cleanup([process], queues)

    restarted = RuntimeController(config, data_dir)
    try:
        restarted.start("demo")
        after_row = restarted._store.get_event(event["id"])
        after_manifest = json.loads((data_dir / "evidence" / event["id"] / "manifest.json").read_text(encoding="utf-8"))
        restarted.stop()
        assert after_row is not None and after_row["recording_status"] == "incomplete"
        assert after_row["window_complete"] is False
        assert after_row["analysis_status"] == "uncertain"
        assert after_manifest["event_id"] == event["id"]
        assert after_manifest["recording_status"] == "incomplete"
        assert after_manifest["window_complete"] is False
        assert "interrupted" in after_manifest["reason"]
        assert after_manifest["frame_count"] == before["frame_count"]
        assert len(list((data_dir / "evidence").glob(event["id"]))) == 1
        (tmp_path / "restart-case.json").write_text(json.dumps({
            "event_id": event["id"], "interrupted_pid": interrupted_pid,
            "writer_exitcode": process.exitcode, "before_manifest": before,
            "after_manifest": after_manifest, "after_row": after_row,
            "interrupted_queue_terminal_state": "unknown; writer terminated without a final audit snapshot",
            "restart_stop": restarted.status()["last_stop"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        restarted.stop()


def test_capture_counts_both_evicted_old_and_rejected_new_frame(monkeypatch):
    """A failed retry after eviction loses two distinct frame identities."""
    packet = {
        "image": np.zeros((2, 2, 3), dtype=np.uint8),
        "timestamp": 1.0,
        "monotonic": 1.0,
        "synthetic": True,
    }

    class Source:
        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            if self.reads > 1:
                raise StopIteration
            return dict(packet)

        def close(self):
            pass

    class RetryRaceQueue:
        def __init__(self):
            self.calls = 0

        def put_nowait(self, value):
            self.calls += 1
            raise queue.Full

        def get_nowait(self):
            return {**packet, "frame_seq": 0}

        def put(self, value, timeout=None):
            pass

    class AcceptQueue:
        def put_nowait(self, value):
            pass

        def put(self, value, timeout=None):
            pass

        def cancel_join_thread(self):
            pass

    class Messages:
        def __init__(self):
            self.items = []

        def put(self, value, timeout=None):
            self.items.append(value)

    class NeverStop:
        def is_set(self):
            return False

        def wait(self, timeout):
            return False

    messages = Messages()
    monkeypatch.setattr(runtime_module, "open_capture", lambda *args: Source())
    runtime_module._capture_process(
        "demo", {"detection": {"fps": 2}}, None,
        RetryRaceQueue(), AcceptQueue(), AcceptQueue(), messages, NeverStop(),
    )
    stats = [item for item in messages.items if item.get("_kind") == "stats"]
    assert stats and stats[-1]["dropped_detection_frames"] == 2
