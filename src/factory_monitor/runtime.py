"""Multiprocess runtime orchestration for capture, evidence and inference."""

from __future__ import annotations

import json
import hashlib
import multiprocessing as mp
import os
import queue
import shutil
import sys
import threading
import time
import uuid
from collections import Counter, deque
from pathlib import Path
from typing import Any

import numpy as np
import cv2

from .capture import CaptureError, open_capture
from .capture.control import (
    verify_detail_mapping,
    verify_grid_mapping,
)
from .config import validate_config
from .evidence import EvidenceRecorder, recover_interrupted_evidence, terminalize_incomplete_event
from .inference import LocalReviewError, ModelUnavailable, OllamaReviewer, YoloPersonDetector
from .rules import RuleEngine
from .store import EventStore
from .switching import SwitchPolicy


def _put_bounded(target: mp.Queue, value: Any) -> bool:
    try:
        target.put_nowait(value)
        return True
    except queue.Full:
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        try:
            target.put_nowait(value)
        except queue.Full:
            return False
        return False


def _identity(value: int | str) -> int:
    """Stable fixed-width identity for cross-process audit checksums."""
    if isinstance(value, int):
        return value
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest(), "big")


class _AuditJournal:
    """A worker-owned, append-only audit outside all lossy IPC queues.

    Flush at bounded intervals and on close; only a terminal marker establishes
    a complete ledger. A killed writer leaves its unflushed tail unknown.
    """

    def __init__(self, path: str | Path, run_id: str, worker: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.worker = worker
        self._file = self.path.open("x", encoding="utf-8", buffering=65536)
        self._last_flush = time.monotonic()
        self._lock = threading.Lock()
        self._closed = False

    def record(self, channel: str, transition: str, item_id: int | str, *,
               timestamp: float | None = None, cameras: list[str] | None = None) -> None:
        entry = {"run_id": self.run_id, "worker": self.worker, "channel": channel,
                 "transition": transition, "item_id": item_id}
        if timestamp is not None:
            entry["source_timestamp"] = timestamp
        if cameras is not None:
            entry["camera_ids"] = cameras
        with self._lock:
            if self._closed:
                raise RuntimeError("audit journal is closed")
            self._file.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
            if time.monotonic() - self._last_flush >= 0.25:
                self._file.flush()
                self._last_flush = time.monotonic()

    def flush(self) -> None:
        with self._lock:
            if not self._closed:
                self._file.flush()

    def close(self, complete: bool) -> None:
        with self._lock:
            if self._closed:
                return
            self._file.write(json.dumps({"run_id": self.run_id, "worker": self.worker,
                                         "terminal": bool(complete)}) + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            self._closed = True

    def stage(self, name: str, duration_seconds: float) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("audit journal is closed")
            self._file.write(json.dumps({"run_id": self.run_id, "worker": self.worker,
                                         "stage": name, "duration_seconds": duration_seconds}) + "\n")


def _count_identity(ledger: dict[str, Any], prefix: str, value: int | str,
                    timestamp: float | None = None, *, journal: _AuditJournal | None = None,
                    channel: str | None = None, cameras: list[str] | None = None) -> None:
    if journal is not None and channel is not None:
        journal.record(channel, prefix, value, timestamp=timestamp, cameras=cameras)
    identity = _identity(value)
    ledger[prefix] = ledger.get(prefix, 0) + 1
    ledger[f"{prefix}_id_sum"] = ledger.get(f"{prefix}_id_sum", 0) + identity
    ledger[f"{prefix}_id_square_sum"] = ledger.get(f"{prefix}_id_square_sum", 0) + identity * identity
    trace = ledger.setdefault("trace", [])
    if len(trace) == 512:
        trace.pop(0)
        ledger["trace_truncated"] = ledger.get("trace_truncated", 0) + 1
    entry: dict[str, Any] = {"transition": prefix, "item_id": value}
    if timestamp is not None:
        entry["source_timestamp"] = timestamp
    trace.append(entry)


def _report_queue_audit(messages: mp.Queue, payload: dict[str, Any]) -> None:
    # Failure to deliver makes reconciliation unknown; audit traffic must not
    # indefinitely delay a video worker or its bounded shutdown.
    try:
        messages.put(payload, timeout=0.2)
    except queue.Full:
        pass


def _offer_accounted(target: mp.Queue, packet: dict[str, Any], ledger: dict[str, int],
                     journal: _AuditJournal | None = None, channel: str | None = None) -> None:
    """Account for the old eviction and new rejection independently."""
    sequence = packet["frame_seq"]
    _count_identity(ledger, "attempted", sequence, packet["timestamp"], journal=journal, channel=channel)
    try:
        target.put_nowait(packet)
    except queue.Full:
        try:
            removed = target.get_nowait()
        except queue.Empty:
            removed = None
        if removed is not None:
            _count_identity(ledger, "evicted", removed["frame_seq"], removed.get("timestamp"), journal=journal, channel=channel)
        try:
            target.put_nowait(packet)
        except queue.Full:
            _count_identity(ledger, "rejected", sequence, packet["timestamp"], journal=journal, channel=channel)
        else:
            _count_identity(ledger, "accepted", sequence, packet["timestamp"], journal=journal, channel=channel)
    else:
        _count_identity(ledger, "accepted", sequence, packet["timestamp"], journal=journal, channel=channel)


_DETECTION_FRAME_EOS = {"_protocol": "eos", "stream": "detection_frames"}
_EVIDENCE_FRAME_EOS = {"_protocol": "eos", "stream": "evidence_frames"}
_EVIDENCE_COMMAND_EOS = {"_protocol": "eos", "stream": "evidence_commands"}
_STOP_TOTAL_SECONDS = 5.0
_STOP_CAPTURE_SECONDS = 1.5
_STOP_DETECTION_ACK_SECONDS = 1.5
_STOP_EVIDENCE_SECONDS = 2.0
_STOP_FORCE_JOIN_SECONDS = 0.5
_STOP_PUMP_SECONDS = 0.5


def _is_protocol_eos(value: Any, stream: str) -> bool:
    return isinstance(value, dict) and value.get("_protocol") == "eos" and value.get("stream") == stream


def _put_protocol_eos(target: mp.Queue, marker: dict[str, str], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            target.put(marker, timeout=min(0.1, remaining))
            return True
        except queue.Full:
            continue


def _crop(image: np.ndarray, normalized: list[float]) -> np.ndarray:
    height, width = image.shape[:2]
    x, y, crop_width, crop_height = normalized
    left, top = round(x * width), round(y * height)
    right, bottom = round((x + crop_width) * width), round((y + crop_height) * height)
    return image[max(0, top) : min(height, bottom), max(0, left) : min(width, right)].copy()


def map_live_views(
    image: np.ndarray,
    view_index: int,
    cameras: list[dict[str, Any]],
    expected_size: tuple[int, int],
    verification: dict[str, Any],
) -> tuple[dict[str, tuple[str, np.ndarray | None]], str]:
    invalid = {camera["id"]: ("mapping_invalid", None) for camera in cameras}
    if view_index == -1:
        ok, reason = verify_grid_mapping(
            image,
            expected_size,
            verification.get("grid_identities", {}),
            cameras,
        )
        if not ok:
            return invalid, reason
        return {camera["id"]: ("observable", _crop(image, camera["crop"])) for camera in cameras}, reason
    if not 0 <= view_index < len(cameras):
        return invalid, "view transition or identity is unverified"
    selected = cameras[view_index]["id"]
    ok, reason = verify_detail_mapping(
        image,
        expected_size,
        selected,
        verification.get("detail_identities", {}),
        cameras,
    )
    if not ok:
        return invalid, reason
    mapped = {camera["id"]: ("blind", None) for camera in cameras}
    mapped[selected] = ("detail", image.copy())
    return mapped, reason


def check_source_heartbeat(
    image: np.ndarray,
    heartbeat: dict[str, Any] | None,
    state: dict[str, Any],
    now: float,
) -> tuple[bool, str]:
    if not heartbeat:
        return False, "reliable source heartbeat is not configured"
    try:
        roi = _crop(image, heartbeat["roi"])
        if roi.size == 0:
            raise ValueError("heartbeat ROI is empty")
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        current = cv2.resize(gray, (64, 32), interpolation=cv2.INTER_AREA)
        maximum = float(heartbeat.get("max_unchanged_seconds", 10))
        minimum_delta = float(heartbeat.get("min_pixel_delta", 1.0))
    except (KeyError, TypeError, ValueError, cv2.error) as exc:
        return False, f"source heartbeat calibration is invalid: {exc}"
    previous = state.get("image")
    if previous is None or previous.shape != current.shape:
        state.update({"image": current, "last_change": now, "proven": False})
        return False, "source heartbeat has not yet shown a change"
    delta = float(np.mean(cv2.absdiff(current, previous)))
    state["image"] = current
    if delta >= minimum_delta:
        state["last_change"] = now
        state["proven"] = True
    if not state.get("proven"):
        return False, "source heartbeat has not yet shown a change"
    if now - float(state["last_change"]) > maximum:
        return False, "source heartbeat unchanged; playback is suspected stale"
    return True, "source heartbeat recently changed"


def apply_camera_heartbeats(
    mapped: dict[str, tuple[str, np.ndarray | None]],
    cameras: list[dict[str, Any]],
    verification: dict[str, Any],
    states: dict[str, dict[str, Any]],
    now: float,
) -> tuple[dict[str, tuple[str, np.ndarray | None]], dict[str, str]]:
    specs = verification.get("camera_heartbeats", {})
    results = dict(mapped)
    reasons: dict[str, str] = {}
    for camera in cameras:
        camera_id = camera["id"]
        health, camera_image = results[camera_id]
        if health not in {"observable", "detail"} or camera_image is None:
            continue
        spec = specs.get(camera_id)
        roi_key = "detail_roi" if health == "detail" else "grid_roi"
        if not isinstance(spec, dict) or roi_key not in spec:
            results[camera_id] = ("unavailable", None)
            reasons[camera_id] = f"reliable {health} heartbeat is not configured for {camera_id}"
            continue
        heartbeat = dict(spec)
        heartbeat["roi"] = spec[roi_key]
        fresh, reason = check_source_heartbeat(
            camera_image,
            heartbeat,
            states.setdefault(camera_id, {}),
            now,
        )
        if not fresh:
            results[camera_id] = ("unavailable", None)
            reasons[camera_id] = reason
    return results, reasons


def _capture_process(
    source_name: str,
    config: dict[str, Any],
    input_path: str | None,
    detection_frames: mp.Queue,
    evidence_frames: mp.Queue,
    preview_frames: mp.Queue,
    messages: mp.Queue,
    stop_event: mp.Event,
    audit_path: str | None = None,
    run_id: str | None = None,
) -> None:
    journal = _AuditJournal(audit_path, run_id, "capture") if audit_path and run_id else None
    source = None
    ledgers = {"detection_frames": {}, "evidence_frames": {}}
    frame_seq = 0
    last_reported_loss = 0
    failed = False
    try:
        source = open_capture(source_name, config, input_path)
        messages.put({"_kind": "worker", "worker": "capture", "state": "running", "pid": os.getpid()})
        fps = float(config["detection"]["fps"])
        interval = 1.0 / fps
        next_frame = time.monotonic()
        while not stop_event.is_set():
            try:
                packet = source.read()
            except StopIteration:
                messages.put({"_kind": "worker", "worker": "capture", "state": "ended", "pid": os.getpid()})
                break
            packet["frame_seq"] = frame_seq
            if run_id is not None:
                packet["run_id"] = run_id
            frame_seq += 1
            _offer_accounted(detection_frames, packet, ledgers["detection_frames"], journal, "detection_frames")
            _offer_accounted(evidence_frames, packet, ledgers["evidence_frames"], journal, "evidence_frames")
            _put_bounded(preview_frames, packet)
            dropped_detection = sum(ledgers["detection_frames"].get(key, 0) for key in ("evicted", "rejected"))
            dropped_evidence = sum(ledgers["evidence_frames"].get(key, 0) for key in ("evicted", "rejected"))
            total_loss = dropped_detection + dropped_evidence
            if total_loss and total_loss // 10 > last_reported_loss // 10:
                messages.put(
                    {
                        "_kind": "stats",
                        "dropped_detection_frames": dropped_detection,
                        "dropped_evidence_frames": dropped_evidence,
                    }
                )
                last_reported_loss = total_loss
            next_frame += interval
            stop_event.wait(max(0.0, next_frame - time.monotonic()))
    except Exception as exc:
        failed = True
        messages.put({"_kind": "error", "worker": "capture", "message": str(exc), "fatal": True})
    finally:
        try:
            if source is not None:
                source.close()
        except Exception as exc:
            failed = True
            messages.put({"_kind": "error", "worker": "capture", "message": str(exc), "fatal": True})
        eos_started = time.monotonic()
        detection_closed = _put_protocol_eos(detection_frames, _DETECTION_FRAME_EOS)
        if journal is not None:
            journal.stage("detection_frame_eos", time.monotonic() - eos_started)
        eos_started = time.monotonic()
        evidence_closed = _put_protocol_eos(evidence_frames, _EVIDENCE_FRAME_EOS)
        if journal is not None:
            journal.stage("evidence_frame_eos", time.monotonic() - eos_started)
        if not detection_closed or not evidence_closed:
            failed = True
            messages.put(
                {
                    "_kind": "error",
                    "worker": "capture",
                    "message": "capture could not close all durable frame streams",
                    "fatal": True,
                }
            )
        # Preview is explicitly lossy and has no durable shutdown contract.
        preview_frames.cancel_join_thread()
        drained = not failed and detection_closed and evidence_closed

        if journal is not None:
            journal.close(drained)

        _report_queue_audit(messages, {
            "_kind": "queue_audit", "worker": "capture", "complete": drained,
            "streams": ledgers, "last_frame_seq": frame_seq - 1,
        })
        messages.put({
            "_kind": "stats",
            "dropped_detection_frames": sum(ledgers["detection_frames"].get(key, 0) for key in ("evicted", "rejected")),
            "dropped_evidence_frames": sum(ledgers["evidence_frames"].get(key, 0) for key in ("evicted", "rejected")),
        })
        messages.put(
            {
                "_kind": "worker",
                "worker": "capture",
                "state": "drained" if drained else "stopped",
                "drained": drained,
                "pid": os.getpid(),
            }
        )


def _detection_process(
    source_name: str,
    config: dict[str, Any],
    frames: mp.Queue,
    messages: mp.Queue,
    stop_event: mp.Event,
    view_state: Any,
    audit_path: str | None = None,
    run_id: str | None = None,
) -> None:
    journal = _AuditJournal(audit_path, run_id, "detection") if audit_path and run_id else None
    detector = None
    drained = False
    frame_ledger: dict[str, int] = {}
    frame_audit_complete = True
    try:
        if source_name != "demo":
            messages.put({"_kind": "worker", "worker": "detection", "state": "warming", "pid": os.getpid()})
            detection = config["detection"]
            detector = YoloPersonDetector(
                detection["model_path"],
                device=detection["device"],
                confidence=detection["confidence"],
            )
        else:
            messages.put({"_kind": "worker", "worker": "detection", "state": "running", "pid": os.getpid()})
        expected = tuple(config["source"]["expected_size"])
        cameras = [camera for camera in config["cameras"] if camera["enabled"]]
        verification = config["switching"].get("verification", {})
        mapping_error_reported = False
        heartbeat_states: dict[str, dict[str, Any]] = {}
        warmed = source_name == "demo"
        while True:
            try:
                packet = frames.get(timeout=0.1)
            except queue.Empty:
                continue
            if _is_protocol_eos(packet, "detection_frames"):
                drained = True
                break
            image = packet["image"]
            if source_name == "live":
                mapped, mapping_reason = map_live_views(image, view_state.value, cameras, expected, verification)
                mapping_valid = any(health in {"observable", "detail"} for health, _ in mapped.values())
                mapped, heartbeat_reasons = apply_camera_heartbeats(
                    mapped,
                    cameras,
                    verification,
                    heartbeat_states,
                    float(packet["monotonic"]),
                )
                mapping_valid = any(health in {"observable", "detail"} for health, _ in mapped.values())
                if not mapping_valid and heartbeat_reasons:
                    mapping_reason = "; ".join(f"{camera}: {reason}" for camera, reason in heartbeat_reasons.items())
            else:
                actual = (image.shape[1], image.shape[0])
                mapping_valid = expected == (0, 0) or actual == expected
                mapping_reason = "source dimensions changed; mapping invalid"
                mapped = {
                    camera["id"]: (
                        "observable" if mapping_valid else "mapping_invalid",
                        _crop(image, camera["crop"]) if mapping_valid else None,
                    )
                    for camera in cameras
                }
                heartbeat_reasons = {}
            if not mapping_valid and not mapping_error_reported:
                messages.put({"_kind": "error", "worker": "detection", "message": f"live inference suspended: {mapping_reason}", "fatal": False})
                mapping_error_reported = True
            elif mapping_valid:
                mapping_error_reported = False
            for camera in cameras:
                camera_id = camera["id"]
                health, camera_image = mapped[camera_id]
                if camera_image is None or health not in {"observable", "detail"}:
                    people = []
                    camera_image = np.empty((0, 0, 3), dtype=np.uint8)
                elif source_name == "demo":
                    health = "observable"
                    people = packet.get("demo_people", {}).get(camera_id, [])
                else:
                    health = "observable"
                    people = detector.detect(camera_id, camera_image) if detector else []
                messages.put(
                    {
                        "_kind": "observation",
                        "observation": {
                            "camera_id": camera_id,
                            "timestamp": packet["timestamp"],
                            "health": "blind" if health == "detail" else health,
                            "layout_version": config["source"]["layout_version"],
                            "people": people,
                        },
                        "image": camera_image,
                        "synthetic": packet["synthetic"],
                        "health_reason": heartbeat_reasons.get(camera_id),
                    }
                )
            if not warmed:
                warmed = True
                messages.put({"_kind": "worker", "worker": "detection", "state": "running", "pid": os.getpid()})
            if "frame_seq" in packet:
                _count_identity(frame_ledger, "processed", packet["frame_seq"], packet["timestamp"],
                                journal=journal, channel="detection_frames", cameras=[camera["id"] for camera in cameras])
            else:
                frame_audit_complete = False
    except Exception as exc:
        messages.put({"_kind": "error", "worker": "detection", "message": str(exc), "fatal": True})
    finally:
        if journal is not None:
            journal.close(drained and frame_audit_complete)
        _report_queue_audit(messages, {"_kind": "queue_audit", "worker": "detection", "complete": drained and frame_audit_complete, "streams": {"detection_frames": frame_ledger}})
        messages.put(
            {
                "_kind": "worker",
                "worker": "detection",
                "state": "drained" if drained else "stopped",
                "drained": drained,
                "pid": os.getpid(),
            }
        )


def _evidence_process(
    config: dict[str, Any],
    evidence_dir: str,
    frames: mp.Queue,
    commands: mp.Queue,
    messages: mp.Queue,
    stop_event: mp.Event,
    view_state: Any,
    source_name: str,
    audit_path: str | None = None,
    run_id: str | None = None,
) -> None:
    journal = _AuditJournal(audit_path, run_id, "evidence") if audit_path and run_id else None
    evidence = config["evidence"]
    recorder = EvidenceRecorder(
        evidence_dir,
        pre_seconds=evidence["pre_seconds"],
        post_seconds=evidence["post_seconds"],
        preview_seconds=evidence["preview_seconds"],
        fps=config["detection"]["fps"],
        max_inflight=evidence["max_inflight"],
        max_disk_mb=evidence["max_disk_mb"],
    )
    cameras = [camera for camera in config["cameras"] if camera["enabled"]]
    messages.put({"_kind": "worker", "worker": "evidence", "state": "running", "pid": os.getpid()})
    heartbeat_states: dict[str, dict[str, Any]] = {}
    frame_ledger: dict[str, int] = {}
    command_ledger: dict[str, int] = {}
    frame_audit_complete = True
    def handle_command(command: dict[str, Any]) -> bool:
        if _is_protocol_eos(command, "evidence_commands"):
            return False
        if command.get("action") == "start":
            result = recorder.start(command["event"])
            messages.put({"_kind": "evidence_started", "event_id": command["event"]["id"], **result})
            _count_identity(command_ledger, "processed", str(command["event"]["id"]), journal=journal, channel="evidence_commands")
            _count_identity(command_ledger, "started" if result["ok"] else "recording_rejected", str(command["event"]["id"]), journal=journal, channel="evidence_commands")
        return True

    def handle_frame(packet: dict[str, Any]) -> None:
        if source_name == "live":
            mapped, reason = map_live_views(
                packet["image"],
                view_state.value,
                cameras,
                tuple(config["source"]["expected_size"]),
                config["switching"].get("verification", {}),
            )
            mapped, live_heartbeat_reasons = apply_camera_heartbeats(
                mapped,
                cameras,
                config["switching"].get("verification", {}),
                heartbeat_states,
                float(packet["monotonic"]),
            )
            for camera in cameras:
                health, camera_image = mapped[camera["id"]]
                if health in {"observable", "detail"} and camera_image is not None:
                    recorder.ingest(camera["id"], packet["timestamp"], camera_image,
                                    run_id=packet.get("run_id"), frame_id=packet.get("frame_seq"))
                else:
                    gap_reason = live_heartbeat_reasons.get(
                        camera["id"],
                        reason if health == "mapping_invalid" else "camera blind during verified detail view",
                    )
                    recorder.note_gap(camera["id"], packet["timestamp"], gap_reason)
        else:
            for camera in cameras:
                recorder.ingest(camera["id"], packet["timestamp"], _crop(packet["image"], camera["crop"]),
                                run_id=packet.get("run_id"), frame_id=packet.get("frame_seq"))

    frame_stream_open = True
    command_stream_open = True
    failed = False
    try:
        while frame_stream_open or command_stream_open:
            did_work = False
            if command_stream_open:
                while True:
                    try:
                        command = commands.get_nowait()
                    except queue.Empty:
                        break
                    did_work = True
                    command_stream_open = handle_command(command)
                    if not command_stream_open:
                        break
            if frame_stream_open:
                try:
                    packet = frames.get(timeout=0 if did_work else 0.05)
                except queue.Empty:
                    packet = None
                if packet is not None:
                    did_work = True
                    if _is_protocol_eos(packet, "evidence_frames"):
                        frame_stream_open = False
                        messages.put({"_kind": "shutdown_ack", "worker": "evidence", "stream": "evidence_frames"})
                    else:
                        handle_frame(packet)
                        if "frame_seq" in packet:
                            _count_identity(frame_ledger, "processed", packet["frame_seq"], packet["timestamp"],
                                            journal=journal, channel="evidence_frames", cameras=[camera["id"] for camera in cameras])
                        else:
                            frame_audit_complete = False
            elif command_stream_open and not did_work:
                try:
                    command = commands.get(timeout=0.05)
                except queue.Empty:
                    command = None
                if command is not None:
                    command_stream_open = handle_command(command)
            for update in recorder.drain_updates():
                messages.put({"_kind": "evidence_update", **update})
    except Exception as exc:
        failed = True
        messages.put({"_kind": "error", "worker": "evidence", "message": str(exc), "fatal": True})
    finally:
        close_succeeded = False
        try:
            for update in recorder.close():
                messages.put({"_kind": "evidence_update", **update})
            close_succeeded = True
        except Exception as exc:
            failed = True
            messages.put({"_kind": "error", "worker": "evidence", "message": str(exc), "fatal": True})
        drained = not failed and not frame_stream_open and not command_stream_open and close_succeeded
        if journal is not None:
            journal.close(drained and frame_audit_complete)
        _report_queue_audit(messages, {
            "_kind": "queue_audit", "worker": "evidence", "complete": drained and frame_audit_complete,
            "streams": {"evidence_frames": frame_ledger, "evidence_commands": command_ledger},
        })
        messages.put(
            {
                "_kind": "worker",
                "worker": "evidence",
                "state": "drained" if drained else "stopped",
                "drained": drained,
                "pid": os.getpid(),
            }
        )


def _review_process(config: dict[str, Any], tasks: mp.Queue, messages: mp.Queue, stop_event: mp.Event) -> None:
    review = config["review"]
    try:
        reviewer = OllamaReviewer(review["endpoint"], review["model"], timeout_seconds=review["timeout_seconds"])
        messages.put({"_kind": "worker", "worker": "review", "state": "running", "pid": os.getpid()})
        while not stop_event.is_set():
            try:
                task = tasks.get(timeout=0.1)
            except queue.Empty:
                continue
            started = time.monotonic()
            try:
                result = reviewer.review(task["frames"], deadline=task["deadline"], context=task["context"])
                decision = result.get("decision", "uncertain")
                analysis_status = decision if decision in {"supported", "dismissed", "uncertain"} else "uncertain"
                messages.put(
                    {
                        "_kind": "analysis",
                        "event_id": task["event"]["id"],
                        "analysis_status": analysis_status,
                        "analysis": result,
                        "latency_ms": (time.monotonic() - task["enqueued_at"]) * 1000,
                    }
                )
            except TimeoutError as exc:
                messages.put({"_kind": "analysis", "event_id": task["event"]["id"], "analysis_status": "timeout", "analysis": {"error": str(exc)}, "latency_ms": (time.monotonic() - task["enqueued_at"]) * 1000})
            except Exception as exc:
                messages.put({"_kind": "analysis", "event_id": task["event"]["id"], "analysis_status": "error", "analysis": {"error": str(exc)}, "latency_ms": (time.monotonic() - started) * 1000})
    except Exception as exc:
        messages.put({"_kind": "error", "worker": "review", "message": str(exc), "fatal": True})
    finally:
        messages.put({"_kind": "worker", "worker": "review", "state": "stopped", "pid": os.getpid()})


class RuntimeController:
    """Nonblocking GUI/CLI boundary around independently failing workers."""

    def __init__(self, config: dict[str, Any], data_dir: Path) -> None:
        validate_config(config)
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock_handle: Any = None
        for name in ("evidence", "cache", "logs"):
            (self.data_dir / name).mkdir(exist_ok=True)
        self._store = EventStore(self.data_dir / "events.sqlite3")
        self._rules = RuleEngine(config)
        self._switching = SwitchPolicy(config)
        self._events: deque[dict[str, Any]] = deque(maxlen=2000)
        self._events_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._frame_condition = threading.Condition()
        self._latest_full_frame: np.ndarray | None = None
        self._frame_generation = 0
        self._latest_frame_monotonic = float("-inf")
        self._view_controller: Any = None
        self._view_unavailable_reason = (
            "automatic click control is disabled: native hit-testing and DPI-safe target proof are not implemented"
        )
        self._view_action_running = False
        self._running = False
        self._source: str | None = None
        self._workers: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, mp.Process] = {}
        self._worker_stopped_events: dict[str, threading.Event] = {}
        self._worker_drained_events: dict[str, threading.Event] = {}
        self._pump: threading.Thread | None = None
        self._pump_stop_requested = threading.Event()
        self._message_drain_lock = threading.Lock()
        self._stop_event: mp.Event | None = None
        self._messages: mp.Queue | None = None
        self._preview_frames: mp.Queue | None = None
        self._evidence_commands: mp.Queue | None = None
        self._review_tasks: mp.Queue | None = None
        # Spawned processes rebuild queue semaphores after ``start`` returns;
        # retain every queue until shutdown so GC cannot unlink them first.
        self._queues: list[mp.Queue] = []
        self._last_images: dict[str, np.ndarray] = {}
        self._recent_frames: dict[str, deque[dict[str, Any]]] = {}
        self._view_state: Any = None
        self._review_deadlines: dict[str, float] = {}
        self._last_observation: dict[str, float] = {}
        self._stale_reported: set[str] = set()
        self._last_stop_report: dict[str, Any] | None = None
        self._queue_audits: dict[str, dict[str, Any]] = {}
        self._command_ledger: dict[str, int] = {}
        self._run_id: str | None = None
        self._audit_paths: dict[str, Path] = {}
        self._command_journal: _AuditJournal | None = None

    def start(self, source: str = "demo", input_path: str | None = None) -> None:
        with self._state_lock:
            if self._running:
                raise RuntimeError("runtime is already running")
            if source not in {"demo", "video", "live"}:
                raise ValueError("source must be demo, video, or live")
            if source == "live" and not self.config["source"]["calibrated"]:
                raise ValueError("live source must be calibrated before start")
            if source == "video" and not input_path:
                raise ValueError("video source requires input_path")
            self._acquire_runtime_ownership()
            started: list[mp.Process] = []
            try:
                db_recovered = self._store.recover_interrupted_recordings()
                for recovered in recover_interrupted_evidence(self.data_dir / "evidence"):
                    event_id = recovered.get("event_id")
                    if event_id and self._store.get_event(event_id):
                        self._store.update_event(event_id, status="incomplete", recording_status="incomplete", window_complete=False, gaps=recovered["gaps"], completed_at=recovered["completed_at"])
                recovery_ids = set(db_recovered)
                recovery_ids.update(
                    str(event["id"]) for event in self._store.list_events(limit=10_000)
                    if event.get("status") == "incomplete"
                    and event.get("recording_status") == "incomplete"
                    and event.get("completed_at") is None
                )
                for event_id in recovery_ids:
                    event = self._store.get_event(event_id)
                    if event is None or event.get("completed_at") is not None:
                        continue
                    reason = "crash recovery: recording interrupted before terminal acknowledgement"
                    persisted = self._existing_incomplete_manifest(event) or terminalize_incomplete_event(
                        self.data_dir / "evidence", event, reason)
                    self._store.update_event(event_id, status="incomplete", recording_status="incomplete", **persisted)
                self._terminalize_orphaned_reviews()
                self._prune_completed()

                self._run_id = uuid.uuid4().hex
                self._audit_paths = {name: self.data_dir / "logs" / f"audit-{self._run_id}-{name}.jsonl"
                                     for name in ("capture", "detection", "evidence", "controller")}
                self._command_journal = _AuditJournal(self._audit_paths["controller"], self._run_id, "controller")

                context = mp.get_context("spawn")
                self._stop_event = context.Event()
                self._messages = context.Queue(maxsize=100)
                detection_frames = context.Queue(maxsize=3)
                evidence_frames = context.Queue(maxsize=max(10, int(self.config["detection"]["fps"] * 4)))
                self._preview_frames = context.Queue(maxsize=2)
                self._evidence_commands = context.Queue(maxsize=max(4, self.config["evidence"]["max_inflight"] * 2))
                self._review_tasks = context.Queue(maxsize=max(4, self.config["evidence"]["max_inflight"] * 2))
                self._view_state = context.Value("i", -1)
                self._queues = [
                    self._messages,
                    detection_frames,
                    evidence_frames,
                    self._preview_frames,
                    self._evidence_commands,
                    self._review_tasks,
                ]
                args = (source, self.config, input_path, detection_frames, evidence_frames, self._preview_frames,
                        self._messages, self._stop_event, str(self._audit_paths["capture"]), self._run_id)
                self._processes = {
                    "capture": context.Process(target=_capture_process, args=args, name="factory-capture"),
                    "detection": context.Process(target=_detection_process, args=(source, self.config, detection_frames, self._messages,
                                                                                     self._stop_event, self._view_state,
                                                                                     str(self._audit_paths["detection"]), self._run_id), name="factory-detection"),
                    "evidence": context.Process(target=_evidence_process, args=(self.config, str(self.data_dir / "evidence"),
                                                                                   evidence_frames, self._evidence_commands,
                                                                                   self._messages, self._stop_event, self._view_state,
                                                                                   source, str(self._audit_paths["evidence"]),
                                                                                   self._run_id), name="factory-evidence"),
                }
                if self.config["review"]["enabled"]:
                    self._processes["review"] = context.Process(target=_review_process, args=(self.config, self._review_tasks, self._messages, self._stop_event), name="factory-review")
                self._worker_stopped_events = {name: threading.Event() for name in self._processes}
                self._worker_drained_events = {name: threading.Event() for name in self._processes}
                self._pump_stop_requested.clear()
                self._last_stop_report = None
                self._queue_audits.clear()
                self._command_ledger.clear()
                self._view_controller = self._build_view_controller() if source == "live" else None
                self._workers.clear()
                self._review_deadlines.clear()
                for name, process in self._processes.items():
                    process.start()
                    started.append(process)
                    self._workers[name] = {"state": "starting", "pid": process.pid}
                self._running = True
                self._source = source
                self._pump = threading.Thread(target=self._pump_messages, name="factory-runtime-pump", daemon=True)
                self._pump.start()
                self._emit({"type": "status", "state": "running", "source": source, "synthetic": source == "demo", "field_verified": False})
            except Exception:
                if self._command_journal is not None:
                    self._command_journal.close(False)
                    self._command_journal = None
                if self._stop_event is not None:
                    self._stop_event.set()
                for process in started:
                    process.join(timeout=1)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=1)
                self._running = False
                self._source = None
                self._release_runtime_ownership()
                raise

    def stop(self) -> None:
        with self._state_lock:
            if not self._running:
                return
            assert self._stop_event is not None
            self._stop_event.set()
            processes = dict(self._processes)
        stop_started_monotonic = time.monotonic()
        stop_requested_at = time.time()
        deadline = stop_started_monotonic + _STOP_TOTAL_SECONDS
        forced_workers: list[str] = []
        protocol_failures: list[str] = []
        stage_durations: dict[str, float] = {}

        def remaining(limit: float) -> float:
            return max(0.0, min(limit, deadline - time.monotonic()))

        def force_worker(name: str, reason: str) -> None:
            process = processes.get(name)
            if process is None:
                return
            protocol_failures.append(reason)
            if process.is_alive():
                process.terminate()
                forced_workers.append(name)
                self._emit(
                    {
                        "type": "error",
                        "worker": process.name,
                        "message": f"worker required forced termination: {reason}",
                        "fatal": False,
                    }
                )

        def drained_within(name: str, timeout: float) -> bool:
            process = processes.get(name)
            if process is None:
                return True
            acknowledged = self._worker_drained_events.get(name)
            if acknowledged is not None:
                acknowledged.wait(timeout=remaining(timeout))
            process.join(timeout=remaining(0.25))
            return bool(
                acknowledged is not None
                and acknowledged.is_set()
                and not process.is_alive()
                and process.exitcode == 0
            )

        stage_start = time.monotonic()
        if not drained_within("capture", _STOP_CAPTURE_SECONDS):
            force_worker("capture", "capture did not acknowledge durable frame stream closure")
        stage_durations["capture_ack"] = time.monotonic() - stage_start

        stage_start = time.monotonic()
        if protocol_failures:
            force_worker("detection", "detection drain was aborted because capture stream closure failed")
        elif not drained_within("detection", _STOP_DETECTION_ACK_SECONDS):
            force_worker("detection", "detection did not acknowledge completion of its final observation handler")
        stage_durations["detection_ack"] = time.monotonic() - stage_start

        stage_start = time.monotonic()
        if not protocol_failures:
            assert self._evidence_commands is not None
            if not _put_protocol_eos(self._evidence_commands, _EVIDENCE_COMMAND_EOS, timeout=remaining(0.75)):
                protocol_failures.append("evidence command stream could not be closed after detection acknowledgement")
        stage_durations["command_eos"] = time.monotonic() - stage_start
        stage_start = time.monotonic()
        if protocol_failures:
            force_worker("evidence", "evidence drain was aborted because an upstream shutdown acknowledgement failed")
        elif not drained_within("evidence", _STOP_EVIDENCE_SECONDS):
            force_worker("evidence", "evidence did not confirm command/frame drain and manifest finalization")
        stage_durations["evidence_ack"] = time.monotonic() - stage_start

        review = processes.get("review")
        if review is not None:
            review.join(timeout=remaining(0.5))
            if review.is_alive():
                force_worker("review", "review worker did not stop within the shutdown deadline")

        for name, process in processes.items():
            if process.is_alive():
                force_worker(name, f"{name} remained alive after the initial shutdown phase")

        stage_start = time.monotonic()
        force_deadline = stage_start + _STOP_FORCE_JOIN_SECONDS
        for process in processes.values():
            if process.is_alive():
                process.join(timeout=max(0.0, force_deadline - time.monotonic()))
        alive_workers = [name for name, process in processes.items() if process.is_alive()]
        stage_durations["force_join"] = time.monotonic() - stage_start

        stage_start = time.monotonic()
        pump_alive = self._pump is not None and self._pump.is_alive()
        if not alive_workers:
            self._pump_stop_requested.set()
            if self._pump is not None:
                self._pump.join(timeout=_STOP_PUMP_SECONDS)
                pump_alive = self._pump.is_alive()
            if not pump_alive and not self._drain_messages(lock_timeout=0.1):
                pump_alive = True
            if pump_alive:
                protocol_failures.append("message pump did not finish its current handler within the shutdown deadline")
        stage_durations["message_finish"] = time.monotonic() - stage_start

        unsafe_to_terminalize = bool(alive_workers or pump_alive)
        if pump_alive:
            alive_workers.append("message-pump")
        leftover_reason = (
            "shutdown protocol incomplete: " + "; ".join(dict.fromkeys(protocol_failures))
            if protocol_failures
            else "shutdown drain ended without an evidence terminal acknowledgement"
        )
        terminalization_failed = False
        if unsafe_to_terminalize:
            affected_event_ids = self._unfinished_recording_ids()
            protocol_failures.append(
                "event terminalization deferred because a worker or message handler may still own event/manifest writes"
            )
        else:
            affected_event_ids = self._unfinished_recording_ids()
            try:
                affected_event_ids = self._terminalize_unfinished_recordings(leftover_reason)
                if affected_event_ids and not protocol_failures:
                    protocol_failures.append("accepted evidence remained nonterminal after acknowledged drain")
            except Exception as exc:
                terminalization_failed = True
                protocol_failures.append(f"event terminalization failed; retry required: {exc}")
                self._emit({"type": "error", "worker": "shutdown", "message": str(exc), "fatal": False})
        cleanup_succeeded = False
        ownership_released = False
        retry_required = unsafe_to_terminalize or terminalization_failed
        if not unsafe_to_terminalize and not terminalization_failed:
            try:
                self._terminalize_orphaned_reviews()
                self._prune_completed()
                self._release_runtime_ownership()
                ownership_released = True
                cleanup_succeeded = True
            except Exception as exc:
                retry_required = True
                protocol_failures.append(f"final shutdown cleanup failed: {exc}")
                if self._lock_handle is None:
                    try:
                        self._acquire_runtime_ownership()
                    except Exception as ownership_exc:
                        protocol_failures.append(f"runtime ownership could not be reacquired: {ownership_exc}")
        if self._command_journal is not None:
            try:
                if unsafe_to_terminalize:
                    self._command_journal.flush()
                else:
                    self._command_journal.close(True)
                    self._command_journal = None
            except OSError as exc:
                protocol_failures.append(f"command audit could not be finalized: {exc}")
        queue_audit = self._reconcile_queue_audit()
        if queue_audit["state"] in {"unknown", "mismatch"}:
            protocol_failures.append(f"queue lifecycle audit {queue_audit['state']}; see queue_audit in shutdown report")
        confirmed = bool(
            cleanup_succeeded
            and ownership_released
            and not protocol_failures
            and not affected_event_ids
            and not alive_workers
        )
        report = {
            "confirmed": confirmed,
            "forced_workers": sorted(set(forced_workers)),
            "alive_workers": alive_workers,
            "affected_event_ids": affected_event_ids,
            "terminalization_deferred": unsafe_to_terminalize or terminalization_failed,
            "cleanup_succeeded": cleanup_succeeded,
            "ownership_released": ownership_released,
            "retry_required": retry_required,
            "reasons": list(dict.fromkeys(protocol_failures)),
            "worker_exitcodes": {name: process.exitcode for name, process in processes.items()},
            "queue_audit": queue_audit,
            "run_id": self._run_id,
            "audit_paths": {name: str(path) for name, path in self._audit_paths.items()},
            "stage_durations_seconds": stage_durations,
            "requested_at": stop_requested_at,
            "completed_at": time.time(),
            "elapsed_seconds": time.monotonic() - stop_started_monotonic,
        }
        try:
            report["report_path"] = str(self._persist_stop_report(report))
        except OSError as exc:
            report["confirmed"] = False
            report["reasons"].append(f"shutdown report could not be persisted: {exc}")
            report["retry_required"] = True
            retry_required = True
            if ownership_released:
                try:
                    self._acquire_runtime_ownership()
                    report["ownership_released"] = False
                    ownership_released = False
                except Exception as ownership_exc:
                    report["reasons"].append(f"runtime ownership could not be reacquired: {ownership_exc}")
            self._emit({"type": "error", "worker": "shutdown", "message": report["reasons"][-1], "fatal": False})
        with self._state_lock:
            self._running = retry_required
            if not retry_required:
                self._source = None
            self._last_stop_report = report
            self._emit({"type": "shutdown", **report})
            if not retry_required:
                self._emit({"type": "status", "state": "stopped", "source": None, "synthetic": False, "field_verified": False})

    def poll(self) -> list[dict[str, Any]]:
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        return events

    def request_view(self, camera_id: str) -> dict[str, Any]:
        if camera_id not in {camera["id"] for camera in self.config["cameras"] if camera["enabled"]}:
            return {"ok": False, "reason": "camera is not enabled/configured"}
        if not self._running or self._source != "live":
            return {"ok": False, "reason": "view control requires a running live source"}
        if self._view_controller is None:
            return {"ok": False, "reason": self._view_unavailable_reason}
        self._switching.enqueue(camera_id, "material_candidate", time.time())
        return {"ok": True, "reason": "detail request queued through verified switching policy"}

    def return_grid(self) -> dict[str, Any]:
        if not self._running or self._source != "live":
            return {"ok": False, "reason": "view control requires a running live source"}
        if self._view_controller is None:
            return {"ok": False, "reason": self._view_unavailable_reason}
        snapshot = self._switching.snapshot(time.time())
        if snapshot["state"] == "grid":
            return {"ok": True, "reason": "grid is already verified"}
        return {"ok": True, "reason": "grid return is serialized by the switching policy and mandatory dwell"}

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            workers = {}
            for name, state in self._workers.items():
                current = dict(state)
                process = self._processes.get(name)
                if process is not None:
                    current["alive"] = process.is_alive()
                    current["exitcode"] = process.exitcode
                workers[name] = current
            return {
                "running": self._running,
                "source": self._source,
                "field_verified": False,
                "synthetic": self._source == "demo",
                "controller_pid": os.getpid(),
                "workers": workers,
                "switching": self._switching.snapshot(time.time()),
                "last_stop": dict(self._last_stop_report) if self._last_stop_report is not None else None,
            }

    def _reconcile_queue_audit(self) -> dict[str, Any]:
        """Only completed worker snapshots can establish a terminal balance."""
        capture = self._queue_audits.get("capture")
        detection = self._queue_audits.get("detection")
        evidence = self._queue_audits.get("evidence")
        result: dict[str, Any] = {
            "state": "reconciled", "streams": {},
            "accepted_meaning": "cumulative first acceptance; evicted/processed are later outcomes of accepted items",
            "preview_note": "preview is intentionally lossy and outside durable frame reconciliation",
        }
        severity = {"reconciled": 0, "summary_consistent": 1, "unknown": 2, "mismatch": 3}
        for stream, producer, consumer in (
            ("detection_frames", capture, detection),
            ("evidence_frames", capture, evidence),
            ("evidence_commands", {"complete": True, "streams": {"evidence_commands": self._command_ledger}}, evidence),
        ):
            offered = (producer or {}).get("streams", {}).get(stream)
            handled = (consumer or {}).get("streams", {}).get(stream)
            if not producer or not consumer or not producer.get("complete") or not consumer.get("complete") or offered is None or handled is None:
                result["streams"][stream] = {"state": "unknown", "producer": offered, "consumer": handled,
                                             "reason": "worker accounting snapshot or drain acknowledgement missing"}
                if severity["unknown"] > severity[result["state"]]:
                    result["state"] = "unknown"
                continue
            rejected = offered.get("rejected", 0)
            evicted = offered.get("evicted", 0)
            accepted = offered.get("accepted", 0)
            attempted = offered.get("attempted", 0)
            processed = handled.get("processed", 0)
            balance = accepted - evicted - processed
            checks = [attempted == accepted + rejected, balance == 0]
            for suffix in ("_id_sum", "_id_square_sum"):
                checks.append(offered.get("attempted" + suffix, 0) == offered.get("accepted" + suffix, 0) + offered.get("rejected" + suffix, 0))
                checks.append(offered.get("accepted" + suffix, 0) == offered.get("evicted" + suffix, 0) + handled.get("processed" + suffix, 0))
            if stream == "evidence_commands":
                checks.append(processed == handled.get("started", 0) + handled.get("recording_rejected", 0))
            exact_trace = not offered.get("trace_truncated") and not handled.get("trace_truncated")
            if exact_trace:
                def identities(ledger: dict[str, Any], transition: str) -> Counter:
                    return Counter(item["item_id"] for item in ledger.get("trace", []) if item["transition"] == transition)

                for transition in ("attempted", "accepted", "rejected", "evicted"):
                    checks.append(sum(identities(offered, transition).values()) == offered.get(transition, 0))
                for transition in (("processed", "started", "recording_rejected") if stream == "evidence_commands" else ("processed",)):
                    checks.append(sum(identities(handled, transition).values()) == handled.get(transition, 0))
                checks.append(identities(offered, "attempted") == identities(offered, "accepted") + identities(offered, "rejected"))
                checks.append(identities(offered, "accepted") == identities(offered, "evicted") + identities(handled, "processed"))
                if stream == "evidence_commands":
                    checks.append(identities(handled, "processed") == identities(handled, "started") + identities(handled, "recording_rejected"))
            stream_state = ("reconciled" if exact_trace else "summary_consistent") if all(checks) else "mismatch"
            result["streams"][stream] = {
                "state": stream_state, "attempted": attempted, "accepted": accepted,
                "evicted": evicted, "queue_rejected": rejected, "processed": processed,
                "stop_unaccounted": balance, "producer": offered, "consumer": handled,
                "identity_mode": "full_bounded_trace" if exact_trace else "summary_only_identity_not_exact",
            }
            if severity[stream_state] > severity[result["state"]]:
                result["state"] = stream_state
        if self._run_id is not None:
            self._reconcile_durable_records(result)
        return result

    def _reconcile_durable_records(self, result: dict[str, Any]) -> None:
        """Cross-check full identities from worker-owned files, not IPC snapshots."""
        records: dict[str, list[dict[str, Any]]] = {}
        complete: dict[str, bool] = {}
        stages: dict[str, float] = {}
        for worker in ("capture", "detection", "evidence", "controller"):
            path = self._audit_paths.get(worker)
            if path is None or not path.is_file():
                complete[worker] = False
                records[worker] = []
                continue
            try:
                entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                valid = all(entry.get("run_id") == self._run_id and entry.get("worker") == worker
                            for entry in entries)
                complete[worker] = bool(valid and entries and entries[-1].get("terminal") is True)
                records[worker] = [entry for entry in entries if "transition" in entry]
                for entry in entries:
                    if "stage" in entry and isinstance(entry.get("duration_seconds"), (int, float)):
                        stages[f"{worker}.{entry['stage']}"] = float(entry["duration_seconds"])
            except (OSError, UnicodeError, json.JSONDecodeError):
                complete[worker] = False
                records[worker] = []

        pairs = (("detection_frames", "capture", "detection"),
                 ("evidence_frames", "capture", "evidence"),
                 ("evidence_commands", "controller", "evidence"))
        for channel, producer, consumer in pairs:
            stream = result["streams"][channel]
            if not complete[producer] or not complete[consumer]:
                stream.update(state="unknown", identity_mode="incomplete_durable_records",
                              consumed=None, remaining=None, shutdown_discarded=None,
                              reason="producer or consumer durable terminal record missing")
                if channel == "evidence_commands":
                    attempts = {entry["item_id"] for entry in records[producer]
                                if entry.get("channel") == channel and entry.get("transition") == "attempted"}
                    rejected = {entry["item_id"] for entry in records[producer]
                                if entry.get("channel") == channel and entry.get("transition") == "rejected"}
                    stream["command_outcomes"] = {
                        event_id: "queue_rejected" if event_id in rejected else "deferred"
                        for event_id in attempts
                    }
                stream.pop("stop_unaccounted", None)
                result["state"] = "unknown" if result["state"] != "mismatch" else "mismatch"
                continue

            def ids(worker: str, transition: str) -> Counter:
                return Counter(entry["item_id"] for entry in records[worker]
                               if entry.get("channel") == channel and entry.get("transition") == transition)

            attempted, accepted, rejected = (ids(producer, name) for name in ("attempted", "accepted", "rejected"))
            evicted = ids(producer, "evicted")
            consumed = ids(consumer, "processed")
            checks = [attempted == accepted + rejected, accepted == evicted + consumed]
            if channel == "evidence_commands":
                checks.append(consumed == ids(consumer, "started") + ids(consumer, "recording_rejected"))
                acknowledged = ids(producer, "ack_started") + ids(producer, "ack_rejected")
                checks.append(consumed == acknowledged)
                stream["command_outcomes"] = {
                    event_id: ("queue_rejected" if event_id in rejected else
                               "ack_started" if event_id in ids(producer, "ack_started") else
                               "ack_rejected" if event_id in ids(producer, "ack_rejected") else
                               "deferred")
                    for event_id in attempted
                }
            else:
                checks.append(all(count == 1 for count in attempted.values()))
            if stream["state"] == "mismatch":
                checks.append(False)
            exact_state = "reconciled" if all(checks) else "mismatch"
            stream.update(state=exact_state, identity_mode="exact_durable_records",
                          attempted=sum(attempted.values()), accepted=sum(accepted.values()),
                          queue_rejected=sum(rejected.values()), evicted=sum(evicted.values()),
                          consumed=sum(consumed.values()), processed=sum(consumed.values()),
                          remaining=0 if exact_state == "reconciled" else None,
                          shutdown_discarded=0 if exact_state == "reconciled" else None,
                          stop_unaccounted=0 if exact_state == "reconciled" else None)
            if exact_state == "mismatch":
                result["state"] = "mismatch"
        result["durable_record_counts"] = {worker: len(entries) for worker, entries in records.items()}
        result["durable_complete"] = complete
        result["worker_stage_durations_seconds"] = stages
        states = {item["state"] for item in result["streams"].values()}
        result["state"] = "mismatch" if "mismatch" in states else "unknown" if "unknown" in states else "reconciled"

    def _pump_messages(self) -> None:
        assert self._stop_event is not None
        while not self._pump_stop_requested.is_set():
            self._drain_messages(block=True)
            if not self._stop_event.is_set():
                self._drain_preview_frames()
                self._watchdog()
                self._drive_switching()

    def _drain_preview_frames(self) -> None:
        if self._preview_frames is None:
            return
        latest = None
        while True:
            try:
                latest = self._preview_frames.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        with self._frame_condition:
            self._latest_full_frame = latest["image"]
            self._latest_frame_monotonic = float(latest["monotonic"])
            self._frame_generation += 1
            self._frame_condition.notify_all()

    def _drain_messages(self, block: bool = False, lock_timeout: float | None = None) -> bool:
        if self._messages is None:
            return True
        if lock_timeout is None:
            self._message_drain_lock.acquire()
        elif not self._message_drain_lock.acquire(timeout=max(0.0, lock_timeout)):
            return False
        try:
            first = True
            while True:
                try:
                    message = self._messages.get(timeout=0.1 if block and first else 0)
                except queue.Empty:
                    return True
                first = False
                self._handle_message(message)
        finally:
            self._message_drain_lock.release()

    def _handle_message(self, message: dict[str, Any]) -> None:
        kind = message.pop("_kind")
        if kind == "queue_audit":
            self._queue_audits[message["worker"]] = message
            return
        if kind == "worker":
            self._workers[message["worker"]] = {"state": message["state"], "pid": message["pid"]}
            if message["state"] in {"drained", "stopped"}:
                stopped = self._worker_stopped_events.get(message["worker"])
                if stopped is not None:
                    stopped.set()
            if message["state"] == "drained" and message.get("drained") is True:
                drained = self._worker_drained_events.get(message["worker"])
                if drained is not None:
                    drained.set()
            self._emit({"type": "status", **message, "source": self._source, "synthetic": self._source == "demo", "field_verified": False})
            if message["worker"] == "detection" and message["state"] == "running":
                now = time.monotonic()
                for camera in self.config["cameras"]:
                    if camera["enabled"]:
                        self._last_observation.setdefault(camera["id"], now)
            unexpected = (
                message["state"] == "stopped"
                and self._running
                and self._stop_event is not None
                and not self._stop_event.is_set()
                and not (message["worker"] == "capture" and self._source == "video")
            )
            if unexpected:
                self._emit({"type": "error", "worker": message["worker"], "message": "worker exited unexpectedly", "fatal": True})
                if message["worker"] in {"capture", "detection"}:
                    self._invalidate_all("capture/detection worker exited; continuity reset")
            return
        if kind == "shutdown_ack":
            self._emit({"type": "shutdown_ack", **message})
            return
        if kind == "error":
            self._emit({"type": "error", **message})
            if message.get("fatal") and message.get("worker") in {"capture", "detection"}:
                self._invalidate_all(f"{message['worker']} failure; continuity reset")
            return
        if kind == "stats":
            self._emit({"type": "stats", **message})
            return
        if kind == "observation":
            observation = message["observation"]
            camera_id = observation["camera_id"]
            self._last_observation[camera_id] = time.monotonic()
            self._stale_reported.discard(camera_id)
            if message["image"].size:
                self._last_images[camera_id] = message["image"]
                history = self._recent_frames.setdefault(camera_id, deque(maxlen=6))
                history.append({"timestamp": observation["timestamp"], "image": message["image"]})
                self._emit({"type": "frame", "camera_id": camera_id, "image": message["image"], "timestamp": observation["timestamp"], "people": observation["people"], "synthetic": message["synthetic"]})
            else:
                history = self._recent_frames.setdefault(camera_id, deque(maxlen=6))
                history.clear()
            health_event = {"type": "health", "camera_id": camera_id, "health": observation["health"], "timestamp": observation["timestamp"]}
            if message.get("health_reason"):
                health_event["reason"] = message["health_reason"]
            self._emit(health_event)
            try:
                candidates = self._rules.observe(observation)
            except (TypeError, ValueError) as exc:
                self._rules.reset(camera_id)
                self._emit({"type": "health", "camera_id": camera_id, "health": "unavailable", "reason": f"invalid detector observation: {exc}", "timestamp": observation["timestamp"]})
                self._emit({"type": "error", "worker": "detection", "camera_id": camera_id, "message": f"invalid observation rejected: {exc}", "fatal": False})
                return
            for event in candidates:
                self._accept_candidate(event, list(history))
            return
        if kind == "evidence_started":
            event_id = message["event_id"]
            if self._command_journal is not None:
                self._command_journal.record("evidence_commands", "ack_started" if message["ok"] else "ack_rejected",
                                             str(event_id))
            if message["ok"]:
                self._store.update_event(event_id, status="recording", recording_status="recording", evidence_path=message.get("evidence_path"))
            else:
                self._store.update_event(event_id, status="error", recording_status="error", window_complete=False, gaps=[message["reason"]], completed_at=time.time())
                self._emit({"type": "error", "worker": "evidence", "event_id": event_id, "message": message["reason"], "fatal": False})
            self._emit({"type": "event_updated", "event": self._store.get_event(event_id)})
            return
        if kind == "evidence_update":
            event_id = message.pop("event_id")
            if self._store.get_event(event_id):
                self._store.update_event(event_id, **message)
                self._emit({"type": "event_updated", "event": self._store.get_event(event_id)})
                if message.get("completed_at") is not None:
                    self._prune_completed()
            return
        if kind == "analysis":
            event_id = message.pop("event_id")
            if event_id not in self._review_deadlines:
                return
            self._review_deadlines.pop(event_id, None)
            if self._store.get_event(event_id):
                self._store.update_event(event_id, **message)
                self._emit({"type": "analysis", "event_id": event_id, **message})
                self._prune_completed()

    def _accept_candidate(self, event: dict[str, Any], frames: list[dict[str, Any]]) -> None:
        review_enabled = self.config["review"]["enabled"]
        event = {
            **event,
            "analysis_status": "pending" if review_enabled else "uncertain",
            "analysis": None if review_enabled else {"reason": "local review disabled; manual review required"},
            "recording_status": "recording",
            "review_label": "pending",
        }
        self._store.create_event(event)
        self._emit({"type": "candidate", "event": event})
        assert self._evidence_commands is not None
        _count_identity(self._command_ledger, "attempted", str(event["id"]))
        if self._command_journal is not None:
            self._command_journal.record("evidence_commands", "attempted", str(event["id"]))
        try:
            self._evidence_commands.put_nowait({"action": "start", "event": event})
        except queue.Full:
            _count_identity(self._command_ledger, "rejected", str(event["id"]))
            if self._command_journal is not None:
                self._command_journal.record("evidence_commands", "rejected", str(event["id"]))
            self._store.update_event(event["id"], status="error", recording_status="error", window_complete=False, gaps=["evidence command queue full"], completed_at=time.time())
            self._emit({"type": "error", "worker": "evidence", "event_id": event["id"], "message": "evidence command queue full", "fatal": False})
        else:
            _count_identity(self._command_ledger, "accepted", str(event["id"]))
            if self._command_journal is not None:
                self._command_journal.record("evidence_commands", "accepted", str(event["id"]))
        if review_enabled and self._review_tasks is not None:
            enqueued = time.monotonic()
            camera = next(camera for camera in self.config["cameras"] if camera["id"] == event["camera_id"])
            context = {
                "camera_id": event["camera_id"],
                "kind": event["kind"],
                "reason": event["reason"],
                "material_roi": camera["material_roi"],
                "station_roi": camera["station_roi"],
                "exit_line": camera["exit_line"],
                "absence_threshold_seconds": camera["absence_seconds"],
                "observed_duration_seconds": camera["absence_seconds"] if event["kind"] == "station_absence" else None,
            }
            deadline = enqueued + float(self.config["review"]["timeout_seconds"])
            task = {"event": event, "context": context, "frames": frames[-6:], "enqueued_at": enqueued, "deadline": deadline}
            try:
                self._review_tasks.put_nowait(task)
                self._review_deadlines[event["id"]] = deadline
            except queue.Full:
                self._store.update_event(event["id"], analysis_status="timeout", analysis={"error": "review queue full"})
                self._emit({"type": "analysis", "event_id": event["id"], "analysis_status": "timeout", "analysis": {"error": "review queue full"}})
        self._switching.enqueue(event["camera_id"], event["kind"], event["triggered_at"])

    def _prune_completed(self) -> None:
        root = (self.data_dir / "evidence").resolve()
        intents = self._store.prepare_retention(self.config["evidence"]["retain_completed"], root)
        for intent in intents:
            event_id = str(intent["event_id"])
            event_dir = root / event_id
            try:
                if Path(intent["evidence_root"]).resolve() != root:
                    raise ValueError("retention intent evidence root differs from runtime root")
                if event_dir.is_symlink() or event_dir.resolve().parent != root:
                    raise ValueError("unsafe evidence directory")
                listed = {item["path"] for item in intent["media"]}
                listed_dirs = {parent.as_posix() for name in listed
                               for parent in Path(name).parents if parent != Path(".")}
                if event_dir.is_dir():
                    for current in event_dir.rglob("*"):
                        relative = current.relative_to(event_dir).as_posix()
                        if current.is_symlink():
                            raise ValueError(f"unlisted or linked evidence media: {relative}")
                        if current.is_dir():
                            if relative not in listed_dirs:
                                raise ValueError(f"unlisted evidence directory: {relative}")
                        elif not current.is_file() or relative not in listed:
                            raise ValueError(f"unlisted evidence media: {relative}")
                for item in intent["media"]:
                    media = event_dir / item["path"]
                    if media.is_symlink() or not media.resolve().is_relative_to(event_dir.resolve()):
                        raise ValueError("unsafe evidence media path")
                    if not media.exists():
                        continue  # A previous interrupted removal may have removed this file.
                    digest = hashlib.sha256()
                    with media.open("rb") as handle:
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(block)
                    if digest.hexdigest() != item["sha256"] or media.stat().st_size != item["size"]:
                        raise ValueError(f"evidence changed since cleanup intent: {media.name}")
                if event_dir.is_dir():
                    shutil.rmtree(event_dir)
                self._store.complete_retention(event_id)
            except Exception as exc:
                self._store.fail_retention(event_id, str(exc))
                self._emit({"type": "error", "worker": "retention", "event_id": event_id,
                            "message": str(exc), "fatal": False})
                raise

    def _existing_incomplete_manifest(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Resume a manifest write that succeeded before its DB update failed."""
        directory = self.data_dir / "evidence" / str(event["id"])
        manifest_path = directory / "manifest.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (not isinstance(payload, dict) or payload.get("event_id") != event["id"]
                or payload.get("recording_status") != "incomplete"
                or payload.get("window_complete") is not False
                or not isinstance(payload.get("completed_at"), (int, float))):
            return None
        return {
            "evidence_path": str(directory),
            "preview_path": payload.get("preview_path"),
            "gaps": payload.get("gaps") or [],
            "window_complete": False,
            "reason": payload.get("reason") or "incomplete recording recovered from manifest",
            "completed_at": payload["completed_at"],
        }

    def _terminalize_unfinished_recordings(self, reason: str) -> list[str]:
        affected: list[str] = []
        for event in self._store.list_events(limit=10_000):
            if not self._event_is_unfinished_recording(event):
                continue
            persisted = self._existing_incomplete_manifest(event) or terminalize_incomplete_event(
                self.data_dir / "evidence", event, reason)
            self._store.update_event(
                event["id"],
                status="incomplete",
                recording_status="incomplete",
                **persisted,
            )
            affected.append(str(event["id"]))
            self._emit({"type": "event_updated", "event": self._store.get_event(event["id"])})
            self._emit(
                {
                    "type": "error",
                    "worker": "shutdown",
                    "event_id": event["id"],
                    "message": reason,
                    "fatal": False,
                }
            )
        return affected

    def _unfinished_recording_ids(self) -> list[str]:
        return [
            str(event["id"])
            for event in self._store.list_events(limit=10_000)
            if self._event_is_unfinished_recording(event)
        ]

    @staticmethod
    def _event_is_unfinished_recording(event: dict[str, Any]) -> bool:
        if event.get("completed_at") is not None:
            return False
        return event.get("recording_status") == "recording" or event.get("status") in {"candidate", "recording"}

    def _persist_stop_report(self, report: dict[str, Any]) -> Path:
        logs = self.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        target = logs / f"shutdown-{time.time_ns()}-{os.getpid()}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        return target

    def _terminalize_orphaned_reviews(self) -> list[str]:
        """Make abandoned local reviews visible and eligible for retention.

        Review tasks and their monotonic deadlines are deliberately process-local.
        Once workers have stopped, a persisted ``pending`` value cannot receive a
        trustworthy result and must never be silently re-enqueued on a later run.
        """
        reason = "local review interrupted by runtime stop/restart; manual review required"
        recovered: list[str] = []
        for event in self._store.list_events(limit=10_000):
            if event.get("analysis_status") != "pending":
                continue
            self._store.update_event(
                event["id"],
                analysis_status="uncertain",
                analysis={"error": reason, "reason": reason, "manual_review_required": True},
                review_label="manual_review_required",
            )
            self._review_deadlines.pop(event["id"], None)
            recovered.append(event["id"])
        return recovered

    def _build_view_controller(self) -> None:
        # Image readback catches many errors after a click, but cannot prove the
        # OS hit target under DPI scaling, overlays or client-area offsets.
        # Keep dispatch disabled until a target-specific native adapter exists.
        return None

    def _wait_for_fresh_frame(self, after_monotonic: float) -> np.ndarray:
        with self._frame_condition:
            self._frame_condition.wait_for(lambda: self._latest_frame_monotonic > after_monotonic, timeout=3)
            if self._latest_frame_monotonic <= after_monotonic or self._latest_full_frame is None:
                raise RuntimeError("capture did not provide a fresh frame for view readback")
            return self._latest_full_frame.copy()

    def _drive_switching(self) -> None:
        if self._view_controller is None or self._view_action_running or not self._running:
            return
        action = self._switching.next_action(time.time())
        if action is None:
            return
        self._view_action_running = True

        def execute() -> None:
            try:
                self._view_state.value = -2
                if action["action"] == "detail":
                    result = self._view_controller.request_view(action["camera_id"])
                    self._switching.confirm(action["camera_id"], time.time(), verified=result["ok"])
                    if result["ok"]:
                        enabled = [camera for camera in self.config["cameras"] if camera["enabled"]]
                        self._view_state.value = next(index for index, camera in enumerate(enabled) if camera["id"] == action["camera_id"])
                else:
                    result = self._view_controller.return_grid()
                    self._switching.confirm(None, time.time(), verified=result["ok"])
                    if result["ok"]:
                        self._view_state.value = -1
                if not result["ok"]:
                    self._emit({"type": "error", "worker": "view_control", "message": result["reason"], "fatal": False})
                self._emit({"type": "status", "worker": "view_control", "state": self._switching.snapshot(time.time())})
            except Exception as exc:
                self._switching.confirm(action.get("camera_id"), time.time(), verified=False)
                self._emit({"type": "error", "worker": "view_control", "message": str(exc), "fatal": False})
            finally:
                self._view_action_running = False

        threading.Thread(target=execute, name="factory-view-control", daemon=True).start()

    def _watchdog(self) -> None:
        if not self._running:
            return
        now = time.monotonic()
        detection_state = self._workers.get("detection", {}).get("state")
        if detection_state == "running":
            stale_after = max(2.5, 3.0 / float(self.config["detection"]["fps"]))
            for camera_id, last_seen in list(self._last_observation.items()):
                if now - last_seen <= stale_after or camera_id in self._stale_reported:
                    continue
                self._stale_reported.add(camera_id)
                self._rules.reset(camera_id)
                self._recent_frames.setdefault(camera_id, deque(maxlen=6)).clear()
                self._emit(
                    {
                        "type": "health",
                        "camera_id": camera_id,
                        "health": "unavailable",
                        "reason": "observation watchdog expired; continuity reset",
                        "timestamp": time.time(),
                    }
                )
        for name, process in self._processes.items():
            state = self._workers.get(name, {}).get("state")
            if not process.is_alive() and process.exitcode not in (None, 0) and state != "crashed":
                self._workers[name] = {"state": "crashed", "pid": process.pid}
                self._emit({"type": "error", "worker": name, "message": f"worker crashed with exit code {process.exitcode}", "fatal": True})
                if name in {"capture", "detection"}:
                    self._invalidate_all(f"{name} worker crashed; continuity reset")
        for event_id, deadline in list(self._review_deadlines.items()):
            if now < deadline:
                continue
            self._review_deadlines.pop(event_id, None)
            event = self._store.get_event(event_id)
            if event and event.get("analysis_status") == "pending":
                analysis = {"error": "review deadline expired in controller watchdog; manual review required"}
                self._store.update_event(event_id, analysis_status="timeout", analysis=analysis, latency_ms=(now - deadline + float(self.config["review"]["timeout_seconds"])) * 1000)
                self._emit({"type": "analysis", "event_id": event_id, "analysis_status": "timeout", "analysis": analysis})
                self._prune_completed()

    def _acquire_runtime_ownership(self) -> None:
        handle = (self.data_dir / "runtime.lock").open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise RuntimeError(f"runtime data directory is already owned: {self.data_dir}") from exc
        self._lock_handle = handle

    def _release_runtime_ownership(self) -> None:
        handle = self._lock_handle
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._lock_handle = None

    def _invalidate_all(self, reason: str) -> None:
        now = time.time()
        for camera in self.config["cameras"]:
            if not camera["enabled"]:
                continue
            camera_id = camera["id"]
            self._rules.reset(camera_id)
            self._recent_frames.setdefault(camera_id, deque(maxlen=6)).clear()
            self._stale_reported.add(camera_id)
            self._emit({"type": "health", "camera_id": camera_id, "health": "unavailable", "reason": reason, "timestamp": now})

    def _emit(self, event: dict[str, Any]) -> None:
        with self._events_lock:
            self._events.append(event)
