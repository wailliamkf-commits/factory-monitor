import json
from pathlib import Path

import cv2
import numpy as np

from factory_monitor.evidence import EvidenceRecorder, recover_interrupted_evidence


def _frame(value: int) -> np.ndarray:
    return np.full((48, 64, 3), value, dtype=np.uint8)


def test_recorder_keeps_pre_and_post_frames_and_reports_a_gap(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=3, post_seconds=2, preview_seconds=1, fps=2)
    for timestamp in (7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))

    started = recorder.start({"id": "evt-1", "camera_id": "CAM01", "triggered_at": 10.0})
    assert started["ok"] is True

    recorder.ingest("CAM01", 10.5, _frame(10))
    recorder.ingest("CAM01", 12.0, _frame(12))
    updates = recorder.drain_updates()

    final = next(item for item in updates if item["recording_status"] == "complete")
    assert Path(final["evidence_path"]).is_file()
    assert Path(final["preview_path"]).is_file()
    assert final["gaps"] == [[10.5, 12.0]]

    capture = cv2.VideoCapture(final["evidence_path"])
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    assert count >= 6


def test_inflight_limit_rejects_new_event_without_erasing_existing_one(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, max_inflight=1, fps=1)
    recorder.ingest("CAM01", 1.0, _frame(1))

    assert recorder.start({"id": "kept", "camera_id": "CAM01", "triggered_at": 1.0})["ok"]
    rejected = recorder.start({"id": "rejected", "camera_id": "CAM01", "triggered_at": 1.0})

    assert rejected == {"ok": False, "reason": "evidence inflight limit reached"}
    assert (tmp_path / "kept" / "manifest.json").is_file()


def test_recovery_marks_interrupted_manifest_incomplete(tmp_path: Path):
    event_dir = tmp_path / "evt-crashed"
    event_dir.mkdir(parents=True)
    manifest = event_dir / "manifest.json"
    manifest.write_text(
        json.dumps({"event_id": "evt-crashed", "recording_status": "recording", "gaps": []}),
        encoding="utf-8",
    )

    recovered = recover_interrupted_evidence(tmp_path)

    assert recovered[0]["event_id"] == "evt-crashed"
    assert recovered[0]["recording_status"] == "incomplete"
    assert recovered[0]["gaps"][-1][1] is None
    assert json.loads(manifest.read_text(encoding="utf-8"))["recording_status"] == "incomplete"


def test_disk_pressure_finalizes_active_event_as_incomplete_without_deleting_it(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=10, fps=1, max_disk_mb=0.0001)
    recorder.ingest("CAM01", 1.0, _frame(1))
    assert recorder.start({"id": "protected", "camera_id": "CAM01", "triggered_at": 1.0})["ok"]

    recorder.ingest("CAM01", 2.0, np.random.default_rng(7).integers(0, 256, (48, 64, 3), dtype=np.uint8))
    updates = recorder.drain_updates()

    failure = next(item for item in updates if item["recording_status"] == "incomplete")
    assert "disk limit" in failure["reason"]
    assert (tmp_path / "protected" / "manifest.json").is_file()


def test_unverified_layout_is_recorded_as_an_explicit_gap(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=2, fps=2)
    recorder.ingest("CAM01", 10.0, _frame(10))
    assert recorder.start({"id": "mapping-gap", "camera_id": "CAM01", "triggered_at": 10.0})["ok"]

    recorder.note_gap("CAM01", 10.5, "mapping identity unverified")
    recorder.ingest("CAM01", 11.0, _frame(11))
    recorder.ingest("CAM01", 12.0, _frame(12))
    final = next(item for item in recorder.drain_updates() if item["recording_status"] == "complete")

    assert [10.5, 11.0] in final["gaps"]


def test_missing_requested_pre_window_is_explicit(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=30, post_seconds=1, fps=1)
    recorder.ingest("CAM01", 100.0, _frame(100))
    assert recorder.start({"id": "short-pre", "camera_id": "CAM01", "triggered_at": 100.0})["ok"]
    recorder.ingest("CAM01", 101.0, _frame(101))

    final = next(item for item in recorder.drain_updates() if item["recording_status"] == "complete")

    assert [70.0, 100.0] in final["gaps"]


def test_corrupt_frame_makes_clip_incomplete_instead_of_silently_shorter(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=2, fps=1)
    recorder.ingest("CAM01", 10.0, _frame(10))
    assert recorder.start({"id": "decode-failure", "camera_id": "CAM01", "triggered_at": 10.0})["ok"]
    recorder.ingest("CAM01", 11.0, _frame(11))
    frame_path = sorted((tmp_path / "decode-failure" / "frames").glob("*.jpg"))[-1]
    frame_path.write_bytes(b"not-a-jpeg")
    recorder.ingest("CAM01", 12.0, _frame(12))

    final = next(item for item in recorder.drain_updates() if item["recording_status"] == "incomplete")

    assert "decode" in final["reason"]
    assert "partial clip retained" in final["reason"]
    assert Path(final["evidence_path"]).name == "evidence.mp4"
    assert Path(final["evidence_path"]).is_file()
