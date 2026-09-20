#!/usr/bin/env python3
"""Real multiprocess synthetic evidence-loop smoke test.

This creates only timestamped artifacts under the selected QA root. It uses
the runtime's synthetic source and turns local review off, so its JSON report
is software-loop evidence only and always marks field acceptance as unproven.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import cv2

from factory_monitor.config import default_config, save_config
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore


def _revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _configured_synthetic() -> dict[str, Any]:
    config = default_config()
    config["source"].update(
        {
            "calibrated": True,
            "expected_size": [960, 540],
            "window_title": "SYNTHETIC QA FIXTURE — NOT A LIVE CLIENT",
        }
    )
    # Two FPS lets CAM01 enter its material ROI after roughly 30 seconds,
    # retaining a genuine 30-second pre-event cache before 60 seconds post.
    config["detection"]["fps"] = 2
    config["review"]["enabled"] = False
    camera = config["cameras"][0]
    camera["material_roi"] = [[0.83, 0.70], [0.95, 0.70], [0.95, 1.0], [0.83, 1.0]]
    camera["station_roi"] = [[0.05, 0.05], [0.35, 0.05], [0.35, 0.35], [0.05, 0.35]]
    camera["absence_seconds"] = 2  # Accelerated, explicitly synthetic fixture only.
    camera["schedule"] = {"days": list(range(7)), "active": [["00:00", "24:00"]], "breaks": []}
    return config


def _wait(controller: RuntimeController, seconds: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + seconds
    events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        events.extend(controller.poll())
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    events.extend(controller.poll())
    return events


def _media_metrics(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"evidence is not playable by OpenCV: {path}")
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    first_ok, _ = capture.read()
    capture.release()
    if not first_ok or frames <= 0 or fps <= 0:
        raise RuntimeError(f"evidence has no decodable frames: {path}")
    return {"path": str(path), "frame_count": frames, "fps": fps, "duration_seconds": frames / fps}


def _assert_primary_events(store_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    store = EventStore(store_path)
    try:
        events = store.list_events(limit=100)
    finally:
        store.close()
    primary = [event for event in events if event["kind"] in {"material_candidate", "station_absence"}]
    kinds = {event["kind"] for event in primary}
    if kinds != {"material_candidate", "station_absence"}:
        raise RuntimeError(f"expected both synthetic rule candidates, found {sorted(kinds)}")
    completed = [event for event in primary if event["status"] == "complete"]
    if len(completed) < 2:
        raise RuntimeError(f"expected completed evidence for both candidates, found {[event['status'] for event in primary]}")
    return primary, completed


def run(root: Path, duration_seconds: float) -> dict[str, Any]:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    data_dir = root / stamp
    if data_dir.exists():
        raise RuntimeError(f"refusing to reuse existing QA artifacts: {data_dir}")
    data_dir.mkdir(parents=True)
    config = _configured_synthetic()
    config_path = data_dir / "synthetic-calibrated-config.json"
    save_config(config, config_path)
    started = time.time()
    controller = RuntimeController(config, data_dir)
    first_events: list[dict[str, Any]] = []
    try:
        controller.start("demo")
        first_events = _wait(controller, duration_seconds)
    finally:
        controller.stop()
        first_events.extend(controller.poll())

    primary, completed = _assert_primary_events(data_dir / "events.sqlite3")
    media = []
    manifests = []
    for event in completed:
        evidence_path = Path(event["evidence_path"])
        if evidence_path.suffix != ".mp4" or not evidence_path.is_file():
            raise RuntimeError(f"completed event lacks an MP4: {event['id']}")
        preview = Path(event["preview_path"])
        if not preview.is_file():
            raise RuntimeError(f"completed event lacks a preview: {event['id']}")
        media.append({"event_id": event["id"], **_media_metrics(evidence_path)})
        manifest_path = evidence_path.parent / "manifest.json"
        manifests.append({"event_id": event["id"], **json.loads(manifest_path.read_text(encoding="utf-8"))})

    # A second bounded run makes an actual persisted in-flight event, then
    # stops before its post window. A third controller startup must retain it.
    interrupted = RuntimeController(config, data_dir)
    try:
        interrupted.start("demo")
        _wait(interrupted, 4.0)
    finally:
        interrupted.stop()
        _wait(interrupted, 0.0)
    restarted = RuntimeController(config, data_dir)
    if restarted.status()["running"]:
        raise RuntimeError("fresh controller unexpectedly started during recovery readback")
    store = EventStore(data_dir / "events.sqlite3")
    try:
        recovered_events = store.list_events(limit=100)
    finally:
        store.close()
    incomplete = [event for event in recovered_events if event["status"] == "incomplete"]
    if not incomplete:
        raise RuntimeError("restart lost or failed to mark the intentional interrupted event")

    material_media = next(item for item in media if next(event for event in completed if event["id"] == item["event_id"])["kind"] == "material_candidate")
    if material_media["duration_seconds"] < 85:
        raise RuntimeError(f"material evidence duration too short for 30s pre/60s post representative: {material_media['duration_seconds']:.1f}s")
    station = next(event for event in completed if event["kind"] == "station_absence")
    material = next(event for event in completed if event["kind"] == "material_candidate")
    recordings_overlapped = material["triggered_at"] < station["completed_at"]
    if not recordings_overlapped:
        raise RuntimeError("synthetic material and absence recordings did not overlap")
    report = {
        "ok": True,
        "generated_at": time.time(),
        "started_at": started,
        "repository_revision": _revision(),
        "data_dir": str(data_dir),
        "source": "demo",
        "synthetic": True,
        "review_enabled": False,
        "event_counts": dict(sorted(Counter(event.get("type", "unknown") for event in first_events).items())),
        "candidate_events": [{key: value for key, value in event.items() if key not in {"analysis"}} for event in primary],
        "media": media,
        "manifests": manifests,
        "recordings_overlapped": recordings_overlapped,
        "interrupted_event_count_after_restart": len(incomplete),
        "field_gate": {"status": "FAIL", "reasons": ["synthetic QA smoke validates local software flow only"]},
    }
    (data_dir / "qa-runtime-smoke-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated multiprocess synthetic QA smoke evidence loop")
    parser.add_argument("--root", type=Path, default=Path("artifacts/qa-smoke"))
    parser.add_argument("--seconds", type=float, default=94.0, help="must cover synthetic 30s pre + 60s post material evidence")
    args = parser.parse_args()
    if args.seconds < 90 or args.seconds > 120:
        parser.error("--seconds must be between 90 and 120 for the representative retention check")
    try:
        print(json.dumps(run(args.root, args.seconds), ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
