#!/usr/bin/env python3
"""Bounded Q4 synthetic evidence matrix; run only through diagnostics/bounded_runner.py.

It deliberately does not claim detection, model, live-camera or field success.
All Q4 candidates are explicit synthetic injections after a real 30-second
cache fill.  The real multiprocess RuntimeController and EvidenceRecorder own
the queues, JPEGs, MP4s, manifests and stop protocol unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

import factory_monitor.runtime as runtime_module
from factory_monitor.config import default_config, save_config
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore
from phase2_content_oracle import (
    DEFAULT_CAMERA_IDS,
    FrameExpectation,
    MarkerError,
    inject_and_record_source_frame,
    load_source_oracle,
    verify_media,
)


FULL_FRAME_SHAPE = (540, 960, 3)
FULL_FRAME_BYTES = 960 * 540 * 3
CAMERA_CROP_SHAPE = (180, 240, 3)
CAMERA_CROP_BYTES = 240 * 180 * 3
FPS = 2.0
PRE_SECONDS = 30.0
POST_SECONDS = 60.0
BOUNDARY_TOLERANCE_SECONDS = 0.25
POST_SAMPLE_EPSILON_SECONDS = 1e-6
CAMERA_IDS = DEFAULT_CAMERA_IDS


@dataclass(frozen=True)
class Scenario:
    name: str
    batches: tuple[tuple[float, tuple[str, ...]], ...]
    stop_at_seconds: float
    max_seconds: float
    expects_complete_media: bool


SCENARIOS = {
    "q4a": Scenario("q4a", ((31.0, CAMERA_IDS),), 99.0, 110.0, True),
    "q4b": Scenario("q4b", ((31.0, CAMERA_IDS[:5]), (51.0, CAMERA_IDS[5:])), 119.0, 130.0, True),
    # Q4c validates explicit over-limit rejection only, never a full recording.
    "q4c": Scenario("q4c", ((31.0, CAMERA_IDS),), 0.0, 50.0, False),
}


class MatrixFailure(RuntimeError):
    """A synthetic result is incomplete and must not be labelled Q4 PASS."""


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


class _OracleDemoCapture:
    """Temporary child-process wrapper around the real built-in DemoCapture."""

    def __init__(self, source: Any, *, run_id: str, oracle_path: Path, context_path: Path) -> None:
        self._source = source
        self._run_id = run_id
        self._oracle_path = oracle_path
        self._context_path = context_path
        self._frame_seq = 0

    def read(self) -> dict[str, Any]:
        packet = self._source.read()
        image = packet.get("image")
        if not isinstance(image, np.ndarray) or image.shape != FULL_FRAME_SHAPE:
            raise RuntimeError(f"synthetic source returned unexpected image shape {getattr(image, 'shape', None)!r}")
        packet["image"] = inject_and_record_source_frame(
            image, run_id=self._run_id, frame_seq=self._frame_seq, oracle_path=self._oracle_path
        )
        # This is a source-side sidecar, written before the runtime receives
        # the packet; it is not a manifest and cannot be reconstructed later.
        _append_jsonl(
            self._context_path,
            {
                "kind": "synthetic_source_context_v1",
                "run_id": self._run_id,
                "frame_seq": self._frame_seq,
                "timestamp": float(packet["timestamp"]),
                "monotonic": float(packet["monotonic"]),
                "shape": list(packet["image"].shape),
                "payload_bytes": int(packet["image"].nbytes),
            },
        )
        self._frame_seq += 1
        return packet

    def close(self) -> None:
        self._source.close()


def capture_worker(*args: Any, **kwargs: Any) -> None:
    """Spawn-safe capture entry point that changes only this synthetic process."""
    if len(args) < 10:
        raise RuntimeError("unexpected runtime capture argument contract")
    source_name, audit_path, run_id = args[0], args[8], args[9]
    if source_name != "demo" or not isinstance(run_id, str) or not run_id:
        raise RuntimeError("matrix capture wrapper supports only the controller's synthetic demo run")
    audit = Path(audit_path)
    oracle_path = audit.with_name(f"source-oracle-{run_id}.jsonl")
    context_path = audit.with_name(f"source-context-{run_id}.jsonl")
    original_open_capture = runtime_module.open_capture

    def marked_open_capture(*open_args: Any, **open_kwargs: Any) -> _OracleDemoCapture:
        source = original_open_capture(*open_args, **open_kwargs)
        return _OracleDemoCapture(source, run_id=run_id, oracle_path=oracle_path, context_path=context_path)

    runtime_module.open_capture = marked_open_capture
    try:
        runtime_module._capture_process(*args, **kwargs)
    finally:
        runtime_module.open_capture = original_open_capture


def _config() -> dict[str, Any]:
    config = default_config()
    config["source"].update(
        {
            "calibrated": True,
            "expected_size": [960, 540],
            "window_title": "SYNTHETIC Q4 MATRIX — NOT A LIVE CLIENT",
        }
    )
    config["detection"]["fps"] = int(FPS)
    config["review"]["enabled"] = False
    if config["evidence"]["max_inflight"] != 10:
        raise RuntimeError("Q4 matrix requires the production default max_inflight=10")
    if (config["evidence"]["pre_seconds"], config["evidence"]["post_seconds"]) != (PRE_SECONDS, POST_SECONDS):
        raise RuntimeError("Q4 matrix requires the production 30/60-second evidence window")
    for camera in config["cameras"]:
        # Do not let fixture rules manufacture candidates; only _inject emits them.
        camera["material_roi"] = []
        camera["exit_line"] = []
        camera["station_roi"] = []
        camera["absence_seconds"] = 86_400
        camera["schedule"] = {"days": list(range(7)), "active": [["00:00", "24:00"]], "breaks": []}
    return config


def _inject(
    controller: RuntimeController, camera_ids: Iterable[str], label: str, *, triggered_at: float
) -> list[dict[str, Any]]:
    injected: list[dict[str, Any]] = []
    for camera_id in camera_ids:
        event = {
            "id": f"{label}-{camera_id}-{uuid.uuid4().hex}",
            "camera_id": camera_id,
            "kind": "material_candidate",
            "triggered_at": triggered_at,
            "status": "candidate",
            "reason": "explicit synthetic Q4 injection; not a detector, model, or normal-rule decision",
            "layout_version": 1,
        }
        controller._accept_candidate(event, [])
        injected.append(event)
    return injected


def _pump(controller: RuntimeController, collected: list[dict[str, Any]]) -> None:
    collected.extend(controller.poll())


def _wait_for_recording(controller: RuntimeController, event_ids: set[str], events: list[dict[str, Any]], timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _pump(controller, events)
        statuses = {event_id: (controller._store.get_event(event_id) or {}).get("recording_status") for event_id in event_ids}
        if all(status == "recording" for status in statuses.values()):
            return
        time.sleep(0.05)
    raise RuntimeError(f"events did not all enter real recording state: {statuses}")


def _load_context(path: Path) -> dict[int, dict[str, Any]]:
    entries: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            payload = json.loads(line)
            if payload.get("kind") != "synthetic_source_context_v1":
                raise MarkerError(f"unexpected source context record at line {line_number}")
            sequence = int(payload["frame_seq"])
            if sequence in entries:
                raise MarkerError(f"duplicate source context frame_seq {sequence}")
            if tuple(payload["shape"]) != FULL_FRAME_SHAPE or int(payload["payload_bytes"]) != FULL_FRAME_BYTES:
                raise MarkerError(f"source payload contract failed at frame_seq {sequence}")
            entries[sequence] = payload
    if not entries:
        raise MarkerError("source context has no frames")
    return entries


def _wait_source_anchor(context_path: Path, timeout: float = 8.0) -> tuple[float, float]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if context_path.is_file():
            context = _load_context(context_path)
            first = context[min(context)]
            return float(first["timestamp"]), float(first["monotonic"])
        time.sleep(0.05)
    raise MatrixFailure("synthetic source did not write its first independent oracle record")


def _latest_source_context(context_path: Path) -> dict[str, Any]:
    context = _load_context(context_path)
    return context[max(context)]


def _expected_window(event: dict[str, Any], oracle: tuple[FrameExpectation, ...], context: dict[int, dict[str, Any]]) -> tuple[FrameExpectation, ...]:
    records = [(entry, context[entry.frame_seq]) for entry in oracle if entry.frame_seq in context]
    if len(records) != len(oracle):
        raise MarkerError("source marker oracle and source context disagree")
    cutoff = float(event["triggered_at"]) - PRE_SECONDS
    finish = float(event["triggered_at"]) + POST_SECONDS
    before = [item for item in records if float(item[1]["timestamp"]) <= cutoff]
    after = [item for item in records if float(item[1]["timestamp"]) > cutoff]
    if not after:
        raise MarkerError(f"no source frame after pre-window cutoff for {event['id']}")
    expected: list[FrameExpectation] = []
    predecessor = before[-1] if before else None
    following = after[0]
    if predecessor is not None:
        predecessor_gap = cutoff - float(predecessor[1]["timestamp"])
        following_gap = float(following[1]["timestamp"]) - cutoff
        if predecessor_gap <= BOUNDARY_TOLERANCE_SECONDS and predecessor_gap < following_gap:
            expected.append(predecessor[0])
    for item in after:
        expected.append(item[0])
        if float(item[1]["timestamp"]) >= finish:
            break
    if float(context[expected[-1].frame_seq]["timestamp"]) < finish:
        raise MarkerError(f"source stopped before post-window completion for {event['id']}")
    first_time = float(context[expected[0].frame_seq]["timestamp"])
    last_time = float(context[expected[-1].frame_seq]["timestamp"])
    # Start coverage requires the nearest available half-frame (the recorder's
    # own pre-window policy).  End coverage is intentionally different:
    # finalization writes the first actual source frame at/after the target.
    if abs(first_time - cutoff) > BOUNDARY_TOLERANCE_SECONDS:
        raise MarkerError(f"pre-window boundary misses the allowed 0.25s tolerance for {event['id']}")
    if last_time - finish > 1.0 / FPS + POST_SAMPLE_EPSILON_SECONDS:
        raise MarkerError(f"post-window final source frame exceeds one sample period for {event['id']}")
    return tuple(expected)


def event_media_paths(event: dict[str, Any]) -> tuple[Path, Path]:
    """Resolve actual MP4 evidence and its sibling JPEG frames directory."""
    evidence_path = Path(event["evidence_path"])
    clip = evidence_path / "evidence.mp4" if evidence_path.is_dir() else evidence_path
    return clip, clip.parent / "frames"


def _verify_event(event: dict[str, Any], oracle: tuple[FrameExpectation, ...], context: dict[int, dict[str, Any]]) -> dict[str, Any]:
    expected = _expected_window(event, oracle, context)
    clip, frames_dir = event_media_paths(event)
    frame_paths = sorted(frames_dir.glob("*.jpg"))
    if not frame_paths or not clip.is_file():
        raise MarkerError(f"evidence JPEG/MP4 missing for {event['id']}")
    jpeg_report = verify_media(frame_paths, expected, camera_id=event["camera_id"])
    mp4_report = verify_media([clip], expected, camera_id=event["camera_id"])
    if jpeg_report.frame_sequences != mp4_report.frame_sequences:
        raise MarkerError(f"JPEG/MP4 source-ID order disagreement for {event['id']}")
    capture = cv2.VideoCapture(str(clip))
    if not capture.isOpened():
        capture.release()
        raise MarkerError(f"OpenCV could not reopen video evidence for {event['id']}")
    try:
        actual_fps = float(capture.get(cv2.CAP_PROP_FPS))
        encoded_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()
    if actual_fps <= 0 or abs(actual_fps - FPS) > 0.01:
        raise MarkerError(f"unexpected MP4 FPS for {event['id']}: {actual_fps}")
    duration_seconds = mp4_report.frame_count / actual_fps
    if encoded_count != mp4_report.frame_count or mp4_report.frame_count < 181 or duration_seconds < 90.5:
        raise MarkerError(f"evidence window too short for {event['id']}: {mp4_report.frame_count} frames")
    manifest_path = clip.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("recording_status") != "complete" or manifest.get("window_complete") is not True or manifest.get("gaps"):
        raise MarkerError(f"manifest is incomplete or gapped for {event['id']}")
    manifest_source_ids = [
        (item.get("run_id"), item.get("frame_id"), item.get("camera_id")) for item in manifest.get("source_frames", [])
    ]
    expected_source_ids = [(item.run_id, item.frame_seq, event["camera_id"]) for item in expected]
    if manifest_source_ids != expected_source_ids:
        raise MarkerError(f"manifest source-frame UUID/ID mapping disagrees with independent oracle for {event['id']}")
    return {
        "event_id": event["id"],
        "camera_id": event["camera_id"],
        "expected_frame_count": len(expected),
        "jpeg": asdict(jpeg_report),
        "mp4": asdict(mp4_report),
        "mp4_fps": actual_fps,
        "mp4_duration_seconds": duration_seconds,
        "source_first_frame_seq": expected[0].frame_seq,
        "source_last_frame_seq": expected[-1].frame_seq,
    }


def _validate_source_contract(
    oracle: tuple[FrameExpectation, ...], context: dict[int, dict[str, Any]], runtime_run_id: str
) -> None:
    if not oracle or {item.run_id for item in oracle} != {runtime_run_id}:
        raise MatrixFailure("source oracle full run UUID does not match RuntimeController metadata")
    if set(context) != {item.frame_seq for item in oracle}:
        raise MatrixFailure("source context frame IDs do not exactly match the source marker oracle")
    if any(entry.get("run_id") != runtime_run_id for entry in context.values()):
        raise MatrixFailure("source context full run UUID does not match RuntimeController metadata")


def validate_normal_case(
    *, stop: dict[str, Any] | None, rows: list[dict[str, Any]], expected_event_ids: set[str], media_verification: list[dict[str, Any]]
) -> None:
    if not stop or stop.get("confirmed") is not True:
        raise MatrixFailure("normal Q4 case requires a confirmed stop report")
    audit = stop.get("queue_audit") or {}
    if audit.get("state") != "reconciled":
        raise MatrixFailure("normal Q4 case requires reconciled queue audit")
    streams = audit.get("streams") or {}
    for name in ("detection_frames", "evidence_frames", "evidence_commands"):
        stream = streams.get(name) or {}
        if stream.get("state") != "reconciled":
            raise MatrixFailure(f"queue audit stream {name} is not exact/reconciled")
        if any(stream.get(key) != 0 for key in ("remaining", "shutdown_discarded", "stop_unaccounted")):
            raise MatrixFailure(f"queue audit stream {name} has remaining, discarded, or unknown items")
    by_id = {row["id"]: row for row in rows}
    if set(by_id) != expected_event_ids:
        raise MatrixFailure("database event IDs do not exactly match injected normal-case IDs")
    for event in by_id.values():
        if event.get("status") != "complete" or event.get("recording_status") != "complete" or event.get("window_complete") is not True:
            raise MatrixFailure(f"database event is not complete: {event['id']}")
        if event.get("gaps"):
            raise MatrixFailure(f"database event has a source/evidence gap: {event['id']}")
    if {item["event_id"] for item in media_verification} != expected_event_ids:
        raise MatrixFailure("media oracle verification does not cover every injected normal-case event")


def _backup_database(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _queue_capacities(config: dict[str, Any]) -> dict[str, int]:
    return {
        "detection_frames": 3,
        "evidence_frames": max(10, int(config["detection"]["fps"] * 4)),
        "preview_frames": 2,
        "evidence_commands": max(4, config["evidence"]["max_inflight"] * 2),
    }


def run(case: str, output: Path) -> dict[str, Any]:
    scenario = SCENARIOS[case]
    output = output.resolve()
    if output.exists():
        raise RuntimeError(f"refusing to reuse output directory: {output}")
    output.mkdir(parents=True)
    config = _config()
    save_config(config, output / "synthetic-q4-config.json")
    original_capture_process = runtime_module._capture_process
    runtime_module._capture_process = capture_worker
    controller = RuntimeController(config, output)
    events: list[dict[str, Any]] = []
    injected: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "case": case,
        "synthetic": True,
        "field_gate": "FAIL",
        "candidate_origin": "explicit injection only; normal synthetic rules disabled",
        "full_frame": {"shape": list(FULL_FRAME_SHAPE), "payload_bytes": FULL_FRAME_BYTES},
        "camera_crop": {"shape": list(CAMERA_CROP_SHAPE), "payload_bytes": CAMERA_CROP_BYTES},
        "queue_capacities": _queue_capacities(config),
        "config": {"fps": FPS, "pre_seconds": PRE_SECONDS, "post_seconds": POST_SECONDS, "max_inflight": config["evidence"]["max_inflight"]},
        "marker_run_tag_bits": 16,
        "marker_run_tag_limit": "the visual tag is only 16 bits and is not a unique run UUID; full UUID equality is checked through source oracle and manifest source_frames",
        "marker_uuid_mapping": "full RuntimeController UUID is stored in both independent source oracle sidecars",
        "verification_boundary_contract": {
            "start": "nearest source frame must be within 0.25s of trigger-pre",
            "end": "first actual source frame at/after trigger+post is valid through one configured sample period (1/fps) plus epsilon",
            "note": "this corrects the former erroneous symmetric 0.25s post-boundary check; it does not waive manifest gap validation",
        },
        "started_at": time.time(),
        "ok": False,
    }
    started_monotonic = time.monotonic()
    try:
        controller.start("demo")
        run_id = controller._run_id
        if not run_id:
            raise RuntimeError("RuntimeController did not create a run id")
        oracle_path = output / "logs" / f"source-oracle-{run_id}.jsonl"
        context_path = output / "logs" / f"source-context-{run_id}.jsonl"
        result.update({"runtime_run_id": run_id, "source_oracle": str(oracle_path), "source_context": str(context_path)})
        source_started_at, source_started_monotonic = _wait_source_anchor(context_path)
        result["source_started_at"] = source_started_at
        result["source_started_monotonic"] = source_started_monotonic
        batch_index = 0
        q4c_rejected: dict[str, Any] | None = None
        case_complete = False
        while not case_complete:
            elapsed = time.monotonic() - source_started_monotonic
            _pump(controller, events)
            if elapsed > scenario.max_seconds or time.monotonic() - started_monotonic > scenario.max_seconds + 10:
                raise RuntimeError(f"matrix case exceeded its bounded duration of {scenario.max_seconds}s")
            while batch_index < len(scenario.batches) and elapsed >= scenario.batches[batch_index][0]:
                scheduled_at, cameras = scenario.batches[batch_index]
                source_now = _latest_source_context(context_path)
                batch = _inject(
                    controller, cameras, f"{case}-batch{batch_index + 1}", triggered_at=float(source_now["timestamp"])
                )
                injected.extend(batch)
                result.setdefault("injections", []).append({"scheduled_seconds": scheduled_at, "actual_seconds": elapsed, "event_ids": [item["id"] for item in batch]})
                batch_index += 1
                if case == "q4c":
                    _wait_for_recording(controller, {item["id"] for item in injected}, events)
                    rejected = _inject(
                        controller, ("CAM01",), "q4c-overlimit", triggered_at=float(_latest_source_context(context_path)["timestamp"])
                    )[0]
                    injected.append(rejected)
                    _pump(controller, events)
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        _pump(controller, events)
                        q4c_rejected = controller._store.get_event(rejected["id"])
                        if q4c_rejected and q4c_rejected.get("recording_status") == "error":
                            break
                        time.sleep(0.05)
                    if not q4c_rejected or q4c_rejected.get("recording_status") != "error":
                        raise RuntimeError("Q4c 11th explicit event was not audibly rejected at max_inflight=10")
                    result["overlimit_event"] = q4c_rejected
                    case_complete = True
                    break
            if scenario.stop_at_seconds and elapsed >= scenario.stop_at_seconds:
                break
            time.sleep(0.05)
        controller.stop()
        _pump(controller, events)
        result["stop"] = controller.status()["last_stop"]
        result["actual_capture_duration_seconds"] = time.monotonic() - started_monotonic
        _backup_database(output / "events.sqlite3", output / "events-backup.sqlite3")
        rows = EventStore(output / "events-backup.sqlite3")
        try:
            persisted = rows.list_events(limit=100)
        finally:
            rows.close()
        result["events"] = persisted
        if scenario.expects_complete_media:
            oracle = load_source_oracle(oracle_path)
            context = _load_context(context_path)
            _validate_source_contract(oracle, context, run_id)
            expected_ids = {item["id"] for item in injected}
            complete = [row for row in persisted if row["id"] in expected_ids]
            result["media_verification"] = [_verify_event(row, oracle, context) for row in complete]
            validate_normal_case(
                stop=result["stop"], rows=persisted, expected_event_ids=expected_ids, media_verification=result["media_verification"]
            )
        else:
            result["media_verification"] = []
            command_stream = ((result.get("stop") or {}).get("queue_audit") or {}).get("streams", {}).get("evidence_commands") or {}
            outcomes = command_stream.get("command_outcomes") or {}
            counts = {name: list(outcomes.values()).count(name) for name in ("ack_started", "ack_rejected")}
            reason = " ".join(str(item) for item in (q4c_rejected or {}).get("gaps", []))
            if counts != {"ack_started": 10, "ack_rejected": 1} or "inflight limit" not in reason.lower():
                raise MatrixFailure("Q4c requires ten started commands, one explicit inflight-limit rejection, and no substitute error")
            result["q4c_command_outcomes"] = counts
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            controller.stop()
            _pump(controller, events)
            result.setdefault("stop", controller.status()["last_stop"])
        except Exception as stop_exc:
            result.setdefault("stop_error", f"{type(stop_exc).__name__}: {stop_exc}")
        runtime_module._capture_process = original_capture_process
        result["finished_at"] = time.time()
        result["elapsed_seconds"] = time.monotonic() - started_monotonic
        result["runtime_messages"] = [{key: value for key, value in event.items() if key != "image"} for event in events]
        (output / "qa-phase2-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded synthetic Q4 matrix with independent pixel oracle")
    parser.add_argument("--case", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--output", type=Path, required=True, help="new exclusive synthetic data directory")
    args = parser.parse_args()
    try:
        result = run(args.case, args.output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": result["ok"], "case": args.case, "output": str(args.output), "error": result.get("error")}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
