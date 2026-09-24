import time
from pathlib import Path

import numpy as np

from factory_monitor.evidence import EvidenceRecorder


CAMERAS = tuple(f"CAM{index:02d}" for index in range(1, 11))


def _synthetic_camera_frame(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (180, 240, 3), dtype=np.uint8)


def _seed_retained_events(root: Path) -> None:
    for event_index in range(20):
        frames = root / f"retained-{event_index:02d}" / "frames"
        frames.mkdir(parents=True)
        (frames.parent / "manifest.json").write_bytes(b"{}")
        for frame_index in range(5):
            (frames / f"{frame_index:08d}.jpg").write_bytes(b"retained-evidence")


def test_ten_active_recordings_keep_up_with_two_fps_after_history_prefill(tmp_path: Path) -> None:
    """The evidence worker has 0.5 seconds to consume each synthetic ten-camera packet."""
    _seed_retained_events(tmp_path)
    recorder = EvidenceRecorder(
        tmp_path,
        pre_seconds=30,
        post_seconds=60,
        preview_seconds=30,
        fps=2,
        max_inflight=10,
        max_disk_mb=10240,
    )
    frames = {camera: _synthetic_camera_frame(index) for index, camera in enumerate(CAMERAS, start=1)}

    for step in range(61):
        timestamp = 1.0 + step * 0.5
        for camera in CAMERAS:
            recorder.ingest(camera, timestamp, frames[camera])
    for camera in CAMERAS:
        result = recorder.start({"id": f"event-{camera}", "camera_id": camera, "triggered_at": 31.0})
        assert result["ok"] is True

    started = time.perf_counter()
    for camera in CAMERAS:
        recorder.ingest(camera, 31.5, frames[camera])
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5, f"ten-camera evidence batch took {elapsed:.3f}s; 2 fps budget is 0.500s"


def test_disk_usage_reflects_retained_files_and_external_add_delete(tmp_path: Path) -> None:
    retained = tmp_path / "retained" / "frames" / "frame.jpg"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"a" * 17)
    recorder = EvidenceRecorder(tmp_path, max_disk_mb=1)

    assert recorder._disk_usage() == 17

    external = tmp_path / "external.bin"
    external.write_bytes(b"b" * 23)
    assert recorder._disk_usage() == 40

    retained.unlink()
    assert recorder._disk_usage() == 23


def test_existing_disk_occupancy_still_enforces_limit_after_rescan(tmp_path: Path) -> None:
    (tmp_path / "retained.bin").write_bytes(b"x" * 128)
    recorder = EvidenceRecorder(tmp_path, max_disk_mb=64 / (1024 * 1024))

    result = recorder.start({"id": "over-limit", "camera_id": "CAM01", "triggered_at": 1.0})

    assert result == {"ok": False, "reason": "evidence disk limit reached"}
    assert not (tmp_path / "over-limit").exists()
