"""Multiprocess runtime orchestration for capture, evidence and inference."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import shutil
import sys
import threading
import time
from collections import deque
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
from .evidence import EvidenceRecorder, recover_interrupted_evidence
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
) -> None:
    source = None
    dropped_detection = dropped_evidence = 0
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
            if not _put_bounded(detection_frames, packet):
                dropped_detection += 1
            if not _put_bounded(evidence_frames, packet):
                dropped_evidence += 1
            _put_bounded(preview_frames, packet)
            if (dropped_detection + dropped_evidence) and (dropped_detection + dropped_evidence) % 10 == 1:
                messages.put(
                    {
                        "_kind": "stats",
                        "dropped_detection_frames": dropped_detection,
                        "dropped_evidence_frames": dropped_evidence,
                    }
                )
            next_frame += interval
            stop_event.wait(max(0.0, next_frame - time.monotonic()))
    except Exception as exc:
        messages.put({"_kind": "error", "worker": "capture", "message": str(exc), "fatal": True})
    finally:
        if source is not None:
            source.close()
        messages.put({"_kind": "worker", "worker": "capture", "state": "stopped", "pid": os.getpid()})


def _detection_process(
    source_name: str,
    config: dict[str, Any],
    frames: mp.Queue,
    messages: mp.Queue,
    stop_event: mp.Event,
    view_state: Any,
) -> None:
    detector = None
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
        while not stop_event.is_set():
            try:
                packet = frames.get(timeout=0.1)
            except queue.Empty:
                continue
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
    except Exception as exc:
        messages.put({"_kind": "error", "worker": "detection", "message": str(exc), "fatal": True})
    finally:
        messages.put({"_kind": "worker", "worker": "detection", "state": "stopped", "pid": os.getpid()})


def _evidence_process(
    config: dict[str, Any],
    evidence_dir: str,
    frames: mp.Queue,
    commands: mp.Queue,
    messages: mp.Queue,
    stop_event: mp.Event,
    view_state: Any,
    source_name: str,
) -> None:
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
    try:
        while not stop_event.is_set():
            while True:
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                if command["action"] == "start":
                    result = recorder.start(command["event"])
                    messages.put({"_kind": "evidence_started", "event_id": command["event"]["id"], **result})
            try:
                packet = frames.get(timeout=0.05)
            except queue.Empty:
                continue
            if source_name == "live":
                mapped, reason = map_live_views(
                    packet["image"],
                    view_state.value,
                    cameras,
                    tuple(config["source"]["expected_size"]),
                    config["switching"].get("verification", {}),
                )
                mapped, heartbeat_reasons = apply_camera_heartbeats(
                    mapped,
                    cameras,
                    config["switching"].get("verification", {}),
                    heartbeat_states,
                    float(packet["monotonic"]),
                )
                for camera in cameras:
                    health, camera_image = mapped[camera["id"]]
                    if health in {"observable", "detail"} and camera_image is not None:
                        recorder.ingest(camera["id"], packet["timestamp"], camera_image)
                    else:
                        gap_reason = heartbeat_reasons.get(camera["id"], reason if health == "mapping_invalid" else "camera blind during verified detail view")
                        recorder.note_gap(camera["id"], packet["timestamp"], gap_reason)
            else:
                for camera in cameras:
                    recorder.ingest(camera["id"], packet["timestamp"], _crop(packet["image"], camera["crop"]))
            for update in recorder.drain_updates():
                messages.put({"_kind": "evidence_update", **update})
    except Exception as exc:
        messages.put({"_kind": "error", "worker": "evidence", "message": str(exc), "fatal": True})
    finally:
        for update in recorder.close():
            messages.put({"_kind": "evidence_update", **update})
        messages.put({"_kind": "worker", "worker": "evidence", "state": "stopped", "pid": os.getpid()})


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
        self._pump: threading.Thread | None = None
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
                self._store.recover_interrupted_recordings()
                for recovered in recover_interrupted_evidence(self.data_dir / "evidence"):
                    event_id = recovered.get("event_id")
                    if event_id and self._store.get_event(event_id):
                        self._store.update_event(event_id, status="incomplete", recording_status="incomplete", gaps=recovered["gaps"], completed_at=recovered["completed_at"])
                self._terminalize_orphaned_reviews()
                self._prune_completed()

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
                args = (source, self.config, input_path, detection_frames, evidence_frames, self._preview_frames, self._messages, self._stop_event)
                self._processes = {
                    "capture": context.Process(target=_capture_process, args=args, name="factory-capture"),
                    "detection": context.Process(target=_detection_process, args=(source, self.config, detection_frames, self._messages, self._stop_event, self._view_state), name="factory-detection"),
                    "evidence": context.Process(target=_evidence_process, args=(self.config, str(self.data_dir / "evidence"), evidence_frames, self._evidence_commands, self._messages, self._stop_event, self._view_state, source), name="factory-evidence"),
                }
                if self.config["review"]["enabled"]:
                    self._processes["review"] = context.Process(target=_review_process, args=(self.config, self._review_tasks, self._messages, self._stop_event), name="factory-review")
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
            processes = list(self._processes.values())
        for process in processes:
            process.join(timeout=3)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
                self._emit({"type": "error", "worker": process.name, "message": "worker required forced termination", "fatal": False})
        if self._pump is not None:
            self._pump.join(timeout=1)
        self._drain_messages()
        with self._state_lock:
            self._terminalize_orphaned_reviews()
            self._prune_completed()
            self._running = False
            self._source = None
            self._emit({"type": "status", "state": "stopped", "source": None, "synthetic": False, "field_verified": False})
            self._release_runtime_ownership()

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
            }

    def _pump_messages(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set() or any(process.is_alive() for process in self._processes.values()):
            self._drain_messages(block=True)
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

    def _drain_messages(self, block: bool = False) -> None:
        if self._messages is None:
            return
        first = True
        while True:
            try:
                message = self._messages.get(timeout=0.1 if block and first else 0)
            except queue.Empty:
                return
            first = False
            self._handle_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        kind = message.pop("_kind")
        if kind == "worker":
            self._workers[message["worker"]] = {"state": message["state"], "pid": message["pid"]}
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
            if message["ok"]:
                self._store.update_event(event_id, status="recording", recording_status="recording", evidence_path=message.get("evidence_path"))
            else:
                self._store.update_event(event_id, status="error", recording_status="error", gaps=[message["reason"]], completed_at=time.time())
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
        try:
            self._evidence_commands.put_nowait({"action": "start", "event": event})
        except queue.Full:
            self._store.update_event(event["id"], status="error", recording_status="error", gaps=["evidence command queue full"], completed_at=time.time())
            self._emit({"type": "error", "worker": "evidence", "event_id": event["id"], "message": "evidence command queue full", "fatal": False})
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
        removed = self._store.prune_completed(self.config["evidence"]["retain_completed"])
        root = (self.data_dir / "evidence").resolve()
        for event in removed:
            event_dir = root / str(event["id"])
            try:
                event_dir.resolve().relative_to(root)
            except ValueError:
                self._emit({"type": "error", "worker": "retention", "message": "refused unsafe evidence deletion", "fatal": False})
                continue
            if event_dir.is_dir():
                shutil.rmtree(event_dir)

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
