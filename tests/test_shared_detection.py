"""Regression tests for one prediction network and isolated camera track state."""

from __future__ import annotations

import queue
from types import SimpleNamespace

import numpy as np
from ultralytics.engine.results import Results

from factory_monitor import runtime
from factory_monitor.inference import YoloPersonDetector


def _image(code: int) -> np.ndarray:
    return np.full((100, 100, 3), code, dtype=np.uint8)


def test_detector_predicts_ordered_batch_once_and_keeps_trackers_separate(tmp_path, monkeypatch):
    model_path = tmp_path / "local.pt"
    model_path.touch()
    created = []

    class FakeYolo:
        def __init__(self, path, task):
            created.append(self)
            assert path == str(model_path)
            assert task == "detect"
            self.calls = []

        def predict(self, source, **kwargs):
            self.calls.append(([int(image[0, 0, 0]) for image in source], kwargs))
            results = []
            for image in source:
                code = int(image[0, 0, 0])
                boxes = [[10 + code / 10, 10, 30, 40, 0.9, 0]]
                if code == 30:
                    boxes.append([70, 10, 90, 40, 0.9, 0])
                results.append(Results(orig_img=image, path="synthetic", names={0: "person"}, boxes=np.array(boxes, dtype=np.float32)))
            return results

    monkeypatch.setattr("ultralytics.YOLO", FakeYolo)
    detector = YoloPersonDetector(model_path, device="cpu")

    first = detector.detect_batch({"CAM02": _image(20), "CAM01": _image(10)})
    second = detector.detect_batch({"CAM02": _image(20), "CAM01": _image(10)})

    assert len(created) == 1
    assert [call[0] for call in created[0].calls] == [[20, 10], [20, 10]]
    assert all(call[1]["classes"] == [0] for call in created[0].calls)
    assert list(first) == ["CAM02", "CAM01"]
    assert first["CAM02"][0]["bbox"][0] > first["CAM01"][0]["bbox"][0]
    assert second["CAM02"][0]["track_id"] == first["CAM02"][0]["track_id"]
    assert second["CAM01"][0]["track_id"] == first["CAM01"][0]["track_id"]
    assert detector._trackers["CAM02"] is not detector._trackers["CAM01"]
    assert detector._trackers["CAM02"].frame_id == 2
    assert detector._trackers["CAM01"].frame_id == 2

    detector.reset("CAM01")
    assert "CAM01" not in detector._trackers
    assert detector._trackers["CAM02"].frame_id == 2
    detector.detect_batch({"CAM01": _image(10), "CAM02": _image(30)})
    after_reset = detector.detect_batch({"CAM01": _image(10), "CAM02": _image(30)})
    cam02_ids = [person["track_id"] for person in after_reset["CAM02"]]
    assert first["CAM02"][0]["track_id"] in cam02_ids
    assert len(cam02_ids) == len(set(cam02_ids)) == 2
    assert detector._trackers["CAM02"].frame_id == 4


def test_detection_process_batches_only_visible_cameras_and_resets_after_blind(monkeypatch):
    calls = []

    class FakeDetector:
        def __init__(self, *_args, **_kwargs):
            pass

        def detect_batch(self, images):
            calls.append(("batch", list(images)))
            return {camera: [{"track_id": 1, "bbox": [0.1, 0.1, 0.2, 0.2], "confidence": 0.9}] for camera in images}

        def reset(self, camera):
            calls.append(("reset", camera))

    mapped = [
        {"CAM01": ("observable", _image(10)), "CAM02": ("blind", None)},
        {"CAM01": ("blind", None), "CAM02": ("detail", _image(20))},
        {"CAM01": ("observable", _image(10)), "CAM02": ("observable", _image(20))},
    ]
    monkeypatch.setattr(runtime, "YoloPersonDetector", FakeDetector)
    monkeypatch.setattr(runtime, "map_live_views", lambda *_args: (mapped.pop(0), "mapped"))
    monkeypatch.setattr(runtime, "apply_camera_heartbeats", lambda views, *_args: (views, {}))

    class Frames:
        def __init__(self):
            self.remaining = 3

        def get(self, timeout):
            self.remaining -= 1
            if self.remaining == 0:
                stop.set()
            return {"image": _image(0), "timestamp": 100.0, "monotonic": 10.0, "synthetic": False}

    class Stop:
        active = False

        def is_set(self):
            return self.active

        def set(self):
            self.active = True

    stop = Stop()
    messages = queue.Queue()
    config = {
        "detection": {"model_path": "fake.pt", "device": "cpu", "confidence": 0.35},
        "source": {"expected_size": [100, 100], "layout_version": 1},
        "cameras": [{"id": "CAM01", "enabled": True}, {"id": "CAM02", "enabled": True}],
        "switching": {"verification": {}},
    }

    runtime._detection_process("live", config, Frames(), messages, stop, SimpleNamespace(value=-1))

    assert calls == [
        ("batch", ["CAM01"]),
        ("reset", "CAM01"),
        ("batch", ["CAM02"]),
        ("reset", "CAM02"),
        ("batch", ["CAM01", "CAM02"]),
    ]
    observations = [message["observation"] for message in list(messages.queue) if message["_kind"] == "observation"]
    assert len(observations) == 6
    assert [item["health"] for item in observations[1::2]] == ["blind", "observable", "observable"]
    assert all(item["people"] == [] for item in observations if item["health"] == "blind")
