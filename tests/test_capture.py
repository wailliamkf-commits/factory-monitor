from pathlib import Path

import numpy as np
import pytest

from factory_monitor.capture import CaptureError, DemoCapture, VideoCapture


def test_demo_frames_are_explicitly_synthetic_and_carry_demo_facts():
    source = DemoCapture(width=320, height=180, fps=5)

    packet = source.read()

    assert packet["synthetic"] is True
    assert packet["source"] == "demo"
    assert packet["image"].shape == (180, 320, 3)
    assert isinstance(packet["demo_people"], dict)


def test_video_open_failure_is_visible_instead_of_falling_back_to_demo(tmp_path: Path):
    missing = tmp_path / "missing.mp4"

    with pytest.raises(CaptureError, match="cannot open video"):
        VideoCapture(missing)


def test_video_packets_never_include_scripted_demo_detections(tmp_path: Path):
    import cv2

    path = tmp_path / "one-frame.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()

    source = VideoCapture(path)
    packet = source.read()
    source.close()

    assert packet["synthetic"] is False
    assert "demo_people" not in packet

