#!/usr/bin/env python3
"""Bounded local-Ollama concurrency probe over synthetic six-frame reviews.

This is a component diagnostic, not a runtime, field, or accuracy acceptance.
It deliberately sends ten requests at once and gives every request the same
queue-inclusive 15-second deadline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import cv2
import httpx
import psutil

from factory_monitor.inference import LocalReviewError, OllamaReviewer


def _revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _load_frames(frame_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    paths = sorted(frame_dir.glob("*.jpg"))[:6]
    if len(paths) != 6:
        raise RuntimeError(f"expected six synthetic JPEG frames in {frame_dir}, found {len(paths)}")
    frames: list[dict[str, Any]] = []
    for index, path in enumerate(paths):
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError(f"could not read synthetic frame: {path}")
        try:
            timestamp = float(path.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            timestamp = 1_700_000_000.0 + index * 0.5
        frames.append({"timestamp": timestamp, "image": image})
    return frames, [str(path) for path in paths]


def _api_json(endpoint: str, route: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(trust_env=False, timeout=2.0) as client:
            response = client.get(f"{endpoint.rstrip('/')}{route}")
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else None
    except (httpx.HTTPError, ValueError):
        return None


def _process_snapshot(root_pid: int) -> dict[str, Any]:
    root = psutil.Process(root_pid)
    processes = [root, *root.children(recursive=True)]
    rss = 0
    commands: list[str] = []
    for process in processes:
        try:
            rss += process.memory_info().rss
            commands.append(" ".join(process.cmdline()))
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return {"tree_rss_bytes": rss, "tree_cpu_percent": None, "commands": commands}


def _effective_parallelism(commands: list[str]) -> int | None:
    for command in commands:
        match = re.search(r"(?:^|\s)-np\s+(\d+)(?:\s|$)", command)
        if match:
            return int(match.group(1))
    return None


def _run_one(
    request_id: int,
    reviewer: OllamaReviewer,
    frames: list[dict[str, Any]],
    context: dict[str, Any],
    release: threading.Event,
    batch_deadline: list[float],
) -> dict[str, Any]:
    release.wait()
    started = time.monotonic()
    deadline = batch_deadline[0]
    try:
        result = reviewer.review(frames, deadline=deadline, context=context)
        return {
            "request_id": request_id,
            "status": "completed",
            "schema_valid": True,
            "decision": result["decision"],
            "duration_ms": (time.monotonic() - started) * 1000,
        }
    except TimeoutError as exc:
        return {
            "request_id": request_id,
            "status": "timeout",
            "schema_valid": False,
            "error": str(exc),
            "duration_ms": (time.monotonic() - started) * 1000,
            "right_censored_at_ms": 15_000,
        }
    except LocalReviewError as exc:
        return {
            "request_id": request_id,
            "status": "error",
            "schema_valid": False,
            "error": str(exc),
            "duration_ms": (time.monotonic() - started) * 1000,
        }
    except Exception as exc:  # evidence should retain unexpected local failures
        return {
            "request_id": request_id,
            "status": "error",
            "schema_valid": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": (time.monotonic() - started) * 1000,
        }


def run(args: argparse.Namespace) -> dict[str, Any]:
    frames, frame_paths = _load_frames(args.frames)
    context = {
        "camera_id": "camera-01",
        "kind": "station_absence",
        "reason": "synthetic station ROI unoccupied for configured threshold",
        "material_roi": [0.0, 0.0, 1.0, 1.0],
        "station_roi": [0.0, 0.0, 1.0, 1.0],
        "exit_line": [[0.8, 0.0], [0.8, 1.0]],
        "absence_threshold_seconds": 4,
        "observed_duration_seconds": 4,
    }
    reviewer = OllamaReviewer(args.endpoint, args.model, timeout_seconds=15)
    prewarm_started = time.monotonic()
    prewarm: dict[str, Any]
    try:
        warm_reviewer = OllamaReviewer(args.endpoint, args.model, timeout_seconds=45)
        warm_result = warm_reviewer.review(
            frames,
            deadline=time.monotonic() + 45,
            context=context,
        )
        prewarm = {
            "attempted": True,
            "succeeded": True,
            "schema_valid": True,
            "decision": warm_result["decision"],
            "duration_ms": (time.monotonic() - prewarm_started) * 1000,
        }
    except Exception as exc:
        prewarm = {
            "attempted": True,
            "succeeded": False,
            "schema_valid": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": (time.monotonic() - prewarm_started) * 1000,
        }
        raise RuntimeError(f"prewarm failed; refusing contaminated main trial: {exc}") from exc

    before_ps = _api_json(args.endpoint, "/api/ps")
    root = psutil.Process(args.server_pid)
    release = threading.Event()
    batch_deadline = [0.0]
    results: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10, thread_name_prefix="review-probe") as pool:
        futures = [
            pool.submit(_run_one, index, reviewer, frames, context, release, batch_deadline)
            for index in range(10)
        ]
        batch_started = time.monotonic()
        batch_deadline[0] = batch_started + 15.0
        release.set()
        while any(not future.done() for future in futures):
            try:
                process_sample = _process_snapshot(args.server_pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                process_sample = {"tree_rss_bytes": None, "tree_cpu_percent": None, "commands": []}
            memory = psutil.virtual_memory()
            samples.append(
                {
                    "offset_ms": (time.monotonic() - batch_started) * 1000,
                    "server_tree_rss_bytes": process_sample["tree_rss_bytes"],
                    "server_tree_cpu_percent": process_sample["tree_cpu_percent"],
                    "system_available_memory_bytes": memory.available,
                }
            )
            time.sleep(0.1)
        for future in as_completed(futures):
            results.append(future.result())
        batch_duration_ms = (time.monotonic() - batch_started) * 1000

    final_snapshot = _process_snapshot(args.server_pid)
    after_ps = _api_json(args.endpoint, "/api/ps")
    results.sort(key=lambda item: item["request_id"])
    completed = [item for item in results if item["status"] == "completed"]
    timeouts = [item for item in results if item["status"] == "timeout"]
    errors = [item for item in results if item["status"] == "error"]
    rss_values = [item["server_tree_rss_bytes"] for item in samples if item["server_tree_rss_bytes"] is not None]
    cpu_values = [item["server_tree_cpu_percent"] for item in samples if item["server_tree_cpu_percent"] is not None]
    available_values = [item["system_available_memory_bytes"] for item in samples]
    report = {
        "generated_at": time.time(),
        "repository_revision": _revision(),
        "scope": "local Ollama/Qwen review component diagnostic over synthetic frames",
        "not_evidence_for": ["full runtime throughput", "field accuracy", "camera ingest", "production acceptance"],
        "host": {"platform": os.uname().sysname, "machine": os.uname().machine, "logical_cpu_count": psutil.cpu_count(), "memory_bytes": psutil.virtual_memory().total},
        "server": {
            "endpoint": args.endpoint,
            "pid": args.server_pid,
            "requested_parallelism": args.parallel,
            "effective_runner_parallelism": _effective_parallelism(final_snapshot["commands"]),
            "effective_parallelism_evidence": "parsed from the runner command's -np value",
            "version": _api_json(args.endpoint, "/api/version"),
            "process_commands_after_trial": final_snapshot["commands"],
            "api_ps_before": before_ps,
            "api_ps_after": after_ps,
        },
        "request_contract": {
            "model": args.model,
            "request_count": 10,
            "simultaneous_release": True,
            "queue_inclusive_deadline_ms": 15_000,
            "ordered_frames_per_request": 6,
            "frame_source": "existing synthetic ten-camera smoke evidence",
            "frame_paths": frame_paths,
            "context": context,
            "reviewer": "factory_monitor.inference.OllamaReviewer",
        },
        "prewarm": prewarm,
        "outcomes": {
            "completed_schema_valid": len(completed),
            "timeouts_right_censored": len(timeouts),
            "errors": len(errors),
            "completed_within_15000ms": sum(item["duration_ms"] <= 15_000 for item in completed),
            "batch_duration_ms": batch_duration_ms,
            "requests": results,
        },
        "resource_observations": {
            "sample_interval_ms": 100,
            "sample_count": len(samples),
            "peak_server_tree_rss_bytes": max(rss_values) if rss_values else None,
            "peak_server_tree_cpu_percent": max(cpu_values) if cpu_values else None,
            "cpu_percent_note": "Not collected; the bounded probe prioritizes memory and deadline outcomes.",
            "minimum_system_available_memory_bytes": min(available_values) if available_values else None,
            "ollama_reported_model_vram_bytes": (
                before_ps.get("models", [{}])[0].get("size_vram")
                if before_ps and before_ps.get("models")
                else None
            ),
            "gpu_utilization_percent": None,
            "gpu_utilization_note": "Unavailable from unprivileged macOS process/API telemetry; no value inferred.",
        },
        "interpretation_guardrail": "Do not attribute a comparison gain to parallelism: the effective runner had one slot, while this trial was prewarmed and repeated identical inputs that maximized prompt-cache reuse.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11436")
    parser.add_argument("--model", default="qwen3-vl:2b-instruct")
    parser.add_argument("--parallel", type=int, choices=(2, 4), default=4)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/review-capacity-diagnosis.json"))
    args = parser.parse_args()
    try:
        report = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
