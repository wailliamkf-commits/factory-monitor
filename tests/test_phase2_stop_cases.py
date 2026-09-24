"""Bounded synthetic stop cases with source-side pixel identities."""

from __future__ import annotations

import json
import functools
import multiprocessing as mp
import queue
import threading
import time
from pathlib import Path

import numpy as np
import psutil
import pytest

from factory_monitor.config import default_config
import factory_monitor.runtime as runtime_module
from factory_monitor.runtime import RuntimeController, _capture_process, _evidence_process
from scripts.phase2_content_oracle import inject_and_record_source_frame, load_source_oracle, verify_media


RUN_ID = "phase2-short-stop"
FRAME_IDS = (40, 41, 42, 43)
CONTROLLER_FRAME_IDS = (0, 1, 2, 3)


def _config() -> dict:
    config = default_config()
    config["detection"]["fps"] = 2
    config["review"]["enabled"] = False
    assert config["evidence"]["pre_seconds"] == 30
    assert config["evidence"]["post_seconds"] == 60
    assert config["evidence"]["max_inflight"] == 10
    return config


def _memory_guard() -> None:
    if psutil.virtual_memory().available < 2 * 1024**3:
        pytest.skip("2 GiB free required before bounded synthetic spawn")


def _event(event_id: str) -> dict:
    return {
        "id": event_id, "camera_id": "CAM01", "kind": "material_candidate",
        "triggered_at": 100.0, "status": "candidate", "reason": "synthetic short stop",
        "layout_version": 1,
    }


def _packet(frame_seq: int, oracle_path: Path) -> dict:
    image = inject_and_record_source_frame(
        np.zeros((540, 960, 3), dtype=np.uint8),
        run_id=RUN_ID, frame_seq=frame_seq, oracle_path=oracle_path,
    )
    return {
        "image": image, "timestamp": 99.0 + (frame_seq - FRAME_IDS[0]) / 2,
        "monotonic": float(frame_seq), "synthetic": True,
        "run_id": RUN_ID, "frame_seq": frame_seq,
    }


def _gated_worker(ready: mp.Event, release: mp.Event, args: tuple) -> None:
    ready.set()
    if not release.wait(15):
        raise RuntimeError("synthetic consumer release was not signalled")
    _evidence_process(*args)


class _FourFrameSource:
    """Child-local synthetic source; the fourth offer precedes `source_done`."""

    def __init__(self, start_gate: mp.Event, source_done: mp.Event,
                 stop_event: mp.Event, oracle_path: Path, run_id: str) -> None:
        self.start_gate = start_gate
        self.source_done = source_done
        self.stop_event = stop_event
        self.oracle_path = oracle_path
        self.run_id = run_id
        self.index = 0

    def read(self) -> dict:
        while not self.start_gate.wait(0.05):
            if self.stop_event.is_set():
                raise StopIteration
        if self.index == len(CONTROLLER_FRAME_IDS):
            self.source_done.set()
            raise StopIteration
        frame_id = CONTROLLER_FRAME_IDS[self.index]
        self.index += 1
        image = inject_and_record_source_frame(
            np.zeros((540, 960, 3), dtype=np.uint8),
            run_id=self.run_id, frame_seq=frame_id, oracle_path=self.oracle_path,
        )
        return {"image": image, "timestamp": 99.0 + frame_id / 2,
                "monotonic": float(frame_id), "source": "demo", "synthetic": True,
                "demo_people": {camera["id"]: [] for camera in default_config()["cameras"]}}

    def close(self) -> None:
        return None


def _four_frame_capture(start_gate: mp.Event, source_done: mp.Event, oracle_path: Path, *args) -> None:
    # The replacement is installed inside the spawned child only.
    runtime_module.open_capture = lambda _source, _config, _path: _FourFrameSource(
        start_gate, source_done, args[7], oracle_path, args[9]
    )
    _capture_process(*args)


def _paused_controller_evidence(ready: mp.Event, release: mp.Event, *args) -> None:
    ready.set()
    if not release.wait(15):
        raise RuntimeError("synthetic evidence release was not signalled")
    _evidence_process(*args)


def _collect_until(messages: mp.Queue, predicate, timeout: float = 15) -> list[dict]:
    deadline = time.monotonic() + timeout
    seen: list[dict] = []
    while time.monotonic() < deadline:
        try:
            item = messages.get(timeout=min(0.2, max(0.0, deadline - time.monotonic())))
        except queue.Empty:
            continue
        seen.append(item)
        if predicate(item):
            break
    return seen


def _assert_media_and_report(case_dir: Path, seen: list[dict], event_id: str) -> dict:
    manifest_path = case_dir / "evidence" / event_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = load_source_oracle(case_dir / "source-oracle.jsonl")
    frame_paths = [manifest_path.parent / "frames" / item["file"] for item in manifest["source_frames"]]
    verified = verify_media(frame_paths, source, camera_id="CAM01")
    assert verified.frame_sequences == FRAME_IDS
    assert manifest["frame_count"] == 4
    assert manifest["recording_status"] == "incomplete"  # Deliberately stop before 60-second post window.
    assert manifest["window_complete"] is False
    assert [(item["run_id"], item["frame_id"], item["camera_id"]) for item in manifest["source_frames"]] == [
        (RUN_ID, frame_id, "CAM01") for frame_id in FRAME_IDS
    ]
    audit = next(item for item in seen if item.get("_kind") == "queue_audit" and item.get("worker") == "evidence")
    frames = audit["streams"]["evidence_frames"]
    commands = audit["streams"]["evidence_commands"]
    assert audit["complete"] is True
    assert frames["processed"] == 4
    assert [entry["item_id"] for entry in frames["trace"] if entry["transition"] == "processed"] == list(FRAME_IDS)
    assert commands["processed"] == 1 and commands["started"] == 1
    assert any(item.get("_kind") == "evidence_started" and item.get("event_id") == event_id and item["ok"] for item in seen)
    assert any(item.get("_kind") == "evidence_update" and item.get("event_id") == event_id
               and item.get("recording_status") == "incomplete" for item in seen)
    report = {
        "case": event_id, "source_frame_ids": list(FRAME_IDS),
        "accepted_frame_ids": list(FRAME_IDS), "processed_frame_ids": list(verified.frame_sequences),
        "attempted": 4, "accepted": 4, "rejected": 0, "evicted": 0,
        "consumed": frames["processed"], "remaining": 0, "shutdown_discarded": 0,
        "command": {"attempted": 1, "accepted": 1, "processed": commands["processed"], "started": commands["started"]},
        "manifest": manifest, "worker_audit": audit,
        "stop": None, "database": None,
        "boundary": "direct evidence worker; no RuntimeController stop report or DB claim",
    }
    (case_dir / "case-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"CASE_ARTIFACT={case_dir / 'case-result.json'}")
    return report


@pytest.mark.parametrize("late_command", [False, True], ids=["q1-gated-backlog", "q3-after-frame-eos-ack"])
def test_worker_drains_original_capacity_backlog_and_late_command(
    tmp_path: Path, late_command: bool,
) -> None:
    _memory_guard()
    config = _config()
    case_dir = tmp_path / ("q3" if late_command else "q1")
    case_dir.mkdir()
    context = mp.get_context("spawn")
    frames = context.Queue(maxsize=10)  # Original evidence queue at 2 fps.
    commands = context.Queue(maxsize=20)  # Original max(4, max_inflight * 2).
    messages = context.Queue(maxsize=100)
    stop_event = context.Event()
    ready, release = context.Event(), context.Event()
    view_state = context.Value("i", -1)
    event = _event("q3-late-command" if late_command else "q1-backlog")
    args = (config, str(case_dir / "evidence"), frames, commands, messages,
            stop_event, view_state, "demo", str(case_dir / "evidence-audit.jsonl"), RUN_ID)
    process = context.Process(target=_gated_worker, args=(ready, release, args))
    queues = (frames, commands, messages)

    try:
        process.start()
        assert ready.wait(5), "spawned consumer did not reach gate"
        if not late_command:
            commands.put({"action": "start", "event": event}, timeout=2)
        for frame_id in FRAME_IDS:
            frames.put(_packet(frame_id, case_dir / "source-oracle.jsonl"), timeout=2)
        frames.put({"_protocol": "eos", "stream": "evidence_frames"}, timeout=2)
        stop_event.set()
        release.set()
        seen: list[dict] = []
        if late_command:
            seen = _collect_until(messages, lambda item: item.get("_kind") == "shutdown_ack"
                                  and item.get("stream") == "evidence_frames")
            assert any(item.get("_kind") == "shutdown_ack" and item.get("stream") == "evidence_frames" for item in seen)
            assert process.is_alive(), "command stream closed before late start"
            commands.put({"action": "start", "event": event}, timeout=2)
        commands.put({"_protocol": "eos", "stream": "evidence_commands"}, timeout=2)
        seen.extend(_collect_until(messages, lambda item: item.get("_kind") == "worker"
                                   and item.get("worker") == "evidence" and item.get("state") in {"drained", "stopped"}))
        process.join(3)
        assert not process.is_alive() and process.exitcode == 0
        assert any(item.get("_kind") == "worker" and item.get("state") == "drained" for item in seen)
        _assert_media_and_report(case_dir, seen, event["id"])
    finally:
        release.set()
        stop_event.set()
        if process.is_alive():
            process.terminate()
            process.join(3)
        for item in queues:
            item.cancel_join_thread()
            item.close()


def _controller_result(case_dir: Path, controller: RuntimeController, event_id: str) -> dict:
    report = controller.status()["last_stop"]
    row = controller._store.get_event(event_id)
    manifest_path = case_dir / "evidence" / event_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    result = {"case": event_id, "stop": report, "database": row, "manifest": manifest,
              "oracle": None, "boundary": "controller demo source; no independent pixel oracle for this command case"}
    (case_dir / "case-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"CASE_ARTIFACT={case_dir / 'case-result.json'}")
    return result


def _command_outcome(case: dict, event_id: str) -> str:
    stream = case["stop"]["queue_audit"]["streams"]["evidence_commands"]
    assert stream["state"] == "reconciled", stream
    assert stream["attempted"] == stream["accepted"] + stream["queue_rejected"]
    assert stream["accepted"] == stream["consumed"] + stream["evicted"] + stream["remaining"] + stream["shutdown_discarded"]
    return stream["command_outcomes"][event_id]


def test_q2_controller_immediate_command_has_durable_ack_and_db_manifest(tmp_path: Path) -> None:
    _memory_guard()
    config = _config()
    case_dir = tmp_path / "q2"
    controller = RuntimeController(config, case_dir)
    event = {**_event("q2-immediate-command"), "triggered_at": time.time()}
    try:
        controller.start("demo")
        controller._accept_candidate(event, [])
        controller.stop()
        case = _controller_result(case_dir, controller, event["id"])
    finally:
        controller.stop()

    assert case["stop"]["confirmed"] is True
    assert _command_outcome(case, event["id"]) == "ack_started"
    assert case["database"]["recording_status"] == "incomplete"
    assert case["manifest"]["recording_status"] == "incomplete"
    assert case["manifest"]["event_id"] == event["id"]
    assert case["database"]["window_complete"] is False


def test_q5_controller_virtual_quota_rejects_command_with_durable_ack(tmp_path: Path) -> None:
    _memory_guard()
    config = _config()
    config["evidence"]["max_disk_mb"] = 1  # Test-only quota; production threshold is unchanged.
    case_dir = tmp_path / "q5"
    evidence_root = case_dir / "evidence"
    evidence_root.mkdir(parents=True)
    (evidence_root / "synthetic-quota-fill.bin").write_bytes(b"S" * (1024 * 1024))
    controller = RuntimeController(config, case_dir)
    event = {**_event("q5-quota-rejection"), "triggered_at": time.time()}
    try:
        controller.start("demo")
        controller._accept_candidate(event, [])
        controller.stop()
        case = _controller_result(case_dir, controller, event["id"])
    finally:
        controller.stop()

    assert _command_outcome(case, event["id"]) == "ack_rejected"
    assert case["database"]["recording_status"] == "error"
    assert case["database"]["window_complete"] is False
    assert "disk limit" in case["database"]["gaps"][0]
    assert case["manifest"] is None


def test_q1_full_controller_drains_four_accepted_frames_with_exact_media_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture, detection, evidence, database and stop journal share one run."""
    _memory_guard()
    config = _config()
    case_dir = tmp_path / "q1-full-controller"
    context = mp.get_context("spawn")
    start_gate, source_done = context.Event(), context.Event()
    evidence_ready, evidence_release = context.Event(), context.Event()
    oracle_path = case_dir / "source-oracle.jsonl"
    monkeypatch.setattr(runtime_module, "_capture_process", functools.partial(
        _four_frame_capture, start_gate, source_done, oracle_path,
    ))
    monkeypatch.setattr(runtime_module, "_evidence_process", functools.partial(
        _paused_controller_evidence, evidence_ready, evidence_release,
    ))
    controller = RuntimeController(config, case_dir)
    event = _event("q1-full-controller")
    stop_thread: threading.Thread | None = None
    try:
        controller.start("demo")
        assert evidence_ready.wait(5), "evidence process did not reach the consumer gate"
        controller._accept_candidate(event, [])
        assert controller._store.get_event(event["id"]) is not None
        start_gate.set()
        assert source_done.wait(6), "capture did not offer four source frames"
        stop_thread = threading.Thread(target=controller.stop, name="synthetic-q1-stop")
        stop_thread.start()
        assert controller._stop_event is not None and controller._stop_event.wait(3)
        evidence_release.set()
        stop_thread.join(8)
        assert not stop_thread.is_alive(), "controller stop exceeded its bounded join"

        report = controller.status()["last_stop"]
        assert report is not None and report["confirmed"] is True, report
        assert report["queue_audit"]["state"] == "reconciled", report["queue_audit"]
        for channel in ("detection_frames", "evidence_frames", "evidence_commands"):
            stream = report["queue_audit"]["streams"][channel]
            assert stream["state"] == "reconciled", (channel, stream)
            assert stream["identity_mode"] == "exact_durable_records"
            assert stream["attempted"] == stream["accepted"] + stream["queue_rejected"]
            assert stream["accepted"] == (stream["consumed"] + stream["evicted"]
                                           + stream["remaining"] + stream["shutdown_discarded"])
            assert stream["evicted"] == stream["remaining"] == stream["shutdown_discarded"] == 0
        assert report["queue_audit"]["streams"]["evidence_frames"]["accepted"] == 4
        assert report["queue_audit"]["streams"]["detection_frames"]["accepted"] == 4
        for channel in ("detection_frames", "evidence_frames"):
            stream = report["queue_audit"]["streams"][channel]
            assert [item["item_id"] for item in stream["producer"]["trace"]
                    if item["transition"] == "accepted"] == list(CONTROLLER_FRAME_IDS)
            assert [item["item_id"] for item in stream["consumer"]["trace"]
                    if item["transition"] == "processed"] == list(CONTROLLER_FRAME_IDS)
        assert report["queue_audit"]["streams"]["evidence_commands"]["command_outcomes"][event["id"]] == "ack_started"

        manifest_path = case_dir / "evidence" / event["id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        row = controller._store.get_event(event["id"])
        assert row is not None and row["recording_status"] == manifest["recording_status"] == "incomplete"
        assert row["window_complete"] is manifest["window_complete"] is False
        assert manifest["frame_count"] == 4
        assert [(item["run_id"], item["frame_id"], item["camera_id"]) for item in manifest["source_frames"]] == [
            (report["run_id"], frame_id, "CAM01") for frame_id in CONTROLLER_FRAME_IDS
        ]
        media_paths = [manifest_path.parent / "frames" / item["file"] for item in manifest["source_frames"]]
        verified = verify_media(media_paths, load_source_oracle(oracle_path), camera_id="CAM01")
        assert verified.frame_sequences == CONTROLLER_FRAME_IDS

        result = {"case": event["id"], "stop": report, "database": row, "manifest": manifest,
                  "source_frame_ids": list(CONTROLLER_FRAME_IDS),
                  "decoded_frame_ids": list(verified.frame_sequences),
                  "oracle_path": str(oracle_path), "media_paths": [str(path) for path in media_paths],
                  "config": {"fps": 2, "pre_seconds": 30, "post_seconds": 60,
                             "max_inflight": 10, "detection_queue": 3, "evidence_queue": 10}}
        (case_dir / "case-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"CASE_ARTIFACT={case_dir / 'case-result.json'}")
    finally:
        start_gate.set()
        evidence_release.set()
        if stop_thread is not None and stop_thread.is_alive():
            stop_thread.join(8)
        if controller.status()["running"]:
            controller.stop()
