#!/usr/bin/env python3
"""Compare original and shared YOLO detectors on ten derived public still crops.

Each implementation runs in its own process. This measures only detector component
latency and process RSS; it does not measure live feeds, recognition accuracy,
capture, review, recording, or the 15-second event deadline.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
BASELINE_REF = "0de7f2e"
BASELINE_PATH = "src/factory_monitor/inference.py"
DEFAULT_MODEL = Path("/Users/wailliam/Documents/factory-monitor/models/yolo11n.pt")
DEFAULT_IMAGE = Path("/Users/wailliam/Documents/factory-monitor/artifacts/public-bus.jpg")
DEFAULT_OUTPUT = ROOT / "reports/local/mainland-20260925/detector-benchmark.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0 <= fraction <= 1:
        raise ValueError("percentile requires samples and a fraction from zero to one")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    left = math.floor(rank)
    right = math.ceil(rank)
    return ordered[left] + (ordered[right] - ordered[left]) * (rank - left)


def make_views(image: Any, *, count: int = 10) -> dict[str, Any]:
    """Make distinct, deterministic 4:3 crops; all output arrays are 320x240 BGR."""
    import cv2
    import numpy as np

    if count < 2 or image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("expected a BGR image and at least two camera views")
    height, width = image.shape[:2]
    crop_width = min(int(width * 0.94), int(height * 4 / 3))
    crop_height = int(crop_width * 3 / 4)
    if crop_width < 320 or crop_height < 240:
        raise ValueError("public source image is too small for ten distinct 320x240 views")
    max_x, max_y = width - crop_width, height - crop_height
    views: dict[str, Any] = {}
    for index in range(count):
        x = round(max_x * ((index * 3) % count) / (count - 1))
        y = round(max_y * index / (count - 1))
        cropped = image[y : y + crop_height, x : x + crop_width]
        views[f"CAM{index + 1:02d}"] = cv2.resize(cropped, (320, 240), interpolation=cv2.INTER_AREA)
    hashes = [hashlib.sha256(np.ascontiguousarray(view).tobytes()).hexdigest() for view in views.values()]
    if len(set(hashes)) != count:
        raise ValueError("derived camera views are not distinct")
    return views


def _load_views(image_path: Path) -> dict[str, Any]:
    import cv2

    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"could not decode public still image: {image_path}")
    return make_views(image)


def prepare_baseline(output_dir: Path) -> tuple[Path, str]:
    revision = subprocess.check_output(
        ["git", "rev-parse", BASELINE_REF], cwd=ROOT, text=True, stderr=subprocess.PIPE
    ).strip()
    source = subprocess.check_output(
        ["git", "show", f"{revision}:{BASELINE_PATH}"], cwd=ROOT, stderr=subprocess.PIPE
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "baseline_inference.py"
    path.write_bytes(source)
    return path, revision


class RssSampler:
    def __init__(self) -> None:
        import psutil

        self.process = psutil.Process(os.getpid())
        self.peak = self.process.memory_info().rss
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.01):
            self.peak = max(self.peak, self.process.memory_info().rss)

    def __enter__(self) -> "RssSampler":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, self.process.memory_info().rss)


def _synchronize(device: str) -> None:
    if device == "cuda":
        import torch

        torch.cuda.synchronize()
    elif device == "mps":
        import torch

        torch.mps.synchronize()


def _module(mode: str, baseline_path: Path) -> Any:
    if mode == "shared":
        from factory_monitor import inference

        return inference
    spec = importlib.util.spec_from_file_location("baseline_inference", baseline_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load pinned baseline inference module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(mode: str, baseline_path: Path, model_path: Path, image_path: Path, rounds: int) -> dict[str, Any]:
    import cv2
    import torch
    import ultralytics

    if rounds < 10:
        raise ValueError("at least ten warm rounds are required")
    views = _load_views(image_path)
    camera_ids = list(views)
    module = _module(mode, baseline_path)
    actual_yolo = ultralytics.YOLO
    loads = 0

    def count_yolo(*args: Any, **kwargs: Any) -> Any:
        nonlocal loads
        loads += 1
        return actual_yolo(*args, **kwargs)

    ultralytics.YOLO = count_yolo
    try:
        with RssSampler() as memory:
            started = time.perf_counter()
            detector = module.YoloPersonDetector(model_path, device="auto", confidence=0.35)
            init_ms = (time.perf_counter() - started) * 1000

            def one_round() -> tuple[float, dict[str, int]]:
                started = time.perf_counter()
                if mode == "shared":
                    people = detector.detect_batch(views)
                else:
                    people = {camera: detector.detect(camera, views[camera]) for camera in camera_ids}
                _synchronize(detector.device)
                elapsed_ms = (time.perf_counter() - started) * 1000
                if list(people) != camera_ids:
                    raise RuntimeError("detector result order did not match camera order")
                return elapsed_ms, {camera: len(people[camera]) for camera in camera_ids}

            cold_ms, cold_counts = one_round()
            warm: list[float] = []
            warm_counts: list[dict[str, int]] = []
            for _ in range(rounds):
                latency_ms, counts = one_round()
                warm.append(latency_ms)
                warm_counts.append(counts)
    finally:
        ultralytics.YOLO = actual_yolo
    image_hashes = {
        camera: hashlib.sha256(view.tobytes()).hexdigest() for camera, view in views.items()
    }
    return {
        "mode": mode,
        "device": detector.device,
        "model_instances_loaded": loads,
        "camera_count": len(camera_ids),
        "image_shape": [240, 320, 3],
        "image_hashes": image_hashes,
        "model_sha256": sha256_file(model_path),
        "public_image_sha256": sha256_file(image_path),
        "baseline_source_sha256": sha256_file(baseline_path),
        "cold_constructor_ms": init_ms,
        "cold_first_ten_ms": cold_ms,
        "cold_total_ms": init_ms + cold_ms,
        "warm_ten_ms": warm,
        "warm_p50_ms": percentile(warm, 0.5),
        "warm_p95_ms": percentile(warm, 0.95),
        "sampled_peak_process_rss_bytes": memory.peak,
        "cold_people_per_camera": cold_counts,
        "warm_people_per_camera": warm_counts,
        "python": sys.version.split()[0],
        "opencv": cv2.__version__,
        "torch": torch.__version__,
        "ultralytics": ultralytics.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def run(model_path: Path, image_path: Path, output_path: Path, rounds: int, *, prepare_only: bool = False) -> dict[str, Any]:
    if not model_path.is_file() or not image_path.is_file():
        raise FileNotFoundError("existing model and public image files are required")
    views = _load_views(image_path)
    baseline_path, revision = prepare_baseline(output_path.parent)
    prepared = {
        "baseline_revision": revision,
        "baseline_module": str(baseline_path),
        "baseline_source_sha256": sha256_file(baseline_path),
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "public_image_path": str(image_path),
        "public_image_sha256": sha256_file(image_path),
        "camera_count": len(views),
        "image_shape": [240, 320, 3],
        "image_hashes": {camera: hashlib.sha256(view.tobytes()).hexdigest() for camera, view in views.items()},
        "warm_rounds": rounds,
        "fixture_type": "ten deterministic crops of one public still photograph",
        "scope": "offline detector component only; no live streams, accuracy, capture, review, or field acceptance",
        "rss_scope": "10 ms sampled worker-process RSS; does not represent total unified-memory or GPU allocation",
    }
    if prepare_only:
        return prepared
    if rounds < 10:
        raise ValueError("at least ten warm rounds are required")
    report: dict[str, Any] = {**prepared, "process_order": ["baseline", "shared"], "runs": {}}
    for mode in report["process_order"]:
        worker_report = output_path.parent / f"detector-{mode}-worker.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            mode,
            "--baseline-module",
            str(baseline_path),
            "--model",
            str(model_path),
            "--image",
            str(image_path),
            "--rounds",
            str(rounds),
            "--worker-report",
            str(worker_report),
        ]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=900)
        if completed.returncode != 0:
            report["error"] = {"mode": mode, "returncode": completed.returncode, "stderr": completed.stderr[-4000:]}
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(f"{mode} worker failed; see {output_path}")
        result = json.loads(worker_report.read_text(encoding="utf-8"))
        if (
            result["image_hashes"] != prepared["image_hashes"]
            or result["model_sha256"] != prepared["model_sha256"]
            or result["baseline_source_sha256"] != prepared["baseline_source_sha256"]
        ):
            raise RuntimeError(f"{mode} worker used different fixture, model, or baseline bytes")
        report["runs"][mode] = result
    baseline = report["runs"]["baseline"]
    shared = report["runs"]["shared"]
    report["same_resolved_device"] = baseline["device"] == shared["device"]
    report["warm_p50_speedup_baseline_over_shared"] = (
        baseline["warm_p50_ms"] / shared["warm_p50_ms"] if report["same_resolved_device"] else None
    )
    report["warm_p95_speedup_baseline_over_shared"] = (
        baseline["warm_p95_ms"] / shared["warm_p95_ms"] if report["same_resolved_device"] else None
    )
    report["ok"] = True
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--prepare-only", action="store_true", help="validate inputs and pin baseline without inference")
    parser.add_argument("--worker", choices=["baseline", "shared"], help=argparse.SUPPRESS)
    parser.add_argument("--baseline-module", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            if args.baseline_module is None or args.worker_report is None:
                parser.error("worker requires baseline module and worker report")
            result = worker(args.worker, args.baseline_module, args.model, args.image, args.rounds)
            args.worker_report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            result = run(args.model, args.image, args.output, args.rounds, prepare_only=args.prepare_only)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"detector benchmark failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
