"""Capture sources with explicit provenance and failure behavior."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class CaptureError(RuntimeError):
    """A selected capture source cannot deliver frames."""


class DemoCapture:
    """Synthetic mosaic used only by the visibly labelled demo mode."""

    def __init__(self, width: int = 960, height: int = 540, fps: float = 5.0) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self._index = 0
        self._started = time.monotonic()

    def read(self) -> dict[str, Any]:
        frame = np.full((self.height, self.width, 3), (22, 27, 32), dtype=np.uint8)
        people: dict[str, list[dict[str, Any]]] = {}
        cols, rows = 4, 3
        tile_w, tile_h = self.width // cols, self.height // rows
        for index in range(10):
            col, row = index % cols, index // cols
            x0, y0 = col * tile_w, row * tile_h
            camera_id = f"CAM{index + 1:02d}"
            shade = 38 + (index * 9) % 45
            frame[y0 : y0 + tile_h, x0 : x0 + tile_w] = (shade, shade + 4, shade + 8)
            cv2.rectangle(frame, (x0, y0), (x0 + tile_w - 1, y0 + tile_h - 1), (100, 110, 120), 1)
            cv2.putText(frame, f"SYNTHETIC {camera_id}", (x0 + 8, y0 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (40, 220, 255), 1)
            phase = (self._index * 3 + index * 17) % max(1, tile_w - 50)
            px, py = x0 + 20 + phase, y0 + tile_h - 18
            cv2.circle(frame, (px, py - 25), 7, (210, 220, 235), -1)
            cv2.line(frame, (px, py - 18), (px, py), (210, 220, 235), 3)
            cv2.line(frame, (px, py - 10), (px - 8, py - 2), (210, 220, 235), 2)
            cv2.line(frame, (px, py - 10), (px + 8, py - 2), (210, 220, 235), 2)
            bbox = [
                (px - 10 - x0) / tile_w,
                (py - 34 - y0) / tile_h,
                (px + 10 - x0) / tile_w,
                (py - y0) / tile_h,
            ]
            people[camera_id] = [{"track_id": 1, "bbox": bbox, "confidence": 1.0}]
        cv2.putText(frame, "SYNTHETIC DEMO - SCRIPTED FACTS", (12, self.height - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 190, 255), 2)
        packet = {
            "timestamp": time.time(),
            "monotonic": self._started + self._index / self.fps,
            "image": frame,
            "source": "demo",
            "synthetic": True,
            "demo_people": people,
        }
        self._index += 1
        return packet

    def close(self) -> None:
        return None


class VideoCapture:
    """Replay an actual video; inference remains entirely detector-driven."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._capture = cv2.VideoCapture(str(self.path))
        if not self._capture.isOpened():
            self._capture.release()
            raise CaptureError(f"cannot open video: {self.path}")
        fps = float(self._capture.get(cv2.CAP_PROP_FPS))
        self.fps = fps if math.isfinite(fps) and fps > 0 else 5.0

    def read(self) -> dict[str, Any]:
        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise StopIteration
        return {
            "timestamp": time.time(),
            "monotonic": time.monotonic(),
            "image": frame,
            "source": "video",
            "synthetic": False,
        }

    def close(self) -> None:
        self._capture.release()


def open_capture(source: str, config: dict[str, Any], input_path: str | None = None):
    if source == "demo":
        return DemoCapture(fps=float(config.get("detection", {}).get("fps", 5)))
    if source == "video":
        if not input_path:
            raise CaptureError("video source requires input_path")
        return VideoCapture(input_path)
    if source != "live":
        raise CaptureError(f"unsupported source: {source}")
    source_config = config.get("source", {})
    if not source_config.get("calibrated"):
        raise CaptureError("live source is not calibrated")
    backend = source_config.get("backend", "auto")
    native_config = dict(source_config)
    native_config["fps"] = config.get("detection", {}).get("fps", 5)
    if backend in {"auto", "screencapturekit", "screen_capture_kit"}:
        import sys

        if sys.platform == "darwin":
            from .macos import MacScreenCapture

            return MacScreenCapture(native_config)
    if backend in {"auto", "wgc", "display"}:
        import sys

        if sys.platform == "win32":
            from .windows import WindowsGraphicsCapture

            return WindowsGraphicsCapture(native_config)
    raise CaptureError(f"native capture backend {backend!r} is unavailable on this OS")


__all__ = ["CaptureError", "DemoCapture", "VideoCapture", "open_capture"]
