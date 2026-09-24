"""Synthetic checks of the independent, per-run shutdown ledger."""

from pathlib import Path
import json
import queue
import threading

import pytest

from factory_monitor.config import default_config
from factory_monitor.runtime import RuntimeController
from factory_monitor import runtime as runtime_module


def test_stop_exposes_independent_exact_frame_and_command_ledger(tmp_path: Path):
    config = default_config()
    config["cameras"] = config["cameras"][:1]
    config["review"]["enabled"] = False
    controller = RuntimeController(config, tmp_path / "run")
    try:
        controller.start("demo")
        controller.stop()
        stop = controller.status()["last_stop"]
        assert stop["run_id"]
        assert stop["queue_audit"]["streams"]["evidence_frames"]["identity_mode"] == "exact_durable_records"
    finally:
        controller.stop()


def test_truncated_trace_cannot_certify_different_exact_frame_ids(tmp_path: Path):
    """513 records overflow the old trace; unequal sets collide on its sums."""
    controller = RuntimeController(default_config(), tmp_path)
    offered, consumed = {}, {}
    source_ids = [1, 5, 6, *range(100, 610)]
    wrong_ids = [2, 3, 7, *range(100, 610)]
    for frame_id in source_ids:
        runtime_module._count_identity(offered, "attempted", frame_id)
        runtime_module._count_identity(offered, "accepted", frame_id)
    for frame_id in wrong_ids:
        runtime_module._count_identity(consumed, "processed", frame_id)
    assert offered["trace_truncated"] > 0
    controller._queue_audits = {
        "capture": {"complete": True, "streams": {"detection_frames": offered, "evidence_frames": {}}},
        "detection": {"complete": True, "streams": {"detection_frames": consumed}},
        "evidence": {"complete": True, "streams": {"evidence_frames": {}, "evidence_commands": {}}},
    }
    controller._run_id = "collision-case"
    for worker in ("capture", "detection", "evidence", "controller"):
        path = tmp_path / f"{worker}.jsonl"
        entries = []
        if worker == "capture":
            for frame_id in source_ids:
                for transition in ("attempted", "accepted"):
                    entries.append({"run_id": "collision-case", "worker": worker, "channel": "detection_frames",
                                    "transition": transition, "item_id": frame_id})
        if worker == "detection":
            for frame_id in wrong_ids:
                entries.append({"run_id": "collision-case", "worker": worker, "channel": "detection_frames",
                                "transition": "processed", "item_id": frame_id})
        entries.append({"run_id": "collision-case", "worker": worker, "terminal": True})
        path.write_text("\n".join(json.dumps(item) for item in entries) + "\n", encoding="utf-8")
        controller._audit_paths[worker] = path
    audit = controller._reconcile_queue_audit()
    assert audit["streams"]["detection_frames"]["state"] != "reconciled"
    assert audit["streams"]["detection_frames"]["state"] != "summary_consistent"


def test_dropped_ipc_audit_snapshot_does_not_erase_durable_frame_identity(tmp_path: Path):
    controller = RuntimeController(default_config(), tmp_path)
    controller._run_id = "snapshot-lost"
    for worker in ("capture", "detection", "evidence", "controller"):
        entries = []
        if worker == "capture":
            for channel in ("detection_frames", "evidence_frames"):
                for frame_id in range(513):
                    for transition in ("attempted", "accepted"):
                        entries.append({"run_id": controller._run_id, "worker": worker,
                                        "channel": channel, "transition": transition, "item_id": frame_id})
        elif worker in {"detection", "evidence"}:
            channel = "detection_frames" if worker == "detection" else "evidence_frames"
            for frame_id in range(513):
                entries.append({"run_id": controller._run_id, "worker": worker,
                                "channel": channel, "transition": "processed", "item_id": frame_id})
        entries.append({"run_id": controller._run_id, "worker": worker, "terminal": True})
        path = tmp_path / f"{worker}.jsonl"
        path.write_text("\n".join(json.dumps(item) for item in entries) + "\n", encoding="utf-8")
        controller._audit_paths[worker] = path
    # The bounded message queue delivered no queue_audit snapshots at all.
    audit = controller._reconcile_queue_audit()
    assert audit["state"] == "reconciled"
    for channel in ("detection_frames", "evidence_frames"):
        stream = audit["streams"][channel]
        assert stream["identity_mode"] == "exact_durable_records"
        assert (stream["attempted"], stream["accepted"], stream["consumed"]) == (513, 513, 513)


def test_unsafe_stop_keeps_controller_journal_until_message_handler_finishes(tmp_path: Path):
    controller = RuntimeController(default_config(), tmp_path)
    controller._run_id = "blocked-handler"
    controller._audit_paths = {name: tmp_path / f"{name}.jsonl"
                               for name in ("capture", "detection", "evidence", "controller")}
    for worker in ("capture", "detection", "evidence"):
        entries = []
        if worker == "evidence":
            for transition in ("processed", "recording_rejected"):
                entries.append({"run_id": controller._run_id, "worker": worker,
                                "channel": "evidence_commands", "transition": transition, "item_id": "late-ack"})
        entries.append({"run_id": controller._run_id, "worker": worker, "terminal": True})
        controller._audit_paths[worker].write_text(
            "\n".join(json.dumps(item) for item in entries) + "\n", encoding="utf-8")
    journal = runtime_module._AuditJournal(controller._audit_paths["controller"], controller._run_id, "controller")
    controller._command_journal = journal
    for transition in ("attempted", "accepted"):
        journal.record("evidence_commands", transition, "late-ack")
    controller._store.create_event({
        "id": "late-ack", "camera_id": "CAM01", "kind": "material_candidate",
        "triggered_at": 1.0, "status": "candidate", "reason": "synthetic delayed ACK",
        "layout_version": 1, "recording_status": "recording", "analysis_status": "uncertain",
    })
    controller._running = True
    controller._source = "demo"
    controller._stop_event = threading.Event()
    controller._processes = {}
    controller._evidence_commands = queue.Queue()
    entered, release = threading.Event(), threading.Event()
    errors = []

    def delayed_handler():
        entered.set()
        release.wait()
        try:
            controller._handle_message({"_kind": "evidence_started", "event_id": "late-ack",
                                        "ok": False, "reason": "synthetic rejected"})
        except Exception as exc:
            errors.append(exc)

    handler = threading.Thread(target=delayed_handler)
    controller._pump = handler
    handler.start()
    assert entered.wait(2)
    try:
        controller.stop()
        assert controller.status()["last_stop"]["terminalization_deferred"] is True
        assert controller._command_journal is journal
    finally:
        release.set()
        handler.join(timeout=2)
    assert not handler.is_alive() and not errors
    controller.stop()
    assert controller._command_journal is None
    assert controller.status()["last_stop"]["queue_audit"]["durable_complete"]["controller"] is True
    assert controller.status()["last_stop"]["queue_audit"]["streams"]["evidence_commands"]["command_outcomes"] == {
        "late-ack": "ack_rejected"}


def test_retention_retry_refuses_media_added_after_persisted_intent(tmp_path: Path):
    config = default_config()
    config["evidence"]["retain_completed"] = 0
    controller = RuntimeController(config, tmp_path)
    event_dir = tmp_path / "evidence" / "completed"
    event_dir.mkdir()
    (event_dir / "manifest.json").write_text('{"synthetic": true}', encoding="utf-8")
    controller._store.create_event({
        "id": "completed", "camera_id": "CAM01", "kind": "material_candidate",
        "triggered_at": 1.0, "status": "complete", "reason": "synthetic retained event",
        "layout_version": 1, "recording_status": "complete", "analysis_status": "supported",
        "completed_at": 2.0,
    })
    controller._store.prepare_retention(0, tmp_path / "evidence")
    unexpected = event_dir / "late-file.txt"
    unexpected.write_text("SYNTHETIC LATE MEDIA", encoding="utf-8")
    with pytest.raises(ValueError, match="unlisted"):
        controller._prune_completed()
    assert unexpected.is_file() and (event_dir / "manifest.json").is_file()
    assert controller._store.get_event("completed") is not None
    assert controller._store.get_retention_result("completed")["result"] is None
