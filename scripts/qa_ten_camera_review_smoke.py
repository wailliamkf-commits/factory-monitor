#!/usr/bin/env python3
"""Measure one real local-Qwen review queue over simultaneous synthetic facts."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psutil

from factory_monitor.config import save_config
from factory_monitor.runtime import RuntimeController
from factory_monitor.store import EventStore
from qa_ten_camera_smoke import _config


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


def _peak_rss(process: psutil.Process) -> int:
    total = 0
    for candidate in [process, *process.children(recursive=True)]:
        try:
            total += candidate.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def run(root: Path, seconds: float) -> dict[str, Any]:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    data_dir = root / stamp
    if data_dir.exists():
        raise RuntimeError(f"refusing to reuse existing QA evidence directory: {data_dir}")
    data_dir.mkdir(parents=True)
    config = _config()
    config["review"].update({"enabled": True, "endpoint": "http://127.0.0.1:11435", "timeout_seconds": 15})
    # At 2 FPS this allows six real timestamped camera frames to accumulate
    # before all ten deterministic synthetic absence candidates enter review.
    for camera in config["cameras"]:
        camera["absence_seconds"] = 4
    save_config(config, data_dir / "synthetic-ten-camera-review-config.json")

    controller = RuntimeController(config, data_dir)
    parent = psutil.Process(os.getpid())
    events: list[dict[str, Any]] = []
    peak_rss = 0
    try:
        controller.start("demo")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            events.extend(controller.poll())
            peak_rss = max(peak_rss, _peak_rss(parent))
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    finally:
        controller.stop()
        events.extend(controller.poll())

    candidates = [event["event"] for event in events if event.get("type") == "candidate"]
    if len(candidates) != 10:
        raise RuntimeError(f"expected ten simultaneous synthetic candidates, found {len(candidates)}")
    spread = max(event["triggered_at"] for event in candidates) - min(event["triggered_at"] for event in candidates)
    store = EventStore(data_dir / "events.sqlite3")
    try:
        rows = store.list_events(limit=100)
    finally:
        store.close()
    if len(rows) != 10:
        raise RuntimeError(f"expected ten SQLite review rows, found {len(rows)}")
    pending = [row["id"] for row in rows if row.get("analysis_status") == "pending"]
    statuses = Counter(str(row.get("analysis_status")) for row in rows)
    terminal_latencies = [float(row["latency_ms"]) for row in rows if isinstance(row.get("latency_ms"), (int, float)) and math.isfinite(float(row["latency_ms"]))]
    successful_rows = [row for row in rows if row.get("analysis_status") in {"supported", "dismissed", "uncertain"}]
    successful_latencies = [float(row["latency_ms"]) for row in successful_rows if isinstance(row.get("latency_ms"), (int, float)) and math.isfinite(float(row["latency_ms"]))]
    timeout_count = statuses["timeout"]
    completed_within_15 = sum(
        row.get("analysis_status") in {"supported", "dismissed", "uncertain"}
        and isinstance(row.get("latency_ms"), (int, float))
        and float(row["latency_ms"]) <= 15_000
        for row in rows
    )
    report = {
        "ok": not pending,
        "generated_at": time.time(),
        "base_repository_revision": _revision(),
        "revision_provenance": _revision_provenance(),
        "data_dir": str(data_dir),
        "source": "demo",
        "synthetic_candidate_facts": True,
        "review_request": {"endpoint": config["review"]["endpoint"], "model": config["review"]["model"], "timeout_seconds": 15, "ordered_frames_per_request": 6},
        "candidate_count": len(candidates),
        "candidate_spread_seconds": spread,
        "analysis_status_counts": dict(sorted(statuses.items())),
        "timeout_count": timeout_count,
        "pending_count": len(pending),
        "sla_sample_denominator": len(rows),
        "sla_failure_count": sum(row.get("analysis_status") in {"timeout", "error"} for row in rows),
        "successful_review_measured_denominator": len(successful_latencies),
        "successful_review_p95_measured_ms": _p95(successful_latencies),
        "censored_timeout_count": timeout_count,
        "terminal_notification_latency_denominator": len(terminal_latencies),
        "terminal_notification_p95_ms": _p95(terminal_latencies),
        "completed_within_15000ms": completed_within_15,
        "peak_runtime_tree_rss_bytes": peak_rss,
        "event_counts": dict(sorted(Counter(event.get("type", "unknown") for event in events).items())),
        "field_gate": {"status": "FAIL", "reasons": ["real local model requests over synthetic candidates do not establish field accuracy or production readiness"]},
    }
    (data_dir / "qa-ten-camera-review-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real loopback Qwen queue smoke over ten synthetic candidates")
    parser.add_argument("--root", type=Path, default=Path("artifacts/qa-ten-camera-review"))
    parser.add_argument("--seconds", type=float, default=31.0, help="about 25 seconds after candidate enqueue")
    args = parser.parse_args()
    if not 28 <= args.seconds <= 45:
        parser.error("--seconds must be 28..45")
    try:
        report = run(args.root, args.seconds)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
