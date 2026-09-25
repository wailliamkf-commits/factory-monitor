"""Local-replay acceptance tests for calibrated inspection profiles."""

from __future__ import annotations

from copy import deepcopy

import pytest

from factory_monitor.inspection_profile import evaluate_profile


FINGERPRINT = "a" * 64


def _profile(**overrides: object) -> dict:
    profile = {
        "schema_version": 1,
        "profile_id": "seetong-1.0.13.4-lab-profile",
        "fingerprint": FINGERPRINT,
        "camera_ids": ["CAM01", "CAM02"],
        "client": {"name": "Seetong", "version": "1.0.13.4"},
        "source_size": [1920, 1080],
        "dpi_scale": 1.25,
        "min_cycles_per_camera": 2,
        "max_readback_seconds": 3.0,
    }
    profile.update(overrides)
    return profile


def _record(camera_id: str, cycle: int, *, source: str = "synthetic", **overrides: object) -> dict:
    start = cycle * 10.0
    record = {
        "cycle_id": f"{camera_id}-cycle-{cycle}",
        "profile_fingerprint": FINGERPRINT,
        "source": source,
        "camera_id": camera_id,
        "requested_at": start,
        "detail_frame_at": start + 0.5,
        "grid_requested_at": start + 1.0,
        "grid_frame_at": start + 1.5,
        "detail_camera_id": camera_id,
        "detail_verified": True,
        "grid_verified": True,
        "target_guard_verified": True,
    }
    record.update(overrides)
    return record


def _complete_records(*, source: str = "synthetic") -> list[dict]:
    return [
        _record(camera_id, cycle + offset * 2, source=source)
        for offset, camera_id in enumerate(("CAM01", "CAM02"))
        for cycle in (1, 2)
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("profile_id", ""),
        ("fingerprint", "not-a-full-fingerprint"),
        ("camera_ids", ["CAM01", "CAM01"]),
        ("client", {"name": "Seetong"}),
        ("source_size", [1920, True]),
        ("dpi_scale", float("nan")),
        ("min_cycles_per_camera", 1.0),
        ("max_readback_seconds", False),
    ],
)
def test_invalid_profile_contract_is_rejected(field: str, value: object):
    with pytest.raises(ValueError, match=field):
        evaluate_profile(_profile(**{field: value}), [])


def test_complete_synthetic_replay_passes_only_as_non_field_evidence():
    result = evaluate_profile(_profile(), _complete_records())

    assert result["replay_gate"] == "PASS"
    assert result["counts"] == {
        "records_total": 4,
        "records_valid": 4,
        "records_failed": 0,
        "synthetic_records": 4,
        "onsite_records": 0,
        "required_cycles": 4,
        "successful_cycles": 4,
    }
    assert result["reasons"] == []
    assert result["field_evidence_present"] is False
    assert result["automatic_control_authorized"] is False
    assert result["evidence_scope"] == "synthetic-only"


def test_onsite_structural_receipts_are_present_but_cannot_authorize_automatic_control():
    result = evaluate_profile(_profile(), _complete_records(source="onsite"))

    assert result["replay_gate"] == "PASS"
    assert result["field_evidence_present"] is True
    assert result["automatic_control_authorized"] is False
    assert result["evidence_scope"] == "onsite-structural-replay"


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile_fingerprint": "b" * 64},
        {"camera_id": "CAM99", "detail_camera_id": "CAM99"},
        {"detail_camera_id": "CAM02"},
        {"detail_frame_at": 10.0},
        {"grid_frame_at": 14.1},
        {"grid_requested_at": 10.4},
        {"detail_verified": 1},
        {"grid_verified": False},
        {"target_guard_verified": False},
    ],
)
def test_wrong_or_failed_receipt_is_counted_as_a_failed_replay(overrides: dict):
    records = _complete_records()
    records[0].update(overrides)

    result = evaluate_profile(_profile(), records)

    assert result["replay_gate"] == "FAIL"
    assert result["counts"]["records_total"] == 4
    assert result["counts"]["records_valid"] == 3
    assert result["counts"]["records_failed"] == 1
    assert result["counts"]["successful_cycles"] == 3
    assert result["reasons"]


def test_duplicate_cycle_invalidates_every_occurrence_and_cannot_be_counted_twice():
    records = _complete_records()
    duplicate = deepcopy(records[0])
    duplicate["requested_at"] = 50.0
    duplicate["detail_frame_at"] = 50.5
    duplicate["grid_requested_at"] = 51.0
    duplicate["grid_frame_at"] = 51.5
    records.append(duplicate)

    result = evaluate_profile(_profile(), records)

    assert result["replay_gate"] == "FAIL"
    assert result["counts"]["records_total"] == 5
    assert result["counts"]["records_valid"] == 3
    assert result["counts"]["records_failed"] == 2
    assert result["counts"]["successful_cycles"] == 3
    assert any("duplicate cycle_id" in reason for reason in result["reasons"])


def test_coverage_shortfall_fails_even_when_every_supplied_record_is_valid():
    records = [record for record in _complete_records() if record["camera_id"] == "CAM01"]

    result = evaluate_profile(_profile(), records)

    assert result["replay_gate"] == "FAIL"
    assert result["counts"]["records_valid"] == 2
    assert result["counts"]["records_failed"] == 0
    assert result["counts"]["successful_cycles"] == 2
    assert any("CAM02" in reason and "requires 2" in reason for reason in result["reasons"])


def test_self_reported_passed_flag_is_not_evidence():
    records = _complete_records()
    records[0]["passed"] = True
    records[0]["target_guard_verified"] = False

    result = evaluate_profile(_profile(), records)

    assert result["replay_gate"] == "FAIL"
    assert result["counts"]["records_failed"] == 1


def test_overlapping_and_out_of_order_single_window_cycles_fail():
    records = _complete_records()
    records[2] = _record("CAM02", 1)
    assert evaluate_profile(_profile(), records)["replay_gate"] == "FAIL"


@pytest.mark.parametrize("timestamps", [(-4, -3.5, -3, -2.5), (10**400, 11, 12, 13)])
def test_negative_or_unrepresentable_timestamps_are_counted_failures(timestamps):
    records = _complete_records()
    records[0].update(zip(("requested_at", "detail_frame_at", "grid_requested_at", "grid_frame_at"), timestamps))
    assert evaluate_profile(_profile(), records)["counts"]["records_failed"] == 1


def test_huge_profile_numbers_raise_value_error():
    with pytest.raises(ValueError):
        evaluate_profile(_profile(dpi_scale=10**400), [])


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        (field, value)
        for field in (
            "cycle_id",
            "source",
            "camera_id",
            "detail_camera_id",
            "detail_verified",
            "grid_verified",
            "target_guard_verified",
            "requested_at",
            "detail_frame_at",
            "grid_requested_at",
            "grid_frame_at",
        )
        for value in (None, False, 1, 1.5, [], {}, "")
    ],
)
def test_every_invalid_json_type_in_record_fields_is_a_counted_failure(
    field: str, invalid_value: object
):
    records = _complete_records()
    records[0][field] = invalid_value

    result = evaluate_profile(_profile(), records)

    assert result["replay_gate"] == "FAIL"
    assert result["counts"]["records_total"] == 4
    assert result["counts"]["records_valid"] == 3
    assert result["counts"]["records_failed"] == 1
