from __future__ import annotations

import sys
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = APP_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from qa_phase2_matrix import (  # noqa: E402
    MatrixFailure,
    MarkerError,
    _expected_window,
    event_media_paths,
    validate_normal_case,
)
from phase2_content_oracle import DEFAULT_CAMERA_IDS, FrameExpectation, visual_run_tag  # noqa: E402


def _source_records(timestamps: list[float]) -> tuple[tuple[FrameExpectation, ...], dict[int, dict]]:
    run_id = "q4-boundary-unit"
    tag = visual_run_tag(run_id)
    oracle = tuple(FrameExpectation(run_id, tag, sequence, DEFAULT_CAMERA_IDS) for sequence in range(len(timestamps)))
    context = {
        sequence: {"timestamp": timestamp, "monotonic": timestamp, "shape": [540, 960, 3], "payload_bytes": 1_555_200}
        for sequence, timestamp in enumerate(timestamps)
    }
    return oracle, context


def test_event_media_paths_uses_mp4_parent_for_the_jpeg_directory(tmp_path: Path) -> None:
    clip = tmp_path / "event-1" / "evidence.mp4"

    actual_clip, frames = event_media_paths({"evidence_path": str(clip)})

    assert actual_clip == clip
    assert frames == clip.parent / "frames"


def test_normal_case_refuses_a_stop_report_that_is_not_confirmed() -> None:
    with pytest.raises(MatrixFailure, match="confirmed"):
        validate_normal_case(
            stop={"confirmed": False, "queue_audit": {"state": "reconciled", "streams": {}}},
            rows=[],
            expected_event_ids=set(),
            media_verification=[],
        )


def test_expected_window_accepts_first_source_frame_after_post_target_within_one_sample_period() -> None:
    # 2 fps: EvidenceRecorder intentionally finalizes at the first source frame >= end.
    oracle, context = _source_records([70.0, 159.997, 160.497])

    expected = _expected_window({"id": "event", "triggered_at": 100.0}, oracle, context)

    assert [item.frame_seq for item in expected] == [0, 1, 2]


def test_expected_window_rejects_post_target_overshoot_beyond_one_sample_period() -> None:
    oracle, context = _source_records([70.0, 159.997, 160.501])

    with pytest.raises(MarkerError, match="post-window"):
        _expected_window({"id": "event", "triggered_at": 100.0}, oracle, context)


def test_expected_window_still_rejects_a_pre_window_start_gap_larger_than_half_frame() -> None:
    oracle, context = _source_records([69.74, 70.26, 160.0])

    with pytest.raises(MarkerError, match="pre-window"):
        _expected_window({"id": "event", "triggered_at": 100.0}, oracle, context)
