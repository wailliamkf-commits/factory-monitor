import json
import multiprocessing as mp
import queue
import threading
import time
from pathlib import Path

import numpy as np
import psutil
import pytest

import factory_monitor.runtime as runtime_module
from factory_monitor.config import default_config
from factory_monitor.runtime import RuntimeController, _evidence_process
from factory_monitor.store import EventStore


def _wait_for_recording(controller: RuntimeController, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in controller.poll():
            if message["type"] != "event_updated":
                continue
            event = message["event"]
            if event.get("recording_status") == "recording":
                return event
        time.sleep(0.02)
    raise AssertionError("synthetic event never reached recording state")


def _messages_until_stopped(messages: mp.Queue, timeout: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    seen: list[dict] = []
    while time.monotonic() < deadline:
        try:
            message = messages.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
        except queue.Empty:
            continue
        seen.append(message)
        if (
            message.get("_kind") == "worker"
            and message.get("worker") == "evidence"
            and message.get("state") in {"drained", "stopped"}
        ):
            return seen
    return seen


def _message_until(messages: mp.Queue, predicate, timeout: float = 5.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = messages.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if predicate(message):
            return message
    return None


def test_evidence_worker_drains_a_preaccepted_command_and_frame_after_stop_is_requested(tmp_path: Path):
    """Stop must not make the evidence worker skip work already accepted by its queues."""
    if psutil.virtual_memory().available < 2 * 1024**3:
        pytest.skip("Resource guard: release 2 GiB before this bounded synthetic test")
    context = mp.get_context("spawn")
    frames = context.Queue(maxsize=4)
    commands = context.Queue(maxsize=4)
    messages = context.Queue(maxsize=20)
    stop_event = context.Event()
    view_state = context.Value("i", -1)
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["evidence"].update({"pre_seconds": 1, "post_seconds": 60, "preview_seconds": 1})
    event = {
        "id": "accepted-before-stop",
        "camera_id": config["cameras"][0]["id"],
        "kind": "material_candidate",
        "triggered_at": 100.0,
        "status": "recording",
        "reason": "synthetic regression fixture",
        "layout_version": 1,
        "recording_status": "recording",
    }
    packet = {
        "image": np.zeros((48, 64, 3), dtype=np.uint8),
        "timestamp": 100.0,
        "monotonic": 1.0,
        "synthetic": True,
    }
    process = context.Process(
        target=_evidence_process,
        args=(config, str(tmp_path / "evidence"), frames, commands, messages, stop_event, view_state, "demo"),
    )
    queues = (frames, commands, messages)

    try:
        commands.put({"action": "start", "event": event})
        frames.put(packet)
        stop_event.set()
        process.start()
        # Explicit stream ends make the intended post-fix drain bounded. The
        # baseline worker exits on stop_event before it can observe either one.
        frames.put({"_protocol": "eos", "stream": "evidence_frames"})
        commands.put({"_protocol": "eos", "stream": "evidence_commands"})

        seen = _messages_until_stopped(messages)
        process.join(timeout=2)

        assert not process.is_alive(), "evidence worker did not acknowledge a bounded stop"
        assert process.exitcode == 0
        assert any(item.get("state") == "drained" for item in seen)
        started = next(item for item in seen if item.get("_kind") == "evidence_started")
        assert started["event_id"] == event["id"] and started["ok"] is True
        final = next(
            item
            for item in seen
            if item.get("_kind") == "evidence_update" and item.get("event_id") == event["id"]
            and item.get("recording_status") == "incomplete"
        )
        assert "stop" in final["reason"].lower()
        manifest_path = tmp_path / "evidence" / event["id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["recording_status"] == "incomplete"
        assert manifest["frame_count"] >= 1
    finally:
        stop_event.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        for item in queues:
            item.cancel_join_thread()
            item.close()


def test_evidence_worker_accepts_the_last_command_after_its_frame_stream_ends(tmp_path: Path):
    """A final detection may enqueue its command after capture has closed evidence frames."""
    context = mp.get_context("spawn")
    frames = context.Queue(maxsize=4)
    commands = context.Queue(maxsize=4)
    messages = context.Queue(maxsize=20)
    stop_event = context.Event()
    view_state = context.Value("i", -1)
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["evidence"].update({"pre_seconds": 1, "post_seconds": 60, "preview_seconds": 1})
    event = {
        "id": "accepted-after-frame-eos",
        "camera_id": config["cameras"][0]["id"],
        "kind": "material_candidate",
        "triggered_at": 200.0,
        "status": "recording",
        "reason": "synthetic late-command regression fixture",
        "layout_version": 1,
        "recording_status": "recording",
    }
    process = context.Process(
        target=_evidence_process,
        args=(config, str(tmp_path / "evidence"), frames, commands, messages, stop_event, view_state, "demo"),
    )
    queues = (frames, commands, messages)

    try:
        frames.put(
            {
                "image": np.zeros((48, 64, 3), dtype=np.uint8),
                "timestamp": 200.0,
                "monotonic": 1.0,
                "synthetic": True,
            }
        )
        frames.put({"_protocol": "eos", "stream": "evidence_frames"})
        stop_event.set()
        process.start()
        running_message = messages.get(timeout=3)
        running = [running_message]
        assert running_message.get("_kind") == "worker" and running_message.get("state") == "running"
        frame_ack = _message_until(
            messages,
            lambda item: item.get("_kind") == "shutdown_ack" and item.get("stream") == "evidence_frames",
        )
        assert frame_ack is not None
        assert process.is_alive(), "worker closed before the still-open command stream"

        commands.put({"action": "start", "event": event})
        commands.put({"_protocol": "eos", "stream": "evidence_commands"})
        seen = [*running, *_messages_until_stopped(messages)]
        process.join(timeout=2)

        assert not process.is_alive() and process.exitcode == 0
        assert any(item.get("state") == "drained" for item in seen)
        assert any(item.get("_kind") == "evidence_started" and item.get("event_id") == event["id"] for item in seen)
        manifest = json.loads((tmp_path / "evidence" / event["id"] / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["recording_status"] == "incomplete"
        assert manifest["frame_count"] >= 1
    finally:
        stop_event.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        for item in queues:
            item.cancel_join_thread()
            item.close()


def _accepted_event(event_id: str, camera_id: str, triggered_at: float) -> dict:
    return {
        "id": event_id,
        "camera_id": camera_id,
        "kind": "material_candidate",
        "triggered_at": triggered_at,
        "status": "candidate",
        "reason": "synthetic stop-protocol regression fixture",
        "layout_version": 1,
    }


def test_controller_stop_drains_an_immediately_accepted_command_and_persists_its_report(tmp_path: Path):
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["review"]["enabled"] = False
    config["evidence"].update({"pre_seconds": 1, "post_seconds": 60, "preview_seconds": 1})
    data_dir = tmp_path / "runtime-pending-command"
    controller = RuntimeController(config, data_dir)
    event = _accepted_event("controller-pending", config["cameras"][0]["id"], time.time())

    controller.start("demo")
    controller._accept_candidate(event, [])
    controller.stop()

    persisted = controller._store.get_event(event["id"])
    assert persisted is not None and persisted["recording_status"] == "incomplete"
    manifest_path = data_dir / "evidence" / event["id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["recording_status"] == "incomplete"
    assert "stop" in manifest["reason"].lower()
    report = controller.status()["last_stop"]
    assert report["confirmed"] is True
    assert report["forced_workers"] == [] and report["alive_workers"] == []
    assert report["affected_event_ids"] == []
    assert Path(report["report_path"]).is_file()
    assert all(worker["alive"] is False for worker in controller.status()["workers"].values())


class _NeverAcknowledge:
    def set(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> bool:
        return False

    def is_set(self) -> bool:
        return False


class _SetOnlyEvent:
    def __init__(self) -> None:
        self.was_set = False

    def set(self) -> None:
        self.was_set = True


class _StopFlag:
    def set(self) -> None:
        pass


class _UnkillableProcess:
    name = "factory-evidence"
    exitcode = None

    def join(self, timeout: float | None = None) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    def terminate(self) -> None:
        pass


class _AcceptingQueue:
    def put(self, value, timeout: float | None = None) -> None:
        pass


def test_controller_stop_discloses_ack_timeout_per_event_and_exits_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime_module, "_STOP_TOTAL_SECONDS", 1.5)
    monkeypatch.setattr(runtime_module, "_STOP_DETECTION_ACK_SECONDS", 0.1)
    monkeypatch.setattr(runtime_module, "_STOP_EVIDENCE_SECONDS", 0.2)
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["review"]["enabled"] = False
    config["evidence"].update({"pre_seconds": 1, "post_seconds": 60, "preview_seconds": 1})
    data_dir = tmp_path / "runtime-ack-timeout"
    controller = RuntimeController(config, data_dir)
    event = _accepted_event("controller-timeout", config["cameras"][0]["id"], time.time())

    controller.start("demo")
    controller._accept_candidate(event, [])
    controller._worker_stopped_events["detection"] = _NeverAcknowledge()
    controller._worker_drained_events["detection"] = _NeverAcknowledge()
    controller.stop()

    report = controller.status()["last_stop"]
    persisted = controller._store.get_event(event["id"])
    manifest = json.loads((data_dir / "evidence" / event["id"] / "manifest.json").read_text(encoding="utf-8"))
    assert report["confirmed"] is False
    assert report["alive_workers"] == []
    assert event["id"] in report["affected_event_ids"]
    assert any("acknowledge" in reason for reason in report["reasons"])
    assert persisted is not None and persisted["recording_status"] == "incomplete"
    assert "shutdown protocol incomplete" in persisted["reason"]
    assert manifest["recording_status"] == "incomplete"
    assert manifest["reason"] == persisted["reason"]
    assert all(worker["alive"] is False for worker in controller.status()["workers"].values())


def test_message_drain_cannot_ack_detection_before_the_prior_observation_handler_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only one consumer may remove and fully handle runtime messages at a time."""
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["review"]["enabled"] = False
    controller = RuntimeController(config, tmp_path / "runtime-handler-barrier")
    handler_entered = threading.Event()
    release_handler = threading.Event()
    candidate = _accepted_event("handler-gated", config["cameras"][0]["id"], time.time())

    def gated_observe(observation: dict) -> list[dict]:
        handler_entered.set()
        assert release_handler.wait(timeout=5), "test did not release the synthetic handler gate"
        return [candidate]

    monkeypatch.setattr(controller._rules, "observe", gated_observe)
    controller._source = "demo"
    controller._messages = queue.Queue()
    controller._evidence_commands = queue.Queue()
    controller._worker_stopped_events = {"detection": threading.Event()}
    controller._worker_drained_events = {"detection": threading.Event()}
    controller._messages.put(
        {
            "_kind": "observation",
            "observation": {
                "camera_id": config["cameras"][0]["id"],
                "timestamp": candidate["triggered_at"],
                "health": "observable",
                "layout_version": 1,
                "people": [],
            },
            "image": np.zeros((48, 64, 3), dtype=np.uint8),
            "synthetic": True,
            "health_reason": None,
        }
    )
    controller._messages.put(
        {"_kind": "worker", "worker": "detection", "state": "drained", "drained": True, "pid": 12345}
    )

    first_consumer = threading.Thread(target=controller._drain_messages, daemon=True)
    second_consumer = threading.Thread(target=controller._drain_messages, daemon=True)
    first_consumer.start()
    assert handler_entered.wait(timeout=3)
    second_consumer.start()
    try:
        assert not controller._worker_drained_events["detection"].wait(timeout=0.2)
    finally:
        release_handler.set()
        first_consumer.join(timeout=2)
        second_consumer.join(timeout=2)

    command = controller._evidence_commands.get(timeout=1)
    assert command["action"] == "start" and command["event"]["id"] == candidate["id"]
    assert controller._worker_drained_events["detection"].is_set()


def test_abnormal_worker_stop_is_not_a_drain_acknowledgement(tmp_path: Path):
    controller = RuntimeController(default_config(), tmp_path)
    terminal = _SetOnlyEvent()
    drained = _SetOnlyEvent()
    controller._worker_stopped_events = {"evidence": terminal}
    controller._worker_drained_events = {"evidence": drained}

    controller._handle_message(
        {"_kind": "worker", "worker": "evidence", "state": "stopped", "pid": 12345, "drained": False}
    )

    assert terminal.was_set is True
    assert drained.was_set is False


def test_alive_evidence_worker_defers_parent_manifest_terminalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runtime_module, "_STOP_TOTAL_SECONDS", 0.05)
    monkeypatch.setattr(runtime_module, "_STOP_EVIDENCE_SECONDS", 0.01)
    controller = RuntimeController(default_config(), tmp_path)
    event = _accepted_event("still-owned-by-worker", "CAM01", time.time())
    event.update({"recording_status": "recording", "analysis_status": "uncertain"})
    controller._store.create_event(event)
    controller._running = True
    controller._source = "demo"
    controller._stop_event = _StopFlag()
    controller._processes = {"evidence": _UnkillableProcess()}
    controller._worker_stopped_events = {"evidence": _NeverAcknowledge()}
    controller._worker_drained_events = {"evidence": _NeverAcknowledge()}
    controller._evidence_commands = _AcceptingQueue()

    controller.stop()

    persisted = controller._store.get_event(event["id"])
    report = controller.status()["last_stop"]
    assert persisted is not None and persisted["recording_status"] == "recording"
    assert not (tmp_path / "evidence" / event["id"] / "manifest.json").exists()
    assert report["confirmed"] is False
    assert report["alive_workers"] == ["evidence"]
    assert report["affected_event_ids"] == [event["id"]]
    assert report["terminalization_deferred"] is True
    assert controller.status()["running"] is True


def test_cleanup_failure_cannot_publish_confirmed_stop_and_keeps_retry_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = RuntimeController(default_config(), tmp_path)
    ownership_sentinel = object()
    controller._running = True
    controller._source = "demo"
    controller._stop_event = _StopFlag()
    controller._processes = {}
    controller._evidence_commands = _AcceptingQueue()
    controller._lock_handle = ownership_sentinel

    def fail_cleanup() -> None:
        raise RuntimeError("synthetic retention cleanup failure")

    monkeypatch.setattr(controller, "_prune_completed", fail_cleanup)

    controller.stop()

    report = controller.status()["last_stop"]
    shutdown_events = [item for item in controller.poll() if item.get("type") == "shutdown"]
    assert report["confirmed"] is False
    assert report["cleanup_succeeded"] is False
    assert report["ownership_released"] is False
    assert any("synthetic retention cleanup failure" in reason for reason in report["reasons"])
    assert controller.status()["running"] is True
    assert controller._source == "demo"
    assert controller._lock_handle is ownership_sentinel
    assert shutdown_events and all(item["confirmed"] is False for item in shutdown_events)


def test_stop_terminalizes_an_accepted_recording_with_an_explicit_post_window_gap(tmp_path: Path):
    """An accepted event must not remain silently stuck in ``recording`` after stop."""
    if psutil.virtual_memory().available < 2 * 1024**3:
        pytest.skip("Resource guard: release 2 GiB before this bounded synthetic test")
    config = default_config()
    config["cameras"] = [config["cameras"][0]]
    config["cameras"][0]["exit_line"] = [[0.1, 0], [0.1, 1]]
    config["review"]["enabled"] = False
    config["evidence"].update({"pre_seconds": 1, "post_seconds": 60, "preview_seconds": 1})
    data_dir = tmp_path / "runtime-stop"
    controller = RuntimeController(config, data_dir)

    try:
        controller.start("demo")
        accepted = _wait_for_recording(controller)
        started = time.monotonic()
        controller.stop()
        stop_elapsed = time.monotonic() - started
        stop_report = controller.status()["last_stop"]
    finally:
        # Also stop our own workers if candidate creation or an assertion fails.
        controller.stop()

    store = EventStore(data_dir / "events.sqlite3")
    try:
        persisted = store.get_event(accepted["id"])
    finally:
        store.close()

    assert persisted is not None
    assert persisted["recording_status"] == "incomplete"
    assert persisted["status"] != "recording"
    reason = persisted.get("reason", "").lower()
    assert "stop" in reason or "shutdown" in reason
    assert persisted.get("gaps"), "the unavailable requested post-window must be explicit"
    assert stop_elapsed < 6.0, f"public stop blocked for {stop_elapsed:.3f}s"
    assert stop_report["confirmed"] is True
