"""ScreenCaptureKit exact-window adapter using the bundled Swift helper."""

from __future__ import annotations

import os
import struct
import subprocess
import time
from pathlib import Path
from typing import BinaryIO

import cv2
import numpy as np

from . import CaptureError


_HEADER = struct.Struct(">dIII")


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("ScreenCaptureKit helper ended its frame stream")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def decode_frame(stream: BinaryIO) -> tuple[float, np.ndarray]:
    timestamp, width, height, length = _HEADER.unpack(_read_exact(stream, _HEADER.size))
    expected = width * height * 4
    if not width or not height or length != expected:
        raise CaptureError(f"invalid ScreenCaptureKit frame dimensions {width}x{height}/{length}")
    bgra = np.frombuffer(_read_exact(stream, length), dtype=np.uint8).reshape((height, width, 4))
    return timestamp, cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)


def ensure_helper(build_dir: str | Path | None = None) -> Path:
    if os.uname().sysname != "Darwin":
        raise CaptureError("ScreenCaptureKit is available only on macOS")
    source = Path(__file__).resolve().parent / "assets" / "ScreenCaptureKitHelper.swift"
    target_dir = Path(build_dir) if build_dir is not None else Path.home() / "Library" / "Caches" / "FactoryMonitor"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "factory-monitor-screencapturekit"
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    command = [
        "xcrun",
        "swiftc",
        "-parse-as-library",
        "-O",
        "-framework",
        "ScreenCaptureKit",
        "-framework",
        "AppKit",
        "-framework",
        "CoreMedia",
        "-framework",
        "CoreVideo",
        str(source),
        "-o",
        str(target),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise CaptureError(f"could not build ScreenCaptureKit helper: {result.stderr.strip()}")
    return target


class MacScreenCapture:
    def __init__(self, config: dict) -> None:
        title = str(config.get("window_title", "")).strip()
        if not title:
            raise CaptureError("ScreenCaptureKit requires an exact non-empty window_title")
        helper = ensure_helper()
        fps = max(1, round(float(config.get("fps", 5))))
        self._process = subprocess.Popen(
            [str(helper), "--window-title", title, "--fps", str(fps)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._title = title
        self._epoch_to_monotonic = time.monotonic() - time.time()

    def read(self) -> dict:
        if self._process.stdout is None:
            raise CaptureError("ScreenCaptureKit helper has no output stream")
        if self._process.poll() is not None:
            detail = self._process.stderr.read().decode("utf-8", "replace").strip() if self._process.stderr else ""
            raise CaptureError(detail or "ScreenCaptureKit helper exited")
        try:
            timestamp, image = decode_frame(self._process.stdout)
        except EOFError as exc:
            detail = self._process.stderr.read().decode("utf-8", "replace").strip() if self._process.stderr else ""
            raise CaptureError(detail or str(exc)) from exc
        return {
            "timestamp": timestamp or time.time(),
            "monotonic": timestamp + self._epoch_to_monotonic,
            "image": image,
            "source": "live",
            "source_identity": {"backend": "screencapturekit", "window_title": self._title},
            "synthetic": False,
        }

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1)
