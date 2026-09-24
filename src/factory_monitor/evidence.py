"""Crash-visible ring buffering and event clip finalization."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@dataclass
class _Recording:
    event: dict[str, Any]
    directory: Path
    frames: list[tuple[float, Path]] = field(default_factory=list)
    gaps: list[list[float | None]] = field(default_factory=list)
    last_timestamp: float | None = None
    preview_path: Path | None = None
    open_gap_start: float | None = None
    gap_reasons: list[dict[str, Any]] = field(default_factory=list)
    source_frames: list[dict[str, Any]] = field(default_factory=list)


class EvidenceRecorder:
    """Record cached and future frames without depending on inference progress."""

    def __init__(
        self,
        evidence_dir: str | Path,
        *,
        pre_seconds: float = 30,
        post_seconds: float = 60,
        preview_seconds: float = 30,
        fps: float = 5,
        max_inflight: int = 10,
        max_disk_mb: float = 10240,
    ) -> None:
        self.root = Path(evidence_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.pre_seconds = float(pre_seconds)
        self.post_seconds = float(post_seconds)
        self.preview_seconds = float(preview_seconds)
        self.fps = max(0.1, float(fps))
        self.max_inflight = int(max_inflight)
        self.max_disk_bytes = int(max_disk_mb * 1024 * 1024)
        # JPEG-compressed cache avoids retaining 30 seconds of raw 4K arrays.
        self._cache: dict[str, deque[tuple[float, bytes, dict[str, Any]]]] = defaultdict(deque)
        self._cache_bytes = 0
        self._max_cache_bytes = max(1024 * 1024, min(self.max_disk_bytes // 10, 512 * 1024 * 1024))
        self._active: dict[str, _Recording] = {}
        self._updates: deque[dict[str, Any]] = deque()

    def ingest(self, camera_id: str, timestamp: float, image: np.ndarray, *, run_id: str | None = None, frame_id: int | None = None) -> None:
        encoded_ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not encoded_ok:
            encoded_bytes = b""
        else:
            encoded_bytes = encoded.tobytes()
        cache = self._cache[camera_id]
        identity = {"run_id": run_id, "frame_id": frame_id, "camera_id": camera_id}
        cache.append((timestamp, encoded_bytes, identity))
        self._cache_bytes += len(encoded_bytes)
        # A start command and its source frame travel through separate queues.
        # Retain one sampling interval of command-arrival grace, plus the real
        # boundary predecessor. This does not extend the event's requested
        # window or byte cap; longer delays must still disclose missing frames.
        cache_cutoff = timestamp - self.pre_seconds - 1.0 / self.fps
        while len(cache) > 1 and cache[1][0] <= cache_cutoff:
            _, expired, _ = cache.popleft()
            self._cache_bytes -= len(expired)
        self._trim_cache()
        for event_id, recording in list(self._active.items()):
            if recording.event["camera_id"] != camera_id:
                continue
            if self._disk_usage() >= self.max_disk_bytes:
                self._mark_incomplete(event_id, "evidence disk limit reached during recording")
                continue
            self._append_encoded(recording, timestamp, encoded_bytes, identity)
            trigger = float(recording.event["triggered_at"])
            if recording.preview_path is None and timestamp >= trigger + self.preview_seconds:
                preview = recording.directory / "preview.jpg"
                if cv2.imwrite(str(preview), image):
                    recording.preview_path = preview
                    self._write_manifest(recording)
                    self._updates.append({"event_id": event_id, "preview_path": str(preview), "recording_status": "recording"})
            if timestamp >= trigger + self.post_seconds:
                self._finalize(event_id)

    def start(self, event: dict[str, Any]) -> dict[str, Any]:
        if len(self._active) >= self.max_inflight:
            return {"ok": False, "reason": "evidence inflight limit reached"}
        if self._disk_usage() >= self.max_disk_bytes:
            return {"ok": False, "reason": "evidence disk limit reached"}
        event_id = str(event["id"])
        if event_id in self._active or (self.root / event_id).exists():
            return {"ok": False, "reason": "event evidence already exists"}
        directory = self.root / event_id
        (directory / "frames").mkdir(parents=True)
        recording = _Recording(dict(event), directory)
        self._active[event_id] = recording
        cutoff = float(event["triggered_at"]) - self.pre_seconds
        predecessor: tuple[float, bytes, dict[str, Any]] | None = None
        cached: list[tuple[float, bytes, dict[str, Any]]] = []
        for timestamp, encoded, identity in self._cache.get(str(event["camera_id"]), ()):
            if timestamp <= cutoff:
                if predecessor is None or timestamp >= predecessor[0]:
                    predecessor = (timestamp, encoded, identity)
            else:
                cached.append((timestamp, encoded, identity))
        boundary_tolerance = 0.5 / self.fps
        following_distance = cached[0][0] - cutoff if cached else float("inf")
        if (
            predecessor is not None
            and cutoff - predecessor[0] <= boundary_tolerance
            and cutoff - predecessor[0] < following_distance
        ):
            cached.insert(0, predecessor)
        if not cached or abs(cached[0][0] - cutoff) > boundary_tolerance:
            # The first retained frame may arrive after the trigger. Keep the
            # entire unavailable prefix open until that frame is on disk.
            recording.open_gap_start = cutoff
            recording.gaps.append([cutoff, None])
            recording.gap_reasons.append({"start": cutoff, "end": None, "reason": "requested pre-event window unavailable"})
        for timestamp, encoded, identity in cached:
            self._append_encoded(recording, timestamp, encoded, identity)
        self._write_manifest(recording)
        return {"ok": True, "reason": "recording", "evidence_path": str(directory)}

    def drain_updates(self) -> list[dict[str, Any]]:
        result = list(self._updates)
        self._updates.clear()
        return result

    def note_gap(self, camera_id: str, timestamp: float, reason: str) -> None:
        for recording in self._active.values():
            if recording.event["camera_id"] != camera_id:
                continue
            if recording.open_gap_start is None:
                recording.open_gap_start = timestamp
                recording.gap_reasons.append({"start": timestamp, "end": None, "reason": reason})
                self._write_manifest(recording)

    def close(self) -> list[dict[str, Any]]:
        for event_id in list(self._active):
            self._mark_incomplete(event_id, "runtime stopped before post-event capture completed")
        return self.drain_updates()

    def _append_frame(self, recording: _Recording, timestamp: float, image: np.ndarray) -> None:
        encoded_ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
        self._append_encoded(recording, timestamp, encoded.tobytes() if encoded_ok else b"")

    def _append_encoded(self, recording: _Recording, timestamp: float, encoded: bytes, identity: dict[str, Any] | None = None) -> None:
        frame_path = recording.directory / "frames" / f"{len(recording.frames):08d}-{timestamp:.6f}.jpg"
        if not encoded:
            recording.gaps.append([timestamp, timestamp])
            return
        frame_path.write_bytes(encoded)
        if recording.open_gap_start is not None:
            if recording.gaps and recording.gaps[-1] == [recording.open_gap_start, None]:
                recording.gaps[-1][1] = timestamp
            else:
                recording.gaps.append([recording.open_gap_start, timestamp])
            recording.gap_reasons[-1]["end"] = timestamp
            recording.open_gap_start = None
        if recording.last_timestamp is not None:
            expected = 1.0 / self.fps
            if timestamp - recording.last_timestamp > max(expected * 1.75, 0.25):
                recording.gaps.append([recording.last_timestamp, timestamp])
        recording.frames.append((timestamp, frame_path))
        recording.source_frames.append({
            "run_id": (identity or {}).get("run_id"),
            "frame_id": (identity or {}).get("frame_id"),
            "camera_id": recording.event["camera_id"],
            "timestamp": timestamp, "file": frame_path.name,
        })
        recording.last_timestamp = timestamp
        self._write_manifest(recording)

    def _write_manifest(self, recording: _Recording, status: str = "recording", reason: str | None = None) -> None:
        payload = {
            "event_id": recording.event["id"],
            "camera_id": recording.event["camera_id"],
            "triggered_at": recording.event["triggered_at"],
            "recording_status": status,
            "window_complete": (not recording.gaps) if status == "complete" else (False if status == "incomplete" else None),
            "frame_count": len(recording.frames),
            "source_frames": recording.source_frames,
            "last_timestamp": recording.last_timestamp,
            "gaps": recording.gaps,
            "gap_reasons": recording.gap_reasons,
            "preview_path": str(recording.preview_path) if recording.preview_path else None,
            "reason": reason,
        }
        _atomic_json(recording.directory / "manifest.json", payload)

    def _finalize(self, event_id: str) -> None:
        recording = self._active[event_id]
        if not recording.frames:
            self._mark_incomplete(event_id, "no frames captured")
            return
        first = cv2.imread(str(recording.frames[0][1]))
        if first is None:
            self._mark_incomplete(event_id, "cached evidence could not be decoded")
            return
        height, width = first.shape[:2]
        clip_path = recording.directory / "evidence.mp4"
        writer = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (width, height))
        if not writer.isOpened():
            self._mark_incomplete(event_id, "video encoder unavailable")
            return
        previous_timestamp: float | None = None
        for index, (timestamp, path) in enumerate(recording.frames):
            frame = first if index == 0 else cv2.imread(str(path))
            if frame is None:
                writer.release()
                self._mark_incomplete(
                    event_id,
                    f"evidence frame decode failed: {path.name}; partial clip retained at {clip_path.name}",
                    evidence_path=clip_path,
                )
                return
            if frame.shape[:2] != (height, width):
                frame = cv2.resize(frame, (width, height))
            cv2.putText(frame, f"timestamp {timestamp:.3f}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
            if previous_timestamp is not None and timestamp - previous_timestamp > max(1.0 / self.fps * 1.75, 0.25):
                cv2.putText(frame, f"GAP {timestamp - previous_timestamp:.1f}s", (8, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
            writer.write(frame)
            previous_timestamp = timestamp
        writer.release()
        if recording.preview_path is None:
            preview = recording.directory / "preview.jpg"
            shutil.copy2(recording.frames[-1][1], preview)
            recording.preview_path = preview
        self._write_manifest(recording, "complete")
        self._updates.append(
            {
                "event_id": event_id,
                "recording_status": "complete",
                "status": "complete",
                "evidence_path": str(clip_path),
                "preview_path": str(recording.preview_path),
                "gaps": recording.gaps,
                "window_complete": not recording.gaps,
                "completed_at": time.time(),
            }
        )
        del self._active[event_id]

    def _mark_incomplete(self, event_id: str, reason: str, *, evidence_path: Path | None = None) -> None:
        recording = self._active[event_id]
        if recording.last_timestamp is not None:
            recording.gaps.append([recording.last_timestamp, None])
        if recording.open_gap_start is not None:
            if [recording.open_gap_start, None] not in recording.gaps:
                recording.gaps.append([recording.open_gap_start, None])
            recording.gap_reasons[-1]["end"] = None
        self._write_manifest(recording, "incomplete", reason)
        self._updates.append(
            {
                "event_id": event_id,
                "recording_status": "incomplete",
                "status": "incomplete",
                "evidence_path": str(evidence_path or recording.directory),
                "preview_path": str(recording.preview_path) if recording.preview_path else None,
                "gaps": recording.gaps,
                "window_complete": False,
                "completed_at": time.time(),
                "reason": reason,
            }
        )
        del self._active[event_id]

    def _disk_usage(self) -> int:
        total = 0
        pending = [self.root]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                            elif entry.is_file():
                                # Match Path.is_file() for a symlink to a regular file.
                                total += entry.stat().st_size
                        except (FileNotFoundError, NotADirectoryError):
                            # Retention or an external actor removed the entry during this scan.
                            continue
            except (FileNotFoundError, NotADirectoryError):
                continue
        return total

    def _trim_cache(self) -> None:
        while self._cache_bytes > self._max_cache_bytes:
            oldest_camera = None
            oldest_timestamp = float("inf")
            for camera_id, frames in self._cache.items():
                if frames and frames[0][0] < oldest_timestamp:
                    oldest_camera, oldest_timestamp = camera_id, frames[0][0]
            if oldest_camera is None:
                break
            _, expired, _ = self._cache[oldest_camera].popleft()
            self._cache_bytes -= len(expired)


def recover_interrupted_evidence(evidence_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(evidence_dir)
    recovered: list[dict[str, Any]] = []
    if not root.exists():
        return recovered
    for manifest_path in root.glob("*/manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("recording_status") != "recording":
            continue
        payload["recording_status"] = "incomplete"
        payload["window_complete"] = False
        payload["reason"] = "interrupted recording recovered at startup"
        gaps = list(payload.get("gaps") or [])
        if not gaps or gaps[-1][1] is not None:
            gaps.append([payload.get("last_timestamp"), None])
        payload["gaps"] = gaps
        payload["completed_at"] = time.time()
        _atomic_json(manifest_path, payload)
        recovered.append(payload)
    return recovered


def terminalize_incomplete_event(
    evidence_dir: str | Path,
    event: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Persist an event-level shutdown failure after its worker is no longer alive."""
    event_id = str(event["id"])
    directory = Path(evidence_dir) / event_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    payload: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    gaps = list(payload.get("gaps") or event.get("gaps") or [])
    gap_start = payload.get("last_timestamp")
    if gap_start is None:
        gap_start = event.get("triggered_at")
    terminal_gap = [gap_start, None]
    if not gaps or gaps[-1] != terminal_gap:
        gaps.append(terminal_gap)
    gap_reasons = list(payload.get("gap_reasons") or [])
    gap_reasons.append({"start": gap_start, "end": None, "reason": reason})
    completed_at = time.time()
    payload.update(
        {
            "event_id": event_id,
            "camera_id": event.get("camera_id"),
            "triggered_at": event.get("triggered_at"),
            "recording_status": "incomplete",
            "window_complete": False,
            "frame_count": int(payload.get("frame_count") or 0),
            "last_timestamp": payload.get("last_timestamp"),
            "gaps": gaps,
            "gap_reasons": gap_reasons,
            "preview_path": payload.get("preview_path") or event.get("preview_path"),
            "reason": reason,
            "completed_at": completed_at,
        }
    )
    _atomic_json(manifest_path, payload)
    return {
        "evidence_path": str(directory),
        "preview_path": payload.get("preview_path"),
        "gaps": gaps,
        "window_complete": False,
        "reason": reason,
        "completed_at": completed_at,
    }
