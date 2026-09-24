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
    assert final["window_complete"] is False
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
    assert recovered[0]["window_complete"] is False
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
    assert final["window_complete"] is False


def test_empty_cache_gap_reaches_actual_first_recorded_frame(tmp_path: Path):
    """A delayed first frame cannot leave an invisible trigger-to-frame hole."""
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=2, preview_seconds=1, fps=2)
    assert recorder.start({"id": "late-first", "camera_id": "CAM01", "triggered_at": 100.0})["ok"]
    for timestamp in (101.0, 101.5, 102.0):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))
    final = next(item for item in recorder.drain_updates() if item["recording_status"] == "complete")
    manifest = json.loads((tmp_path / "late-first" / "manifest.json").read_text(encoding="utf-8"))
    assert [99.0, 101.0] in final["gaps"]
    assert [99.0, 101.0] in manifest["gaps"]
    assert manifest["gap_reasons"][0]["end"] == 101.0
    assert manifest["frame_count"] == 3
    assert manifest["window_complete"] is False
    assert final["window_complete"] is False


def test_empty_cache_without_a_first_frame_keeps_open_gap_at_stop(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=2, fps=2)
    assert recorder.start({"id": "never-first", "camera_id": "CAM01", "triggered_at": 100.0})["ok"]
    stopped = next(item for item in recorder.close() if item["recording_status"] == "incomplete")
    manifest = json.loads((tmp_path / "never-first" / "manifest.json").read_text(encoding="utf-8"))
    assert stopped["gaps"] == [[99.0, None]]
    assert manifest["gaps"] == [[99.0, None]]
    assert manifest["gap_reasons"][0]["end"] is None
    assert manifest["window_complete"] is False


def test_full_requested_window_has_positive_coverage_only_after_finalization(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=1, fps=2)
    for timestamp in (99.0, 99.5, 100.0):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))
    assert recorder.start({"id": "full-window", "camera_id": "CAM01", "triggered_at": 100.0})["ok"]
    initial = json.loads((tmp_path / "full-window" / "manifest.json").read_text(encoding="utf-8"))
    assert initial["window_complete"] is None
    for timestamp in (100.5, 101.0):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))
    final = next(item for item in recorder.drain_updates() if item["recording_status"] == "complete")
    manifest = json.loads((tmp_path / "full-window" / "manifest.json").read_text(encoding="utf-8"))
    assert final["gaps"] == [] and final["window_complete"] is True
    assert manifest["window_complete"] is True


def test_three_millisecond_boundary_keeps_one_real_predecessor_frame(tmp_path: Path):
    """A source frame 3 ms before cutoff must not disappear from the 30 s lead-in."""
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=1, fps=2)
    for timestamp in (99.0, 99.5, 100.003):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))
    assert recorder.start({"id": "boundary", "camera_id": "CAM01", "triggered_at": 100.003})["ok"]
    for timestamp in (100.503, 101.003):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))

    final = next(item for item in recorder.drain_updates() if item["recording_status"] == "complete")
    manifest = json.loads((tmp_path / "boundary" / "manifest.json").read_text(encoding="utf-8"))
    saved = [float(path.stem.split("-")[1]) for path in sorted((tmp_path / "boundary" / "frames").glob("*.jpg"))]
    assert saved == [99.0, 99.5, 100.003, 100.503, 101.003]
    assert manifest["frame_count"] == 5
    assert final["gaps"] == [] and final["window_complete"] is True


def test_fifteen_millisecond_late_boundary_does_not_add_distant_predecessor(tmp_path: Path):
    """The close in-window sample wins over a frame nearly half a second early."""
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=1, fps=2)
    for timestamp in (98.5, 99.0, 99.5, 99.985):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))
    assert recorder.start({"id": "late-boundary", "camera_id": "CAM01", "triggered_at": 99.985})["ok"]
    for timestamp in (100.485, 100.985):
        recorder.ingest("CAM01", timestamp, _frame(int(timestamp)))

    final = next(item for item in recorder.drain_updates() if item["recording_status"] == "complete")
    manifest = json.loads((tmp_path / "late-boundary" / "manifest.json").read_text(encoding="utf-8"))
    saved = [float(path.stem.split("-")[1]) for path in sorted((tmp_path / "late-boundary" / "frames").glob("*.jpg"))]
    assert saved == [99.0, 99.5, 99.985, 100.485, 100.985]
    assert manifest["frame_count"] == 5
    assert final["gaps"] == [] and final["window_complete"] is True


def test_recovery_keeps_open_prefix_gap_without_a_first_frame(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=2, fps=2)
    assert recorder.start({"id": "unseen", "camera_id": "CAM01", "triggered_at": 100.0})["ok"]
    recovered = recover_interrupted_evidence(tmp_path)
    manifest = json.loads((tmp_path / "unseen" / "manifest.json").read_text(encoding="utf-8"))
    assert recovered[0]["gaps"] == [[99.0, None]]
    assert manifest["window_complete"] is False


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
