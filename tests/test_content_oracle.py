from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = APP_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from phase2_content_oracle import (  # noqa: E402
    DEFAULT_CAMERA_IDS,
    MarkerError,
    decode_frame_markers,
    decode_tile,
    inject_and_record_source_frame,
    load_source_oracle,
    verify_media,
    verify_ordered_media,
)


def _base_frame() -> np.ndarray:
    """Match the real synthetic source's full-frame dimensions and grid."""
    return np.full((540, 960, 3), (31, 36, 41), dtype=np.uint8)


def _write_jpeg(path: Path, image: np.ndarray) -> Path:
    assert cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return path


def _write_mp4(path: Path, images: list[np.ndarray]) -> Path:
    height, width = images[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 2.0, (width, height))
    assert writer.isOpened()
    try:
        for image in images:
            writer.write(image)
    finally:
        writer.release()
    return path


def _marked_frames(tmp_path: Path, sequences: list[int]) -> tuple[Path, list[np.ndarray]]:
    oracle_path = tmp_path / "source-oracle.jsonl"
    images = [
        inject_and_record_source_frame(_base_frame(), run_id="R020-Q4A", frame_seq=sequence, oracle_path=oracle_path)
        for sequence in sequences
    ]
    return oracle_path, images


def test_marker_survives_actual_lossy_jpeg_and_recovers_all_camera_identities(tmp_path: Path) -> None:
    oracle_path, images = _marked_frames(tmp_path, [17])

    decoded = decode_frame_markers(cv2.imread(str(_write_jpeg(tmp_path / "source.jpg", images[0]))))

    assert load_source_oracle(oracle_path)[0].frame_seq == 17
    assert [decoded.by_camera[camera_id].camera_id for camera_id in DEFAULT_CAMERA_IDS] == list(DEFAULT_CAMERA_IDS)
    assert {marker.frame_seq for marker in decoded.by_camera.values()} == {17}
    assert decode_tile(images[0][0:180, 0:240]) == (decoded.by_camera["CAM01"].run_tag, 17, "CAM01")


def test_content_oracle_accepts_ordered_jpeg_and_mp4_from_known_source(tmp_path: Path) -> None:
    oracle_path, images = _marked_frames(tmp_path, [101, 102, 103])
    jpeg_paths = [_write_jpeg(tmp_path / f"frame-{index}.jpg", image) for index, image in enumerate(images)]
    mp4_path = _write_mp4(tmp_path / "evidence.mp4", images)
    expected = load_source_oracle(oracle_path)

    jpeg_report = verify_ordered_media(jpeg_paths, expected)
    mp4_report = verify_ordered_media([mp4_path], expected)

    assert jpeg_report.frame_count == 3 and jpeg_report.frame_sequences == (101, 102, 103)
    assert mp4_report.frame_count == 3 and mp4_report.frame_sequences == (101, 102, 103)


def test_content_oracle_verifies_a_single_camera_mp4_crop_after_lossy_encoding(tmp_path: Path) -> None:
    oracle_path, images = _marked_frames(tmp_path, [301, 302, 303])
    single_camera_mp4 = _write_mp4(tmp_path / "CAM01-evidence.mp4", [image[0:180, 0:240] for image in images])

    report = verify_media([single_camera_mp4], load_source_oracle(oracle_path), camera_id="CAM01")

    assert report.frame_count == 3 and report.frame_sequences == (301, 302, 303)


@pytest.mark.parametrize(
    ("actual_sequences", "reason"),
    [([101, 101, 103], "duplicate"), ([102, 101, 103], "order")],
)
def test_content_oracle_rejects_duplicate_or_reordered_media_with_same_frame_count(
    tmp_path: Path, actual_sequences: list[int], reason: str
) -> None:
    oracle_path, expected_images = _marked_frames(tmp_path, [101, 102, 103])
    actual_images = [
        inject_and_record_source_frame(_base_frame(), run_id="R020-Q4A", frame_seq=sequence, oracle_path=tmp_path / "actual-expected-tag.jsonl")
        for sequence in actual_sequences
    ]
    media = _write_mp4(tmp_path / f"{reason}.mp4", actual_images)

    with pytest.raises(MarkerError, match=reason):
        verify_ordered_media([media], load_source_oracle(oracle_path))
    assert len(actual_images) == len(expected_images) == 3


def test_content_oracle_rejects_camera_marker_in_the_wrong_grid_crop_with_same_frame_count(tmp_path: Path) -> None:
    oracle_path, images = _marked_frames(tmp_path, [201])
    wrong_camera_ids = ("CAM02", "CAM01", *DEFAULT_CAMERA_IDS[2:])
    wrong = inject_and_record_source_frame(
        _base_frame(), run_id="R020-Q4A", frame_seq=201, oracle_path=tmp_path / "wrong-camera-source.jsonl",
        camera_ids=wrong_camera_ids,
    )
    media = _write_jpeg(tmp_path / "wrong-camera.jpg", wrong)

    with pytest.raises(MarkerError, match="camera"):
        verify_ordered_media([media], load_source_oracle(oracle_path))
    assert len(images) == 1
