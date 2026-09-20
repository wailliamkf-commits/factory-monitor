"""Windows Graphics Capture adapter for an exact window or explicit display."""

from __future__ import annotations

import queue
import sys
import time
from typing import Any

import numpy as np

from . import CaptureError


class WindowsGraphicsCapture:
    def __init__(self, config: dict[str, Any]) -> None:
        if sys.platform != "win32":
            raise CaptureError("Windows Graphics Capture is available only on Windows")
        try:
            from windows_capture import Frame, InternalCaptureControl, WindowsCapture
        except ImportError as exc:
            raise CaptureError("windows-capture provider is not installed") from exc
        title = str(config.get("window_title", "")).strip()
        backend = config.get("backend", "auto")
        if backend != "display" and not title:
            raise CaptureError("WGC window capture requires an exact non-empty window_title")
        self._frames: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=3)
        self._errors: queue.Queue[str] = queue.Queue()
        options: dict[str, Any] = {"cursor_capture": False, "draw_border": False}
        if backend == "display":
            options["monitor_index"] = int(config.get("display_index", 0)) + 1
            self._identity = {"backend": "wgc-display", "display_index": int(config.get("display_index", 0))}
        else:
            options["window_name"] = title
            self._identity = {"backend": "wgc-window", "window_title": title}
        self._capture = WindowsCapture(**options)

        @self._capture.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl) -> None:
            try:
                converted = frame.convert_to_bgr()
                array = np.asarray(converted.frame_buffer, dtype=np.uint8)
                expected_shape = (converted.height, converted.width, 3)
                if array.shape != expected_shape:
                    raise ValueError(f"Windows Graphics Capture BGR frame has shape {array.shape}, expected {expected_shape}")
                bgr = array.copy()
                try:
                    self._frames.put_nowait((time.monotonic(), bgr))
                except queue.Full:
                    try:
                        self._frames.get_nowait()
                    except queue.Empty:
                        pass
                    self._frames.put_nowait((time.monotonic(), bgr))
            except Exception as exc:
                self._errors.put(str(exc))

        @self._capture.event
        def on_closed() -> None:
            self._errors.put("Windows Graphics Capture source closed")

        try:
            self._control = self._capture.start_free_threaded()
        except Exception as exc:
            raise CaptureError(f"Windows Graphics Capture could not start: {exc}") from exc

    def read(self) -> dict[str, Any]:
        try:
            error = self._errors.get_nowait()
        except queue.Empty:
            error = None
        if error:
            raise CaptureError(error)
        try:
            captured_monotonic, image = self._frames.get(timeout=3)
        except queue.Empty as exc:
            raise CaptureError("Windows Graphics Capture produced no frame; check permission and selected source") from exc
        return {
            "timestamp": time.time(),
            "monotonic": captured_monotonic,
            "image": image,
            "source": "live",
            "source_identity": self._identity,
            "synthetic": False,
        }

    def close(self) -> None:
        try:
            self._control.stop()
        except Exception:
            pass
