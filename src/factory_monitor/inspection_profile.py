"""Bounded, local-only validation for active-inspection learning receipts.

The evaluator verifies the shape and ordering of a recorded replay.  It does
not inspect an operating-system action, capture device, or client window, and
therefore can never authorize automatic desktop control.
"""

from __future__ import annotations

import math
import re
from typing import Any


_FINGERPRINT = re.compile(r"[0-9a-fA-F]{64}\Z")
_TIMESTAMP_FIELDS = (
    "requested_at",
    "detail_frame_at",
    "grid_requested_at",
    "grid_frame_at",
)
_BOOL_FIELDS = ("detail_verified", "grid_verified", "target_guard_verified")
_RECORD_FIELDS = (
    "cycle_id",
    "profile_fingerprint",
    "source",
    "camera_id",
    *_TIMESTAMP_FIELDS,
    "detail_camera_id",
    *_BOOL_FIELDS,
)


def _is_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _require_number(value: object, name: str, *, minimum: float = 0.0) -> float:
    if not _is_number(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return float(value)


def _require_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_nonempty_string(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_profile(profile: object) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")

    required = {
        "schema_version",
        "profile_id",
        "fingerprint",
        "camera_ids",
        "client",
        "source_size",
        "dpi_scale",
        "min_cycles_per_camera",
        "max_readback_seconds",
    }
    missing = sorted(required - profile.keys())
    if missing:
        raise ValueError(f"profile missing required fields: {', '.join(missing)}")

    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    _require_nonempty_string(profile["profile_id"], "profile_id")
    fingerprint = profile["fingerprint"]
    if type(fingerprint) is not str or not _FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("fingerprint must be exactly 64 hexadecimal characters")

    camera_ids = profile["camera_ids"]
    if type(camera_ids) is not list or not camera_ids:
        raise ValueError("camera_ids must be a non-empty unique list")
    if any(type(camera_id) is not str or not camera_id.strip() for camera_id in camera_ids):
        raise ValueError("camera_ids must contain non-empty strings")
    if len(set(camera_ids)) != len(camera_ids):
        raise ValueError("camera_ids must be unique")

    client = profile["client"]
    if type(client) is not dict:
        raise ValueError("client must be an object with name and version")
    _require_nonempty_string(client.get("name"), "client.name")
    _require_nonempty_string(client.get("version"), "client.version")

    source_size = profile["source_size"]
    if type(source_size) is not list or len(source_size) != 2:
        raise ValueError("source_size must be [width, height]")
    for dimension in source_size:
        _require_positive_int(dimension, "source_size")

    _require_number(profile["dpi_scale"], "dpi_scale", minimum=0.000001)
    _require_positive_int(profile["min_cycles_per_camera"], "min_cycles_per_camera")
    _require_number(profile["max_readback_seconds"], "max_readback_seconds", minimum=0.000001)
    return profile


def _record_reason(record: object, profile: dict[str, Any], duplicate_ids: set[str]) -> str | None:
    if type(record) is not dict:
        return "record must be an object"

    missing = [field for field in _RECORD_FIELDS if field not in record]
    if missing:
        return f"missing required fields: {', '.join(missing)}"

    cycle_id = record["cycle_id"]
    if type(cycle_id) is not str or not cycle_id.strip():
        return "cycle_id must be a non-empty string"
    if cycle_id in duplicate_ids:
        return f"duplicate cycle_id: {cycle_id}"
    if record["profile_fingerprint"] != profile["fingerprint"]:
        return "profile_fingerprint does not match profile"
    if type(record["source"]) is not str or record["source"] not in ("synthetic", "onsite"):
        return "source must be synthetic or onsite"

    camera_id = record["camera_id"]
    if type(camera_id) is not str or camera_id not in profile["camera_ids"]:
        return "camera_id is not configured by profile"
    if record["detail_camera_id"] != camera_id:
        return "detail_camera_id does not match requested camera_id"

    timestamps = [record[field] for field in _TIMESTAMP_FIELDS]
    if not all(_is_number(timestamp) for timestamp in timestamps):
        return "timestamps must be finite nonnegative numbers"
    requested_at, detail_frame_at, grid_requested_at, grid_frame_at = timestamps
    if not requested_at < detail_frame_at < grid_requested_at < grid_frame_at:
        return "timestamps must be ordered requested < detail frame < grid request < grid frame"

    max_readback = profile["max_readback_seconds"]
    if detail_frame_at - requested_at > max_readback:
        return "detail frame exceeded max_readback_seconds"
    if grid_frame_at - grid_requested_at > max_readback:
        return "grid frame exceeded max_readback_seconds"

    for field in _BOOL_FIELDS:
        if type(record[field]) is not bool:
            return f"{field} must be a strict bool"
        if not record[field]:
            return f"{field} was not verified"
    return None


def _scope(*, synthetic_records: int, onsite_records: int, valid_onsite_records: int) -> str:
    if synthetic_records and not onsite_records:
        return "synthetic-only"
    if valid_onsite_records and not synthetic_records:
        return "onsite-structural-replay"
    if synthetic_records or onsite_records:
        return "mixed-local-replay"
    return "no-valid-evidence"


def evaluate_profile(profile: dict, records: list[dict]) -> dict:
    """Audit a profile and local replay records without authorizing control.

    Invalid profile parameters raise :class:`ValueError`.  Invalid replay
    entries remain in the returned failed count so that they cannot quietly
    improve coverage or success totals.
    """

    validated_profile = _validate_profile(profile)
    replay_records: list[object] = records if type(records) is list else [records]
    cycle_ids = [
        record.get("cycle_id")
        for record in replay_records
        if type(record) is dict
        and type(record.get("cycle_id")) is str
        and record["cycle_id"].strip()
    ]
    cycle_id_counts: dict[str, int] = {}
    for cycle_id in cycle_ids:
        cycle_id_counts[cycle_id] = cycle_id_counts.get(cycle_id, 0) + 1
    duplicate_ids = {
        cycle_id
        for cycle_id, count in cycle_id_counts.items()
        if count > 1
    }

    reasons: list[str] = []
    successful_by_camera = {camera_id: 0 for camera_id in validated_profile["camera_ids"]}
    records_valid = 0
    synthetic_records = 0
    onsite_records = 0
    valid_onsite_records = 0
    last_grid_frame_at = None

    for index, record in enumerate(replay_records):
        if type(record) is dict and record.get("source") == "synthetic":
            synthetic_records += 1
        elif type(record) is dict and record.get("source") == "onsite":
            onsite_records += 1

        reason = _record_reason(record, validated_profile, duplicate_ids)
        if reason is None:
            if last_grid_frame_at is not None and record["requested_at"] < last_grid_frame_at:
                reason = "single-window cycles overlap or are out of order"
            last_grid_frame_at = max(last_grid_frame_at or 0, record["grid_frame_at"])
        if reason is not None:
            reasons.append(f"record {index}: {reason}")
            continue

        records_valid += 1
        camera_id = record["camera_id"]
        successful_by_camera[camera_id] += 1
        if record["source"] == "onsite":
            valid_onsite_records += 1

    minimum = validated_profile["min_cycles_per_camera"]
    for camera_id, successful_cycles in successful_by_camera.items():
        if successful_cycles < minimum:
            reasons.append(f"{camera_id}: {successful_cycles} successful cycles; requires {minimum}")

    records_total = len(replay_records)
    counts = {
        "records_total": records_total,
        "records_valid": records_valid,
        "records_failed": records_total - records_valid,
        "synthetic_records": synthetic_records,
        "onsite_records": onsite_records,
        "required_cycles": len(successful_by_camera) * minimum,
        "successful_cycles": sum(successful_by_camera.values()),
    }
    return {
        "replay_gate": "PASS" if not reasons else "FAIL",
        "counts": counts,
        "reasons": reasons,
        "field_evidence_present": valid_onsite_records > 0,
        "automatic_control_authorized": False,
        "evidence_scope": _scope(
            synthetic_records=synthetic_records,
            onsite_records=onsite_records,
            valid_onsite_records=valid_onsite_records,
        ),
    }
