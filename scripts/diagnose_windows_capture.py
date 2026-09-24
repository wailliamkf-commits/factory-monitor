"""Count WGC callbacks for one explicit target; never inspect or save pixels.

On Windows, run ``python scripts/diagnose_windows_capture.py --help`` first.
Display capture needs both an explicit monitor index and scope acknowledgement.
This is a diagnosis, not a product capture fallback or a visual freshness test.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import inspect
import json
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


class DiagnosticError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--window-hwnd", type=int, help="Exact positive decimal HWND")
    target.add_argument("--monitor-index", type=int, help="Explicit 1-based provider monitor index")
    parser.add_argument("--allow-display-capture", action="store_true", help="Acknowledge the wider display scope; obtain local authorization first")
    parser.add_argument("--duration", "--seconds", dest="seconds", type=float, default=8.0, help="Sampling duration, default 8, maximum 30")
    parser.add_argument("--output", "--summary", dest="summary", type=Path, default=Path("wgc-diagnostic-summary.json"), help="Local metadata-only JSON path")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.window_hwnd is not None and args.window_hwnd <= 0:
        parser.error("--window-hwnd must be positive")
    if args.monitor_index is not None and args.monitor_index <= 0:
        parser.error("--monitor-index must be positive and 1-based")
    if args.monitor_index is not None and not args.allow_display_capture:
        parser.error("display target requires --allow-display-capture")
    if not 0 < args.seconds <= 30:
        parser.error("--seconds must be greater than 0 and at most 30")
    return args


def summarize_callbacks(callbacks: list[tuple[float, int, int]], end_monotonic: float) -> dict[str, Any]:
    times = [item[0] for item in callbacks]
    return {
        "callback_count": len(times),
        "first_offset_s": times[0] if times else None,
        "last_offset_s": times[-1] if times else None,
        "max_inter_callback_gap_s": max((b - a for a, b in zip(times, times[1:])), default=None),
        "tail_gap_s": max(0.0, end_monotonic - times[-1]) if times else end_monotonic,
        "dimensions": [list(size) for size in sorted({(w, h) for _, w, h in callbacks})],
    }


def require_target_parameter(provider: type, parameter: str) -> None:
    try:
        parameters = inspect.signature(provider).parameters
    except (TypeError, ValueError) as exc:
        raise DiagnosticError(f"unverifiable_{parameter}") from exc
    if parameter not in parameters:
        raise DiagnosticError(f"unsupported_{parameter}")


def collect_callbacks(provider: type, options: dict[str, int], seconds: float) -> dict[str, Any]:
    target_name = next(iter(options))
    require_target_parameter(provider, target_name)
    callbacks: list[tuple[float, int, int]] = []
    lock = threading.Lock()
    closed = False
    callback_error: str | None = None
    start = time.monotonic()
    control = None
    stop_succeeded = False
    thread_finished: bool | None = None
    worker_error: str | None = None
    try:
        capture = provider(**options)

        @capture.event
        def on_frame_arrived(frame: Any, capture_control: Any) -> None:
            nonlocal callback_error
            try:
                arrival = time.monotonic() - start
                width, height = int(frame.width), int(frame.height)
                with lock:
                    callbacks.append((arrival, width, height))
            except Exception as exc:
                callback_error = type(exc).__name__

        @capture.event
        def on_closed() -> None:
            nonlocal closed
            closed = True

        control = capture.start_free_threaded()
        time.sleep(seconds)
    except Exception as exc:
        worker_error = type(exc).__name__
    finally:
        if control is not None:
            try:
                control.stop()
                stop_succeeded = True
            except Exception as exc:
                worker_error = worker_error or type(exc).__name__
            try:
                control.wait()
                thread_finished = bool(control.is_finished())
            except Exception as exc:
                worker_error = worker_error or type(exc).__name__
        end = time.monotonic() - start
    with lock:
        result = summarize_callbacks(callbacks, end)
    result.update({"closed": closed, "callback_error": callback_error, "worker_error": worker_error,
                   "stop_succeeded": stop_succeeded, "thread_finished": thread_finished,
                   "elapsed_s": end})
    result["status"] = ("error" if worker_error or callback_error or not stop_succeeded or thread_finished is False
                        else "no_frames" if result["callback_count"] == 0
                        else "single_frame" if result["callback_count"] == 1 else "multiple_callbacks")
    return result


def _write_summary(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_isolated(command: list[str], timeout_s: float, summary_path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        result: dict[str, Any] = {"status": "timeout", "error_type": "ChildTimeout", "child_reaped": True}
    except Exception as exc:
        result = {"status": "error", "error_type": type(exc).__name__}
    else:
        try:
            result = json.loads(completed.stdout)
            if not isinstance(result, dict):
                raise ValueError("non-object result")
            count = result.get("callback_count")
            coherent = ((result.get("status") == "no_frames" and count == 0) or
                        (result.get("status") == "single_frame" and count == 1) or
                        (result.get("status") == "multiple_callbacks" and isinstance(count, int) and count >= 2) or
                        (result.get("status") == "error" and any(
                            result.get(key) for key in ("error_type", "worker_error", "callback_error", "error_code")
                        )))
            if completed.returncode != 0 and not coherent:
                result["status"] = "error"
                result["error_type"] = "ChildExit"
        except (json.JSONDecodeError, ValueError):
            result = {"status": "error", "error_type": "ChildOutput", "exit_code": completed.returncode}
    _write_summary(summary_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target_type = "window" if args.window_hwnd is not None else "display"
    options = {"window_hwnd": args.window_hwnd} if target_type == "window" else {"monitor_index": args.monitor_index}
    try:
        provider_version = importlib.metadata.version("windows-capture")
    except importlib.metadata.PackageNotFoundError:
        provider_version = "not_installed"
    metadata: dict[str, Any] = {
        "target_type": target_type, "target_options": options, "requested_seconds": args.seconds,
        "python_version": platform.python_version(), "os_version": platform.platform(),
        "provider_version": provider_version,
    }
    if args.worker:
        try:
            if sys.platform != "win32":
                raise DiagnosticError("windows_only")
            from windows_capture import WindowsCapture
            metadata.update(collect_callbacks(WindowsCapture, options, args.seconds))
        except DiagnosticError as exc:
            metadata.update({"status": "error", "error_type": "DiagnosticError", "error_code": exc.code})
        except Exception as exc:
            metadata.update({"status": "error", "error_type": type(exc).__name__})
        print(json.dumps(metadata, ensure_ascii=False))
        return 0 if metadata["status"] == "multiple_callbacks" else 1
    command = [sys.executable, str(Path(__file__).resolve()),
               *( ["--window-hwnd", str(args.window_hwnd)] if target_type == "window" else
                  ["--monitor-index", str(args.monitor_index), "--allow-display-capture"]),
               "--seconds", str(args.seconds), "--worker"]
    result = run_isolated(command, args.seconds + 5.0, args.summary)
    result = {**metadata, **result}
    _write_summary(args.summary, result)
    print(json.dumps({"status": result["status"], "summary": str(args.summary)}, ensure_ascii=False))
    return 0 if result["status"] == "multiple_callbacks" else 1


if __name__ == "__main__":
    raise SystemExit(main())
