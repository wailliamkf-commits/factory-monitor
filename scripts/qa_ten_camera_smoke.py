#!/usr/bin/env python3
"""Actual-duration, ten-camera synthetic evidence-capacity smoke test.

This is deliberately not a benchmark of real detection or local-model review.
It drives the real multiprocess runtime with the built-in labelled synthetic
source and verifies simultaneous absence candidates and their evidence files.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import psutil

from factory_monitor.config import default_config, save_config
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore


def _revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _revision_provenance() -> str:
    try:
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return "working-tree state unavailable"
    return "base commit plus working-tree changes" if dirty.strip() else "exact clean base commit"


def _config() -> dict[str, Any]:
    config = default_config()
    config["source"].update(
        {
            "calibrated": True,
            "expected_size": [960, 540],
            "window_title": "SYNTHETIC TEN-CAMERA QA FIXTURE — NOT A LIVE CLIENT",
        }
    )
    config["detection"]["fps"] = 2
    config["review"]["enabled"] = False
    for camera in config["cameras"]:
        camera["material_roi"] = []
        camera["exit_line"] = []
        # The scripted person walks along each tile's lower edge, so this top
        # ROI remains deliberately unoccupied.  31 seconds is fixture-only.
        camera["station_roi"] = [[0.05, 0.05], [0.35, 0.05], [0.35, 0.35], [0.05, 0.35]]
        camera["absence_seconds"] = 31
        camera["schedule"] = {"days": list(range(7)), "active": [["00:00", "24:00"]], "breaks": []}
    return config


def _peak_rss(process: psutil.Process) -> int:
    total = 0
    for candidate in [process, *process.children(recursive=True)]:
        try:
            total += candidate.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total


def _media(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open evidence: {path}")
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    decoded, _ = capture.read()
    capture.release()
    if not decoded or frames <= 0 or fps <= 0:
        raise RuntimeError(f"evidence has no decodable frames: {path}")
    return {"path": str(path), "frame_count": frames, "fps": fps, "duration_seconds": frames / fps}


def run(root: Path, duration_seconds: float) -> dict[str, Any]:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    data_dir = root / stamp
    if data_dir.exists():
        raise RuntimeError(f"refusing to reuse existing QA evidence directory: {data_dir}")
    data_dir.mkdir(parents=True)
    config = _config()
    save_config(config, data_dir / "synthetic-ten-camera-config.json")
    if config["evidence"]["max_inflight"] != 10:
        raise RuntimeError("smoke expects V1 max_inflight=10")

    controller = RuntimeController(config, data_dir)
    parent = psutil.Process(os.getpid())
    events: list[dict[str, Any]] = []
    peak_rss = 0
    started = time.time()
    try:
        controller.start("demo")
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            events.extend(controller.poll())
            peak_rss = max(peak_rss, _peak_rss(parent))
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        # Frames have run for the complete 94-second window. Drain a bounded
        # queue/finalization period; it does not fake shorter event timing.
        settle_deadline = time.monotonic() + 5.0
        while time.monotonic() < settle_deadline:
            events.extend(controller.poll())
            peak_rss = max(peak_rss, _peak_rss(parent))
            time.sleep(0.1)
    finally:
        controller.stop()
        events.extend(controller.poll())

    errors = [{key: value for key, value in event.items() if key != "image"} for event in events if event.get("type") == "error"]
    if errors:
        raise RuntimeError(f"runtime surfaced errors: {errors}")
    candidates = [event["event"] for event in events if event.get("type") == "candidate"]
    if len(candidates) != 10 or {event["camera_id"] for event in candidates} != {f"CAM{index:02d}" for index in range(1, 11)}:
        raise RuntimeError(f"expected one candidate for every camera, found {[event['camera_id'] for event in candidates]}")
    spread = max(event["triggered_at"] for event in candidates) - min(event["triggered_at"] for event in candidates)
    if spread > 1.0:
        raise RuntimeError(f"simultaneous candidate spread exceeded one second: {spread:.3f}")

    store = EventStore(data_dir / "events.sqlite3")
    try:
        rows = store.list_events(limit=100)
    finally:
        store.close()
    if len(rows) != 10 or any(row["status"] != "complete" or row.get("recording_status") != "complete" for row in rows):
        raise RuntimeError(f"not all evidence rows completed: {[(row['camera_id'], row['status']) for row in rows]}")
    media = []
    for row in rows:
        if row.get("gaps"):
            raise RuntimeError(f"unexpected evidence gap for {row['camera_id']}: {row['gaps']}")
        clip = Path(row.get("evidence_path", ""))
        preview = Path(row.get("preview_path", ""))
        if not clip.is_file() or not preview.is_file():
            raise RuntimeError(f"missing clip/preview for {row['camera_id']}")
        metrics = _media(clip)
        if metrics["duration_seconds"] < 88:
            raise RuntimeError(f"clip too short for default 30s pre/60s post verification: {metrics['duration_seconds']:.1f}s")
        media.append({"camera_id": row["camera_id"], **metrics})
    report = {
        "ok": True,
        "generated_at": time.time(),
        "base_repository_revision": _revision(),
        "revision_provenance": _revision_provenance(),
        "data_dir": str(data_dir),
        "source": "demo",
        "synthetic": True,
        "review_enabled": False,
        "fixture_absence_seconds": 31,
        "evidence_window_seconds": {"pre": 30, "post": 60, "preview": 30},
        "inflight_limit": config["evidence"]["max_inflight"],
        "candidate_count": len(candidates),
        "candidate_spread_seconds": spread,
        "event_counts": dict(sorted(Counter(event.get("type", "unknown") for event in events).items())),
        "peak_runtime_tree_rss_bytes": peak_rss,
        "sqlite_event_count": len(rows),
        "media": sorted(media, key=lambda item: item["camera_id"]),
        "errors": errors,
        "field_gate": {"status": "FAIL", "reasons": ["synthetic capacity smoke is not detection, review-latency, or field-acceptance evidence"]},
    }
    (data_dir / "qa-ten-camera-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run actual-duration synthetic ten-camera capacity smoke")
    parser.add_argument("--root", type=Path, default=Path("artifacts/qa-ten-camera"))
    parser.add_argument("--seconds", type=float, default=94.0)
    args = parser.parse_args()
    if not 94 <= args.seconds <= 105:
        parser.error("--seconds must be 94..105 for the default pre/post evidence window")
    try:
        print(json.dumps(run(args.root, args.seconds), ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
