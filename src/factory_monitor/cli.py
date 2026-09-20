"""Local operator CLI. It reports evidence boundaries instead of asserting field readiness."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import platform
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

from .config import default_config, load_config, save_config
from .evaluation import evaluate_results


def _json_stdout(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _error(message: str) -> int:
    _json_stdout({"ok": False, "error": message})
    return 2


def _ollama_status(endpoint: str) -> dict[str, Any]:
    parsed = urlsplit(endpoint)
    url = endpoint.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = [item.get("name") for item in payload.get("models", []) if isinstance(item, dict) and isinstance(item.get("name"), str)]
        return {"reachable": True, "endpoint": endpoint, "models": models, "http_status": response.status}
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        return {"reachable": False, "endpoint": endpoint, "error": str(exc), "host": parsed.hostname}


def _preflight(config: dict, data_dir: Path) -> dict[str, Any]:
    try:
        import psutil  # type: ignore[import-not-found]

        memory = psutil.virtual_memory().total
    except (ImportError, AttributeError):
        memory = None
    probe = data_dir if data_dir.exists() else data_dir.parent
    if not probe.exists():
        probe = Path.cwd()
    disk = shutil.disk_usage(probe)
    model_path = Path(config["detection"]["model_path"])
    source = config["source"]
    dependencies = {name: importlib.util.find_spec(name) is not None for name in ("numpy", "cv2", "httpx", "psutil", "PySide6", "ultralytics")}
    field_reasons = [
        "not proven: this read-only local preflight is not actual field acceptance",
        "required: separate Windows and macOS actual-client evidence",
        "required: 72-hour ten-camera run, 100 verified zero-misbinding switches, normal-shift false-alarm exposure, and fault evidence",
    ]
    return {
        "ok": True,
        "generated_at": time.time(),
        "system": {"operating_system": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": platform.python_version(), "ram_bytes": memory},
        "storage": {"path": str(data_dir), "path_exists": data_dir.exists(), "probe_path": str(probe), "free_bytes": disk.free, "required_pilot_bytes": 50 * 1024**3, "meets_50gb_pilot": disk.free >= 50 * 1024**3},
        "dependencies": dependencies,
        "model": {"path": str(model_path), "exists": model_path.is_file(), "configured_device": config["detection"]["device"]},
        "ollama": _ollama_status(config["review"]["endpoint"]),
        "capture_configuration": {
            "backend": source["backend"], "calibrated": source["calibrated"], "window_title_configured": bool(source["window_title"].strip()),
            "expected_size": source["expected_size"], "layout_version": source["layout_version"],
            "live_start_eligible": bool(source["calibrated"]),
            "note": "No screen capture, permission request, window selection, or OS setting was changed by preflight.",
        },
        "field_gate": {"status": "FAIL", "reasons": field_reasons},
    }


def _summary(controller: Any, source: str, started_at: float, collected: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(event.get("type", "unknown") for event in collected)
    errors = [{key: value for key, value in event.items() if key != "image"} for event in collected if event.get("type") == "error"]
    return {
        "ok": True,
        "source": source,
        "synthetic": source == "demo",
        "field_verified": False,
        "started_at": started_at,
        "ended_at": time.time(),
        "event_counts": dict(sorted(counts.items())),
        "errors": errors,
        "status": controller.status(),
        "field_gate": {"status": "FAIL", "reasons": ["runtime execution alone does not prove field acceptance"]},
    }


def _run_headless(config: dict, data_dir: Path, source: str, input_path: str | None, seconds: float, report: Path | None) -> int:
    if not math.isfinite(seconds) or seconds < 0 or seconds > 86_400:
        return _error("--seconds must be between 0 and 86400")
    from .runtime import RuntimeController

    controller = RuntimeController(config, data_dir)
    started_at = time.time()
    collected: list[dict[str, Any]] = []
    try:
        controller.start(source, input_path)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            collected.extend(controller.poll())
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        collected.extend(controller.poll())
    except (OSError, RuntimeError, ValueError) as exc:
        return _error(str(exc))
    finally:
        controller.stop()
        collected.extend(controller.poll())
    summary = _summary(controller, source, started_at, collected)
    if report is not None:
        _atomic_json(report, summary)
        summary["report_path"] = str(report)
    _json_stdout(summary)
    return 0


def _records_from_file(path: Path) -> list[dict]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read evaluation input: {path}") from exc
    if isinstance(loaded, dict):
        loaded = loaded.get("records")
    if not isinstance(loaded, list):
        raise ValueError("evaluation input must be a JSON list or {'records': [...]} object")
    return loaded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory-monitor", description="Local factory-monitor operator commands")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="write the safe uncalibrated V1 configuration")
    init.add_argument("--config", type=Path, required=True)
    gui = commands.add_parser("gui", help="launch the local desktop interface")
    gui.add_argument("--config", type=Path, required=True)
    gui.add_argument("--data-dir", type=Path, required=True)
    gui.add_argument("--source", choices=("demo", "video", "live"), default="demo")
    gui.add_argument("--input", type=Path)
    preflight = commands.add_parser("preflight", help="read local readiness without capture or OS changes")
    preflight.add_argument("--config", type=Path, required=True)
    preflight.add_argument("--data-dir", type=Path, required=True)
    replay = commands.add_parser("replay", help="run a video through the headless local runtime")
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument("--data-dir", type=Path, required=True)
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--seconds", type=float, default=30)
    replay.add_argument("--report", type=Path)
    demo = commands.add_parser("demo", help="run the visibly synthetic headless demo")
    demo.add_argument("--config", type=Path, required=True)
    demo.add_argument("--data-dir", type=Path, required=True)
    demo.add_argument("--seconds", type=float, default=30)
    demo.add_argument("--report", type=Path)
    evaluate = commands.add_parser("evaluate", help="write a machine-readable held-out evaluation report")
    evaluate.add_argument("--input", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--environment", choices=("synthetic", "field"), default="synthetic")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            if args.config.exists():
                return _error(f"refusing to overwrite existing configuration: {args.config}")
            save_config(default_config(), args.config)
            _json_stdout({"ok": True, "config_path": str(args.config), "calibrated": False, "cloud_enabled": False})
            return 0
        if args.command == "evaluate":
            report = evaluate_results(_records_from_file(args.input), environment=args.environment)
            _atomic_json(args.output, report)
            _json_stdout(report)
            return 0
        config = load_config(args.config)
        if args.command == "preflight":
            _json_stdout(_preflight(config, args.data_dir))
            return 0
        if args.command == "gui":
            if args.source == "video" and args.input is None:
                return _error("--input is required when --source video")
            from .gui.app import run_gui

            return run_gui(args.config, args.data_dir, source=args.source, input_path=str(args.input) if args.input else None)
        if args.command == "replay":
            return _run_headless(config, args.data_dir, "video", str(args.input), args.seconds, args.report)
        if args.command == "demo":
            return _run_headless(config, args.data_dir, "demo", None, args.seconds, args.report)
    except ValueError as exc:
        return _error(str(exc))
    return _error("unsupported command")
