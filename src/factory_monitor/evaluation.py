"""Transparent held-out evaluation accounting for candidate/review outcomes."""

from __future__ import annotations

from collections import Counter, defaultdict
from math import ceil
import math
from typing import Iterable


_OUTCOMES = {"supported", "dismissed", "unknown", "timeout", "missed", "error"}
_KINDS = ("material_candidate", "station_absence")
_STAGES = ("candidate", "final")
_CANDIDATE_TARGET_MS = 3_000.0
_REVIEW_TARGET_MS = 15_000.0


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(len(ordered) * 0.95) - 1]


def _latency(records: list[dict], field: str, target_ms: float) -> dict:
    values: list[float] = []
    failures = 0
    censored = 0
    for record in records:
        outcome = record["outcome"]
        raw = record.get(field)
        valid = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(float(raw)) and raw >= 0
        if not valid:
            censored += 1
        if outcome in {"unknown", "timeout", "missed", "error"} or not valid:
            failures += 1
        else:
            values.append(float(raw))
    prefix = field.removesuffix("_ms")
    result = {
        f"{prefix}_denominator": len(records),
        f"{prefix}_measured_denominator": len(values),
        f"{prefix}_p95_measured_ms": _percentile_95(values),
        f"{prefix}_sla_failure_samples": failures,
        f"{prefix}_censored_samples": censored,
        f"{prefix}_target_ms": target_ms,
    }
    if prefix == "candidate_latency":
        result["candidate_p95_measured_ms"] = result[f"{prefix}_p95_measured_ms"]
    elif prefix == "review_latency":
        result["review_p95_measured_ms"] = result[f"{prefix}_p95_measured_ms"]
    return result


def _bucket(records: list[dict]) -> dict:
    positive = [record for record in records if record["truth"] == "positive"]
    negative = [record for record in records if record["truth"] == "negative"]
    positive_successes = sum(record["outcome"] == "supported" for record in positive)
    negative_successes = sum(record["outcome"] == "dismissed" for record in negative)
    return {
        "sample_denominator": len(records),
        "positive_denominator": len(positive),
        "positive_successes": positive_successes,
        "positive_failures": len(positive) - positive_successes,
        "recall": positive_successes / len(positive) if positive else None,
        "negative_denominator": len(negative),
        "negative_successes": negative_successes,
        "negative_failures": len(negative) - negative_successes,
        "false_alarms": sum(record["outcome"] == "supported" for record in negative),
        "outcomes": dict(sorted(Counter(record["outcome"] for record in records).items())),
        "latency": {
            **_latency(records, "candidate_latency_ms", _CANDIDATE_TARGET_MS),
            **_latency(records, "review_latency_ms", _REVIEW_TARGET_MS),
        },
    }


def _gate_reasons(by_kind: dict[str, dict]) -> list[str]:
    reasons: list[str] = []
    for kind in _KINDS:
        for stage in _STAGES:
            bucket = by_kind[kind][stage]
            label = f"{kind}/{stage}"
            if bucket["positive_denominator"] < 50:
                reasons.append(f"{label}: fewer than 50 positive samples")
            if bucket["negative_denominator"] < 100:
                reasons.append(f"{label}: fewer than 100 negative/confuser samples")
            minimum_recall = 0.95 if stage == "candidate" else 0.90
            if bucket["recall"] is None or bucket["recall"] < minimum_recall:
                reasons.append(f"{label}: recall below {minimum_recall:.0%}")
            if stage == "candidate":
                p95 = bucket["latency"]["candidate_latency_p95_measured_ms"]
                if p95 is None or p95 > _CANDIDATE_TARGET_MS:
                    reasons.append(f"{label}: candidate p95 exceeds 3000ms or is unknown")
                if bucket["latency"]["candidate_latency_sla_failure_samples"]:
                    reasons.append(f"{label}: candidate latency has timeout/unknown/missed/error samples")
            else:
                p95 = bucket["latency"]["review_latency_p95_measured_ms"]
                if p95 is None or p95 > _REVIEW_TARGET_MS:
                    reasons.append(f"{label}: review p95 exceeds 15000ms or is unknown")
                if bucket["latency"]["review_latency_sla_failure_samples"]:
                    reasons.append(f"{label}: review latency has timeout/unknown/missed/error samples")
    return reasons


def evaluate_results(records: Iterable[dict], *, environment: str = "synthetic") -> dict:
    """Report evaluation counts without excluding unknown, timeout, or missed samples.

    Every record must name a V1 event kind, hand-labelled truth (positive or
    negative), stage (candidate/final), and outcome. ``unknown``, ``timeout``,
    ``missed`` and ``error`` remain in denominators and become failed latency
    samples. ``environment='synthetic'`` can validate software accounting but
    never passes the separate actual-field Gate.
    """
    if environment not in {"synthetic", "field"}:
        raise ValueError("environment must be 'synthetic' or 'field'")
    by_kind: dict[str, list[dict]] = defaultdict(list)
    by_kind_stage: dict[str, dict[str, list[dict]]] = {kind: {stage: [] for stage in _STAGES} for kind in _KINDS}
    normalized: list[dict] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"record {index} must be an object")
        kind, truth, outcome = record.get("kind"), record.get("truth"), record.get("outcome")
        if kind not in _KINDS:
            raise ValueError(f"record {index} has invalid kind")
        if truth not in {"positive", "negative"}:
            raise ValueError(f"record {index} has invalid truth")
        if outcome not in _OUTCOMES:
            raise ValueError(f"record {index} has invalid outcome")
        stage = record.get("stage", "final")
        if stage not in _STAGES:
            raise ValueError(f"record {index} has invalid stage")
        value = dict(record)
        value["stage"] = stage
        normalized.append(value)
        by_kind[kind].append(value)
        by_kind_stage[kind][stage].append(value)
    timeout_or_unknown = sum(record["outcome"] in {"timeout", "unknown"} for record in normalized)
    report_by_kind: dict[str, dict] = {}
    for kind in _KINDS:
        aggregate = _bucket(by_kind[kind])
        aggregate["candidate"] = _bucket(by_kind_stage[kind]["candidate"])
        aggregate["final"] = _bucket(by_kind_stage[kind]["final"])
        report_by_kind[kind] = aggregate
    quality_reasons = _gate_reasons(report_by_kind)
    field_reasons = [
        "not proven: this metric report cannot validate operating-system-specific field acceptance",
        "required evidence: target-OS 72-hour ten-camera run",
        "required evidence: 100 verified switching cycles with zero ID misbinding",
        "required evidence: normal-shift false-alarm exposure by camera",
        "required evidence: playable-or-explicitly-missing evidence and fault-test results",
    ]
    if environment != "field":
        field_reasons.insert(0, "not actual field validation")
    return {
        "sample_denominator": len(normalized),
        "environment": environment,
        "by_kind": report_by_kind,
        "failure_outcomes": {
            "timeout_or_unknown": timeout_or_unknown,
            "missed": sum(record["outcome"] == "missed" for record in normalized),
            "error": sum(record["outcome"] == "error" for record in normalized),
        },
        "metric_gate": {"status": "PASS" if not quality_reasons else "FAIL", "reasons": quality_reasons},
        "software_validation": {"status": "PASS" if not quality_reasons else "FAIL", "reasons": quality_reasons},
        "field_gate": {"status": "FAIL", "reasons": field_reasons},
    }
