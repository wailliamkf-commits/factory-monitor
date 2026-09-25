import io
import struct
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from factory_monitor.capture import CaptureError
from factory_monitor.capture.macos import decode_frame, ensure_helper
from factory_monitor.capture.windows import WindowsGraphicsCapture
import factory_monitor.capture.windows as windows_module


@pytest.mark.skipif(sys.platform != "darwin", reason="ScreenCaptureKit helper is macOS-only")
def test_screencapturekit_helper_compiles_and_identifies_itself(tmp_path: Path):
    binary = ensure_helper(tmp_path)

    result = subprocess.run([str(binary), "--help"], text=True, capture_output=True, timeout=10)

    assert result.returncode == 0
    assert "ScreenCaptureKit" in result.stdout


def test_macos_binary_protocol_decodes_bgra_as_bgr():
    bgra = bytes([10, 20, 30, 255, 40, 50, 60, 255])
    stream = io.BytesIO(struct.pack(">dIII", 123.5, 2, 1, len(bgra)) + bgra)

    timestamp, image = decode_frame(stream)

    assert timestamp == 123.5
    assert image.dtype == np.uint8
    assert image.tolist() == [[[10, 20, 30], [40, 50, 60]]]


@pytest.mark.skipif(sys.platform == "win32", reason="validates unsupported-host failure")
def test_windows_capture_does_not_claim_wgc_support_on_other_hosts():
    with pytest.raises(CaptureError, match="Windows"):
        WindowsGraphicsCapture({"window_title": "test", "display_index": 0})


def _mock_windows_capture_provider(monkeypatch: pytest.MonkeyPatch, frame) -> None:
    class CaptureControl:
        def stop(self) -> None:
            return

    class WindowsCapture:
        def __init__(self, **options) -> None:
            self.options = options
            self.on_frame = None
            self.on_closed = None

        def event(self, handler):
            if handler.__name__ == "on_frame_arrived":
                self.on_frame = handler
            else:
                self.on_closed = handler
            return handler

        def start_free_threaded(self) -> CaptureControl:
            assert self.on_frame is not None
            self.on_frame(frame, object())
            return CaptureControl()

    provider = types.ModuleType("windows_capture")
    provider.Frame = type(frame)
    provider.InternalCaptureControl = object
    provider.WindowsCapture = WindowsCapture
    monkeypatch.setitem(sys.modules, "windows_capture", provider)
    monkeypatch.setattr(windows_module.sys, "platform", "win32")


class _BgrProviderFrame:
    def __init__(self, frame_buffer: np.ndarray) -> None:
        self.frame_buffer = frame_buffer
        self.height, self.width = frame_buffer.shape[:2]
        self.converted = False

    def convert_to_bgr(self):
        self.converted = True
        return _BgrProviderFrame(self.frame_buffer[:, :, :3])


def test_windows_capture_uses_provider_bgr_frame_without_channel_swap(monkeypatch: pytest.MonkeyPatch):
    frame = _BgrProviderFrame(np.array([[[10, 20, 30, 255], [40, 50, 60, 255]]], dtype=np.uint8))
    _mock_windows_capture_provider(monkeypatch, frame)

    capture = WindowsGraphicsCapture({"backend": "display", "display_index": 0})
    result = capture.read()

    assert frame.converted is True
    assert result["image"].shape == (1, 2, 3)
    assert result["image"].tolist() == [[[10, 20, 30], [40, 50, 60]]]


def test_windows_capture_reports_provider_conversion_failure(monkeypatch: pytest.MonkeyPatch):
    class BrokenFrame(_BgrProviderFrame):
        def convert_to_bgr(self):
            raise RuntimeError("provider conversion failed")

    _mock_windows_capture_provider(monkeypatch, BrokenFrame(np.zeros((1, 1, 4), dtype=np.uint8)))

    capture = WindowsGraphicsCapture({"backend": "display", "display_index": 0})

    with pytest.raises(CaptureError, match="provider conversion failed"):
        capture.read()


def test_windows_frame_keeps_capture_time_when_dequeued_later(monkeypatch):
    clock = {"wall": 100.0, "mono": 50.0}
    monkeypatch.setattr(windows_module.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(windows_module.time, "monotonic", lambda: clock["mono"])
    _mock_windows_capture_provider(monkeypatch, _BgrProviderFrame(np.zeros((1, 1, 4), dtype=np.uint8)))
    capture = WindowsGraphicsCapture({"backend": "display", "display_index": 0})
    clock.update(wall=108.0, mono=58.0)

    result = capture.read()

    assert result["timestamp"] == 100.0
    assert result["monotonic"] == 50.0
