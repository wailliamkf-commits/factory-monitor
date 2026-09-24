#!/usr/bin/env python3
"""Pixel-level synthetic-frame oracle for Phase 2 evidence verification.

This module is intentionally independent from manifests, filenames, SQLite and
the runtime ledger.  Its only proof inputs are (1) a source-written JSONL
oracle and (2) pixels decoded from JPEG or video evidence.  Each synthetic
full frame receives one compact, high-contrast marker in every camera tile.
The marker carries a visual-run tag, frame sequence and camera identity twice,
with CRC-16 error checks, so lossy JPEG/MP4 encoding cannot silently turn a
count-only comparison into a false pass.

Integration seam for the synthetic source::

    packet["image"] = inject_and_record_source_frame(
        packet["image"], run_id=run_id, frame_seq=frame_seq,
        oracle_path=source_oracle_path,
    )

Call it before putting the packet on any runtime queue.  This tool does not
alter production capture, evidence or model code.
"""

from __future__ import annotations

import argparse
import binascii
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import cv2
import numpy as np


DEFAULT_CAMERA_IDS = tuple(f"CAM{index:02d}" for index in range(1, 11))
GRID_COLUMNS = 4
GRID_ROWS = 3
MARKER_CELLS = 16
CELL_PIXELS = 4
MARKER_PIXELS = MARKER_CELLS * CELL_PIXELS
_MAGIC = 0xD3
_VERSION = 1
_PAYLOAD_BITS = 80
_COPIES = 2


class MarkerError(ValueError):
    """Decoded pixels do not prove the expected synthetic-frame identity."""


@dataclass(frozen=True)
class MarkerIdentity:
    run_tag: int
    frame_seq: int
    camera_id: str


@dataclass(frozen=True)
class DecodedFrame:
    by_camera: dict[str, MarkerIdentity]


@dataclass(frozen=True)
class FrameExpectation:
    run_id: str | int
    run_tag: int
    frame_seq: int
    camera_ids: tuple[str, ...]


@dataclass(frozen=True)
class VerificationReport:
    frame_count: int
    frame_sequences: tuple[int, ...]
    camera_ids: tuple[str, ...]


def visual_run_tag(run_id: str | int) -> int:
    """Return the 16-bit tag that is visually embedded for a source run."""
    if isinstance(run_id, int):
        if not 0 <= run_id <= 0xFFFF:
            raise ValueError("integer run_id must fit in 16 bits")
        return run_id
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string or a 16-bit integer")
    return binascii.crc_hqx(run_id.encode("utf-8"), 0x1D0F)


def _camera_number(camera_id: str) -> int:
    if not camera_id.startswith("CAM") or not camera_id[3:].isdigit():
        raise ValueError(f"camera_id must use CAMnn format, got {camera_id!r}")
    number = int(camera_id[3:])
    if not 1 <= number <= 15:
        raise ValueError(f"camera_id must be CAM01 through CAM15, got {camera_id!r}")
    return number


def _marker_payload(run_tag: int, frame_seq: int, camera_id: str) -> bytes:
    if not 0 <= frame_seq <= 0xFFFFFF:
        raise ValueError("frame_seq must fit in 24 bits")
    header = bytes([_MAGIC, _VERSION]) + run_tag.to_bytes(2, "big") + frame_seq.to_bytes(3, "big") + bytes([_camera_number(camera_id)])
    return header + binascii.crc_hqx(header, 0xFFFF).to_bytes(2, "big")


def _bits(payload: bytes) -> list[int]:
    return [bit for value in payload for bit in range(7, -1, -1) for bit in [(value >> bit) & 1]]


def _payload_from_bits(bits: Sequence[int]) -> bytes:
    if len(bits) != _PAYLOAD_BITS:
        raise MarkerError("marker payload bit count is invalid")
    return bytes(sum(bits[offset + bit] << (7 - bit) for bit in range(8)) for offset in range(0, len(bits), 8))


def _marker_bits(payload: bytes) -> np.ndarray:
    bits = _bits(payload)
    if len(bits) != _PAYLOAD_BITS:
        raise AssertionError("unexpected marker payload size")
    grid = np.zeros((MARKER_CELLS, MARKER_CELLS), dtype=np.uint8)
    # A fixed border makes marker loss visible before payload decoding.
    grid[0, :] = 1
    grid[:, 0] = 1
    inner = [(row, col) for row in range(1, MARKER_CELLS - 1) for col in range(1, MARKER_CELLS - 1)]
    repeated = bits * _COPIES
    for index, (row, col) in enumerate(inner):
        grid[row, col] = repeated[index] if index < len(repeated) else (index & 1)
    return grid


def _tile_bounds(image: np.ndarray, tile_index: int) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    if height < GRID_ROWS * MARKER_PIXELS or width < GRID_COLUMNS * MARKER_PIXELS:
        raise MarkerError(f"frame {width}x{height} is too small for the synthetic marker grid")
    tile_width, tile_height = width // GRID_COLUMNS, height // GRID_ROWS
    if tile_width < MARKER_PIXELS + 8 or tile_height < MARKER_PIXELS + 8:
        raise MarkerError("camera crop cannot hold the synthetic marker")
    col, row = tile_index % GRID_COLUMNS, tile_index // GRID_COLUMNS
    x0, y0 = col * tile_width, row * tile_height
    # Bottom-right avoids the evidence writer's timestamp at full-frame (8, 20).
    return x0 + tile_width - MARKER_PIXELS - 8, y0 + tile_height - MARKER_PIXELS - 8, tile_width, tile_height


def _crop_marker_origin(image: np.ndarray) -> tuple[int, int]:
    """Locate the marker in one already-cropped synthetic camera image."""
    height, width = image.shape[:2]
    if width < MARKER_PIXELS + 8 or height < MARKER_PIXELS + 8:
        raise MarkerError(f"camera crop {width}x{height} is too small for a synthetic marker")
    return width - MARKER_PIXELS - 8, height - MARKER_PIXELS - 8


def inject_frame_markers(
    image: np.ndarray,
    *,
    run_id: str | int,
    frame_seq: int,
    camera_ids: Sequence[str] = DEFAULT_CAMERA_IDS,
) -> np.ndarray:
    """Return a marked copy of a 4x3 synthetic full frame.

    The caller retains its source frame.  Marking all ten camera projections
    makes a full-frame identity insufficient on its own: every crop must also
    prove the camera it claims to represent.
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a BGR color frame")
    if len(camera_ids) != len(DEFAULT_CAMERA_IDS):
        raise ValueError("the synthetic oracle requires exactly ten camera ids")
    marked = image.copy()
    run_tag = visual_run_tag(run_id)
    for tile_index, camera_id in enumerate(camera_ids):
        x, y, _, _ = _tile_bounds(marked, tile_index)
        module = _marker_bits(_marker_payload(run_tag, frame_seq, camera_id))
        pixels = np.repeat(np.repeat(module * 255, CELL_PIXELS, axis=0), CELL_PIXELS, axis=1)
        marked[y : y + MARKER_PIXELS, x : x + MARKER_PIXELS] = np.repeat(pixels[:, :, None], 3, axis=2)
    return marked


def _decode_marker_at(image: np.ndarray, x: int, y: int, label: str) -> MarkerIdentity:
    marker = image[y : y + MARKER_PIXELS, x : x + MARKER_PIXELS]
    grayscale = cv2.cvtColor(marker, cv2.COLOR_BGR2GRAY)
    grid = np.empty((MARKER_CELLS, MARKER_CELLS), dtype=np.uint8)
    for row in range(MARKER_CELLS):
        for col in range(MARKER_CELLS):
            cell = grayscale[row * CELL_PIXELS + 1 : (row + 1) * CELL_PIXELS - 1, col * CELL_PIXELS + 1 : (col + 1) * CELL_PIXELS - 1]
            grid[row, col] = int(float(cell.mean()) >= 128.0)
    if not np.all(grid[0, :] == 1) or not np.all(grid[:, 0] == 1) or np.any(grid[-1, 1:] != 0) or np.any(grid[1:, -1] != 0):
        raise MarkerError(f"marker border failed in {label}")
    inner = [int(grid[row, col]) for row in range(1, MARKER_CELLS - 1) for col in range(1, MARKER_CELLS - 1)]
    first = _payload_from_bits(inner[:_PAYLOAD_BITS])
    second = _payload_from_bits(inner[_PAYLOAD_BITS : _PAYLOAD_BITS * _COPIES])
    if first != second:
        raise MarkerError(f"marker copy mismatch in {label}")
    header, supplied_crc = first[:-2], int.from_bytes(first[-2:], "big")
    if binascii.crc_hqx(header, 0xFFFF) != supplied_crc:
        raise MarkerError(f"marker CRC failed in {label}")
    if header[0] != _MAGIC or header[1] != _VERSION:
        raise MarkerError(f"marker format failed in {label}")
    return MarkerIdentity(
        run_tag=int.from_bytes(header[2:4], "big"),
        frame_seq=int.from_bytes(header[4:7], "big"),
        camera_id=f"CAM{header[7]:02d}",
    )


def decode_tile(image: np.ndarray) -> tuple[int, int, str]:
    """Decode one 240x180-style evidence crop as ``(run_tag, frame_seq, camera_id)``.

    This is the stable per-camera seam for an evidence JPEG or an individual
    camera's MP4 frame.  It uses only crop pixels, never clip names or a
    manifest.
    """
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise MarkerError("camera crop must be a BGR color image")
    x, y = _crop_marker_origin(image)
    marker = _decode_marker_at(image, x, y, "camera crop")
    return marker.run_tag, marker.frame_seq, marker.camera_id


def decode_frame_markers(
    image: np.ndarray,
    *,
    expected_camera_ids: Sequence[str] = DEFAULT_CAMERA_IDS,
) -> DecodedFrame:
    """Decode and validate every synthetic-camera marker in one evidence frame."""
    if len(expected_camera_ids) != len(DEFAULT_CAMERA_IDS):
        raise ValueError("expected_camera_ids must list exactly ten cameras")
    decoded: dict[str, MarkerIdentity] = {}
    for tile_index, expected_camera_id in enumerate(expected_camera_ids):
        x, y, _, _ = _tile_bounds(image, tile_index)
        marker = _decode_marker_at(image, x, y, f"camera tile {tile_index + 1}")
        if marker.camera_id != expected_camera_id:
            raise MarkerError(
                f"camera marker mismatch in tile {tile_index + 1}: expected {expected_camera_id}, got {marker.camera_id}"
            )
        decoded[expected_camera_id] = marker
    sequences = {marker.frame_seq for marker in decoded.values()}
    tags = {marker.run_tag for marker in decoded.values()}
    if len(sequences) != 1 or len(tags) != 1:
        raise MarkerError("camera projections disagree on frame identity")
    return DecodedFrame(decoded)


def _append_oracle(path: Path, expectation: FrameExpectation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "synthetic_source_oracle_v1",
        "run_id": expectation.run_id,
        "run_tag": expectation.run_tag,
        "frame_seq": expectation.frame_seq,
        "camera_ids": list(expectation.camera_ids),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def inject_and_record_source_frame(
    image: np.ndarray,
    *,
    run_id: str | int,
    frame_seq: int,
    oracle_path: str | Path,
    camera_ids: Sequence[str] = DEFAULT_CAMERA_IDS,
) -> np.ndarray:
    """Mark a source frame and write its independent expected identity before return."""
    marked = inject_frame_markers(image, run_id=run_id, frame_seq=frame_seq, camera_ids=camera_ids)
    _append_oracle(
        Path(oracle_path),
        FrameExpectation(run_id, visual_run_tag(run_id), frame_seq, tuple(camera_ids)),
    )
    return marked


def load_source_oracle(path: str | Path) -> tuple[FrameExpectation, ...]:
    """Load the source-side expectations; malformed or duplicate records fail closed."""
    records: list[FrameExpectation] = []
    seen: set[int] = set()
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                item = FrameExpectation(
                    payload["run_id"], int(payload["run_tag"]), int(payload["frame_seq"]), tuple(payload["camera_ids"])
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise MarkerError(f"invalid source oracle line {line_number}") from exc
            try:
                computed_tag = visual_run_tag(item.run_id)
            except ValueError as exc:
                raise MarkerError(f"source oracle integrity failed at line {line_number}") from exc
            if payload.get("kind") != "synthetic_source_oracle_v1" or item.run_tag != computed_tag:
                raise MarkerError(f"source oracle integrity failed at line {line_number}")
            if item.frame_seq in seen:
                raise MarkerError(f"duplicate source oracle frame_seq {item.frame_seq}")
            if item.camera_ids != DEFAULT_CAMERA_IDS:
                raise MarkerError(f"unexpected source camera grid at line {line_number}")
            seen.add(item.frame_seq)
            records.append(item)
    if not records:
        raise MarkerError("source oracle contains no frames")
    return tuple(records)


def _iter_media_frames(media_paths: Iterable[str | Path]) -> Iterator[np.ndarray]:
    for raw_path in media_paths:
        path = Path(raw_path)
        if path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"}:
            capture = cv2.VideoCapture(str(path))
            if not capture.isOpened():
                capture.release()
                raise MarkerError(f"could not open video media: {path}")
            decoded = 0
            try:
                while True:
                    ok, image = capture.read()
                    if not ok:
                        break
                    if image is None:
                        raise MarkerError(f"video returned an empty frame: {path}")
                    decoded += 1
                    yield image
            finally:
                capture.release()
            if not decoded:
                raise MarkerError(f"video had no decodable frames: {path}")
        else:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise MarkerError(f"could not decode image media: {path}")
            yield image


def verify_media(
    media_paths: Iterable[str | Path], expected: Sequence[FrameExpectation], *, camera_id: str | None = None
) -> VerificationReport:
    """Require exact source order from full-frame media or one camera's JPEG/MP4.

    Pass ``camera_id="CAM01"`` for evidence files whose frames are the real
    240x180 camera crop.  Omit it only for full 960x540 synthetic mosaics.
    """
    if not expected:
        raise MarkerError("expected source oracle is empty")
    actual_sequences: list[int] = []
    seen: set[int] = set()
    for index, image in enumerate(_iter_media_frames(media_paths)):
        if index >= len(expected):
            raise MarkerError(f"unexpected extra decoded frame at position {index}")
        wanted = expected[index]
        if camera_id is None:
            decoded = decode_frame_markers(image, expected_camera_ids=wanted.camera_ids)
            representative = next(iter(decoded.by_camera.values()))
        else:
            if camera_id not in wanted.camera_ids:
                raise MarkerError(f"camera {camera_id} is absent from source oracle")
            run_tag, frame_seq, decoded_camera_id = decode_tile(image)
            if decoded_camera_id != camera_id:
                raise MarkerError(f"camera marker mismatch: expected {camera_id}, got {decoded_camera_id}")
            representative = MarkerIdentity(run_tag, frame_seq, decoded_camera_id)
        if representative.run_tag != wanted.run_tag:
            raise MarkerError(f"wrong run tag at frame position {index}")
        if representative.frame_seq in seen:
            raise MarkerError(f"duplicate frame_seq {representative.frame_seq} at position {index}")
        if representative.frame_seq != wanted.frame_seq:
            remaining = {item.frame_seq for item in expected[index + 1 :]}
            if representative.frame_seq in remaining:
                raise MarkerError(f"frame order mismatch at position {index}")
            raise MarkerError(
                f"wrong frame_seq at position {index}: expected {wanted.frame_seq}, got {representative.frame_seq}"
            )
        seen.add(representative.frame_seq)
        actual_sequences.append(representative.frame_seq)
    if len(actual_sequences) != len(expected):
        raise MarkerError(f"missing media frames: expected {len(expected)}, decoded {len(actual_sequences)}")
    cameras = (camera_id,) if camera_id is not None else expected[0].camera_ids
    return VerificationReport(len(actual_sequences), tuple(actual_sequences), cameras)


def verify_ordered_media(
    media_paths: Iterable[str | Path], expected: Sequence[FrameExpectation]
) -> VerificationReport:
    """Backward-compatible full-frame mosaic verification entry point."""
    return verify_media(media_paths, expected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify synthetic source oracle against JPEG/MP4 pixels")
    parser.add_argument("--oracle", type=Path, required=True, help="source-written JSONL oracle")
    parser.add_argument("media", nargs="+", type=Path, help="JPEG images and/or complete video files")
    args = parser.parse_args()
    try:
        report = verify_media(args.media, load_source_oracle(args.oracle))
    except (OSError, MarkerError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "frame_count": report.frame_count, "frame_sequences": report.frame_sequences}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
