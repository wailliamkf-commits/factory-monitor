"""Evidence identity is independent from queue consumption and event overlap."""
import json
from pathlib import Path

import numpy as np

from factory_monitor.evidence import EvidenceRecorder


def test_evidence_manifest_keeps_cached_and_future_source_identity(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=1, post_seconds=1, fps=2)
    image = np.full((180, 240, 3), 100, dtype=np.uint8)
    for seq in range(2):
        recorder.ingest("CAM01", 10 + seq * 0.5, image, run_id="source-run", frame_id=seq)
    assert recorder.start({"id": "first", "camera_id": "CAM01", "triggered_at": 11})["ok"]
    assert recorder.start({"id": "overlapping", "camera_id": "CAM01", "triggered_at": 11})["ok"]
    for seq in range(2, 5):
        recorder.ingest("CAM01", 10 + seq * 0.5, image, run_id="source-run", frame_id=seq)
    for event in ("first", "overlapping"):
        manifest = json.loads((tmp_path / event / "manifest.json").read_text(encoding="utf-8"))
        records = manifest["source_frames"]
        assert [(record["run_id"], record["frame_id"], record["camera_id"]) for record in records] == [
            ("source-run", seq, "CAM01") for seq in range(5)
        ]
        assert [record["file"] for record in records] == [p.name for p in sorted((tmp_path / event / "frames").glob("*.jpg"))]
        assert manifest["frame_count"] == len(records) == 5
        assert manifest["window_complete"] is True


def test_legacy_frames_are_unknown_identity_not_invented(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=0, post_seconds=0, fps=2)
    assert recorder.start({"id": "legacy", "camera_id": "CAM01", "triggered_at": 10})["ok"]
    recorder.ingest("CAM01", 10, np.zeros((48, 64, 3), dtype=np.uint8))
    manifest = json.loads((tmp_path / "legacy" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_frames"][0]["run_id"] is None
    assert manifest["source_frames"][0]["frame_id"] is None


def test_one_sample_command_lag_does_not_expire_requested_pre_window(tmp_path: Path):
    """A frame can reach evidence before a start command for its predecessor."""
    recorder = EvidenceRecorder(tmp_path, pre_seconds=30, post_seconds=60, fps=2)
    image = np.full((180, 240, 3), 100, dtype=np.uint8)
    for seq in range(62):  # source 100 .. 130.5, event occurred at 130
        recorder.ingest("CAM01", 100 + seq * 0.5, image, run_id="lag-run", frame_id=seq)
    assert recorder.start({"id": "lagged", "camera_id": "CAM01", "triggered_at": 130})["ok"]
    for seq in range(62, 181):
        recorder.ingest("CAM01", 100 + seq * 0.5, image, run_id="lag-run", frame_id=seq)
    manifest = json.loads((tmp_path / "lagged" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["window_complete"] is True
    assert manifest["gaps"] == []
    assert [entry["frame_id"] for entry in manifest["source_frames"]] == list(range(181))


def test_command_delay_beyond_bounded_cache_is_still_incomplete(tmp_path: Path):
    recorder = EvidenceRecorder(tmp_path, pre_seconds=30, post_seconds=60, fps=2)
    image = np.full((180, 240, 3), 100, dtype=np.uint8)
    for seq in range(67):  # Deliberately 3 seconds late, not covered by one-sample grace.
        recorder.ingest("CAM01", 100 + seq * 0.5, image, run_id="late-run", frame_id=seq)
    assert recorder.start({"id": "too-late", "camera_id": "CAM01", "triggered_at": 130})["ok"]
    recorder.close()
    manifest = json.loads((tmp_path / "too-late" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["window_complete"] is False
    assert manifest["gaps"][0][0] == 100
