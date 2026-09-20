"""Contract tests for the V1 core boundaries.

Each assertion catches a safety-relevant production mutation: accepting a cloud
endpoint, carrying an absence across a blind interval, pruning active evidence,
or treating unverified switching/review results as normal.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factory_monitor.config import default_config, load_config, save_config, validate_config
from factory_monitor.evaluation import evaluate_results
from factory_monitor.rules import RuleEngine
from factory_monitor.store import EventStore
from factory_monitor.switching import SwitchPolicy


def configured_camera_config() -> dict:
    config = default_config()
    config["source"].update({"calibrated": True, "expected_size": [1920, 1080], "window_title": "Synthetic calibrated test window"})
    camera = config["cameras"][0]
    camera["material_roi"] = [[0.4, 0.4], [0.9, 0.4], [0.9, 0.9], [0.4, 0.9]]
    camera["station_roi"] = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    camera["exit_line"] = [[0.5, 0.0], [0.5, 1.0]]
    camera["absence_seconds"] = 300
    camera["schedule"] = {"days": list(range(7)), "active": [["00:00", "24:00"]], "breaks": []}
    return config


def observation(timestamp: float, people: list[dict], **overrides: object) -> dict:
    value = {
        "camera_id": "CAM01",
        "timestamp": timestamp,
        "health": "observable",
        "layout_version": 1,
        "people": people,
    }
    value.update(overrides)
    return value


class ConfigContractTests(unittest.TestCase):
    def test_default_configuration_has_ten_safe_unconfigured_cameras(self) -> None:
        config = default_config()

        self.assertEqual(10, len(config["cameras"]))
        self.assertFalse(config["source"]["calibrated"])
        self.assertFalse(config["review"]["cloud_enabled"])
        self.assertEqual("127.0.0.1", config["review"]["endpoint"].split("//", 1)[1].split(":", 1)[0])
        self.assertTrue(all(len(camera["crop"]) == 4 for camera in config["cameras"]))

    def test_validation_rejects_cloud_and_non_loopback_review_endpoint(self) -> None:
        config = default_config()
        config["review"]["endpoint"] = "https://review.example.test"
        with self.assertRaisesRegex(ValueError, "loopback"):
            validate_config(config)

        config = default_config()
        config["review"]["cloud_enabled"] = True
        with self.assertRaisesRegex(ValueError, "cloud_enabled"):
            validate_config(config)

    def test_calibrated_live_source_requires_real_dimensions_and_explicit_source_identity(self) -> None:
        config = default_config()
        config["source"]["calibrated"] = True
        with self.assertRaisesRegex(ValueError, "expected_size"):
            validate_config(config)

        config["source"]["expected_size"] = [1920, 1080]
        with self.assertRaisesRegex(ValueError, "window_title"):
            validate_config(config)

        config["source"]["window_title"] = "Factory NVR — authenticated operator window"
        validate_config(config)

    def test_save_is_validated_atomic_and_loads_the_full_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            config = default_config()
            config["detection"]["fps"] = 7
            save_config(config, path)

            self.assertEqual(config, load_config(path))
            self.assertFalse(list(path.parent.glob("*.tmp")))

            unsafe = default_config()
            unsafe["review"]["endpoint"] = "http://192.168.1.42:11434"
            with self.assertRaises(ValueError):
                save_config(unsafe, path)
            self.assertEqual(7, load_config(path)["detection"]["fps"])


class RuleContractTests(unittest.TestCase):
    def test_material_candidate_is_emitted_on_entry_and_deduplicated_by_track(self) -> None:
        config = configured_camera_config()
        config["cameras"][0]["exit_line"] = []
        engine = RuleEngine(config)
        outside = [{"track_id": 7, "bbox": [0.1, 0.1, 0.2, 0.2], "confidence": 0.9}]
        inside = [{"track_id": 7, "bbox": [0.5, 0.5, 0.6, 0.7], "confidence": 0.9}]

        self.assertEqual([], engine.observe(observation(100.0, outside)))
        events = engine.observe(observation(101.0, inside))

        self.assertEqual(["material_candidate"], [event["kind"] for event in events])
        self.assertEqual([], engine.observe(observation(102.0, inside)))

    def test_absence_requires_continuous_observable_active_time_and_resets_after_gap(self) -> None:
        engine = RuleEngine(configured_camera_config())

        self.assertEqual([], engine.observe(observation(0.0, [])))
        for timestamp in range(1, 300):
            self.assertEqual([], engine.observe(observation(float(timestamp), [])))
        events = engine.observe(observation(300.0, []))
        self.assertEqual(["station_absence"], [event["kind"] for event in events])

        engine.reset("CAM01")
        self.assertEqual([], engine.observe(observation(1_000.0, [])))
        self.assertEqual([], engine.observe(observation(1_050.0, [], health="blind")))
        self.assertEqual([], engine.observe(observation(1_051.0, [])))
        self.assertEqual([], engine.observe(observation(1_351.0, [])))

    def test_exit_line_requires_crossing_the_configured_segment_not_its_infinite_extension(self) -> None:
        config = configured_camera_config()
        config["cameras"][0]["material_roi"] = []
        config["cameras"][0]["exit_line"] = [[0.5, 0.4], [0.5, 0.6]]
        engine = RuleEngine(config)

        self.assertEqual([], engine.observe(observation(0.0, [{"track_id": 1, "bbox": [0.4, 0.05, 0.45, 0.1], "confidence": 1.0}])))
        self.assertEqual([], engine.observe(observation(1.0, [{"track_id": 1, "bbox": [0.55, 0.05, 0.6, 0.1], "confidence": 1.0}])))

        engine.reset("CAM01")
        self.assertEqual([], engine.observe(observation(2.0, [{"track_id": 2, "bbox": [0.4, 0.4, 0.45, 0.5], "confidence": 1.0}])))
        events = engine.observe(observation(3.0, [{"track_id": 2, "bbox": [0.55, 0.4, 0.6, 0.5], "confidence": 1.0}]))
        self.assertEqual(["crossed_exit_line"], [event["reason"] for event in events])

    def test_departed_track_dedupe_expires_after_bounded_ttl_without_immediate_repeat(self) -> None:
        config = configured_camera_config()
        config["cameras"][0]["exit_line"] = []
        engine = RuleEngine(config)
        outside = [{"track_id": 99, "bbox": [0.1, 0.1, 0.2, 0.2], "confidence": 1.0}]
        inside = [{"track_id": 99, "bbox": [0.5, 0.5, 0.6, 0.7], "confidence": 1.0}]

        self.assertEqual([], engine.observe(observation(0.0, outside)))
        self.assertEqual(["material_candidate"], [event["kind"] for event in engine.observe(observation(1.0, inside))])
        for timestamp in range(2, 63):
            self.assertEqual([], engine.observe(observation(float(timestamp), [])))
        self.assertEqual([], engine.observe(observation(63.0, outside)))
        self.assertEqual(["material_candidate"], [event["kind"] for event in engine.observe(observation(64.0, inside))])


class StoreContractTests(unittest.TestCase):
    def test_prune_returns_only_finalized_paths_and_never_inflight_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "events.sqlite3")
            store.create_event({"id": "done", "camera_id": "CAM01", "kind": "material_candidate", "triggered_at": 1.0, "status": "complete", "reason": "entry", "layout_version": 1, "completed_at": 2.0, "evidence_path": "safe/done.mp4"})
            store.create_event({"id": "active", "camera_id": "CAM02", "kind": "station_absence", "triggered_at": 3.0, "status": "recording", "reason": "absence", "layout_version": 1, "evidence_path": "safe/active.mp4"})

            pruned = store.prune_completed(retain=0)
            self.assertEqual(["done"], [event["id"] for event in pruned])
            self.assertIsNone(store.get_event("done"))
            self.assertEqual("recording", store.get_event("active")["status"])
            store.close()

    def test_prune_preserves_completed_evidence_while_analysis_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "events.sqlite3")
            store.create_event({"id": "pending-analysis", "camera_id": "CAM01", "kind": "material_candidate", "triggered_at": 1.0, "status": "complete", "reason": "entry", "layout_version": 1, "completed_at": 2.0, "analysis_status": "pending", "evidence_path": "safe/pending.mp4"})

            self.assertEqual([], store.prune_completed(retain=0))
            self.assertIsNotNone(store.get_event("pending-analysis"))
            store.update_event("pending-analysis", analysis_status="supported")
            self.assertEqual(["pending-analysis"], [event["id"] for event in store.prune_completed(retain=0)])
            store.close()

    def test_reopen_marks_interrupted_recording_incomplete_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            store = EventStore(path)
            store.create_event({"id": "interrupted", "camera_id": "CAM01", "kind": "material_candidate", "triggered_at": 1.0, "status": "recording", "reason": "entry", "layout_version": 1, "recording_status": "recording"})
            store.close()

            recovered = EventStore(path)
            self.assertEqual(["interrupted"], recovered.recover_interrupted_recordings())
            event = recovered.get_event("interrupted")
            self.assertEqual("incomplete", event["status"])
            self.assertEqual("incomplete", event["recording_status"])
            self.assertTrue(any("crash" in value for value in event["gaps"]))
            recovered.close()

    def test_concurrent_reader_does_not_mutate_a_live_recording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            writer = EventStore(path)
            writer.create_event({"id": "live", "camera_id": "CAM01", "kind": "material_candidate", "triggered_at": 1.0, "status": "recording", "reason": "entry", "layout_version": 1, "recording_status": "recording"})

            reader = EventStore(path)
            self.assertEqual("recording", reader.get_event("live")["status"])
            reader.close()
            self.assertEqual("recording", writer.get_event("live")["status"])
            writer.close()


class SwitchingContractTests(unittest.TestCase):
    def test_material_has_priority_and_failed_return_disables_automation(self) -> None:
        config = configured_camera_config()
        config["switching"].update({"enabled": True, "calibrated": True})
        policy = SwitchPolicy(config)
        policy.enqueue("CAM01", "station_absence", 100.0)
        policy.enqueue("CAM02", "material_candidate", 101.0)

        self.assertEqual({"action": "detail", "camera_id": "CAM02"}, policy.next_action(102.0))
        policy.confirm("CAM02", 102.1, verified=True)
        self.assertEqual({"action": "grid"}, policy.next_action(117.1))
        policy.confirm(None, 117.2, verified=False)

        snapshot = policy.snapshot(118.0)
        self.assertFalse(snapshot["automation_enabled"])
        self.assertEqual("unknown", policy.health("CAM02", 118.0))
        self.assertIsNone(policy.next_action(118.0))

    def test_unverified_switch_request_expires_fail_closed(self) -> None:
        config = configured_camera_config()
        config["switching"].update({"enabled": True, "calibrated": True, "detail_seconds": 5})
        policy = SwitchPolicy(config)
        policy.enqueue("CAM01", "material_candidate", 0.0)
        self.assertEqual({"action": "detail", "camera_id": "CAM01"}, policy.next_action(0.0))

        self.assertIsNone(policy.next_action(5.1))
        self.assertFalse(policy.snapshot(5.1)["automation_enabled"])
        policy.confirm("CAM01", 5.2, verified=True)
        self.assertEqual("unknown", policy.health("CAM01", 5.2))

    def test_normal_grid_remains_observable_when_automation_is_not_enabled(self) -> None:
        policy = SwitchPolicy(default_config())
        self.assertEqual("observable", policy.health("CAM01", 0.0))

    def test_detail_dwell_and_blind_budget_start_when_the_action_is_requested(self) -> None:
        config = configured_camera_config()
        config["switching"].update({"enabled": True, "calibrated": True, "detail_seconds": 5})
        policy = SwitchPolicy(config)
        policy.enqueue("CAM01", "material_candidate", 10.0)
        self.assertEqual({"action": "detail", "camera_id": "CAM01"}, policy.next_action(10.0))
        policy.confirm("CAM01", 14.0, verified=True)
        self.assertEqual({"action": "grid"}, policy.next_action(15.0))
        policy.confirm(None, 20.0, verified=True)
        self.assertEqual(10.0, policy.snapshot(20.0)["blind_seconds_rolling_hour"]["CAM02"])

    def test_unverified_grid_return_expires_instead_of_hanging(self) -> None:
        config = configured_camera_config()
        config["switching"].update({"enabled": True, "calibrated": True, "detail_seconds": 5})
        policy = SwitchPolicy(config)
        policy.enqueue("CAM01", "material_candidate", 0.0)
        policy.next_action(0.0)
        policy.confirm("CAM01", 0.0, verified=True)
        self.assertEqual({"action": "grid"}, policy.next_action(5.0))
        self.assertIsNone(policy.next_action(10.1))
        self.assertFalse(policy.snapshot(10.1)["automation_enabled"])


class EvaluationContractTests(unittest.TestCase):
    def test_unknown_timeout_and_missed_are_failures_with_explicit_denominators(self) -> None:
        report = evaluate_results([
            {"id": "p1", "kind": "material_candidate", "truth": "positive", "outcome": "supported"},
            {"id": "p2", "kind": "material_candidate", "truth": "positive", "outcome": "timeout"},
            {"id": "p3", "kind": "material_candidate", "truth": "positive", "outcome": "missed"},
            {"id": "n1", "kind": "material_candidate", "truth": "negative", "outcome": "supported"},
            {"id": "n2", "kind": "material_candidate", "truth": "negative", "outcome": "unknown"},
        ])

        material = report["by_kind"]["material_candidate"]
        self.assertEqual(3, material["positive_denominator"])
        self.assertEqual(2, material["positive_failures"])
        self.assertAlmostEqual(1 / 3, material["recall"])
        self.assertEqual(2, material["negative_denominator"])
        self.assertEqual(2, material["negative_failures"])
        self.assertEqual(2, report["failure_outcomes"]["timeout_or_unknown"])

    def test_synthetic_report_cannot_pass_field_gate_and_zero_samples_are_not_perfect(self) -> None:
        report = evaluate_results([], environment="synthetic")

        self.assertEqual("FAIL", report["field_gate"]["status"])
        self.assertIsNone(report["by_kind"]["material_candidate"]["final"]["recall"])
        self.assertIn("not actual field validation", report["field_gate"]["reasons"])

    def test_latency_gate_includes_timeout_as_a_failure_sample(self) -> None:
        records = []
        for index in range(50):
            records.append({"id": f"p{index}", "kind": "material_candidate", "truth": "positive", "outcome": "supported", "stage": "candidate", "candidate_latency_ms": 100.0})
        for index in range(100):
            records.append({"id": f"n{index}", "kind": "material_candidate", "truth": "negative", "outcome": "dismissed", "stage": "candidate", "candidate_latency_ms": 100.0})
        for index in range(8):
            records.append({"id": f"timeout{index}", "kind": "material_candidate", "truth": "positive", "outcome": "timeout", "stage": "candidate"})

        report = evaluate_results(records, environment="synthetic")
        candidate = report["by_kind"]["material_candidate"]["candidate"]
        self.assertEqual(158, candidate["latency"]["candidate_latency_denominator"])
        self.assertEqual(8, candidate["latency"]["candidate_latency_sla_failure_samples"])
        self.assertEqual("FAIL", report["software_validation"]["status"])

    def test_field_label_and_passing_metrics_cannot_prove_the_overall_field_gate(self) -> None:
        records = []
        for kind in ("material_candidate", "station_absence"):
            for stage in ("candidate", "final"):
                records.extend(
                    {"id": f"{kind}-{stage}-p-{index}", "kind": kind, "stage": stage, "truth": "positive", "outcome": "supported", "candidate_latency_ms": 100.0, "review_latency_ms": 100.0}
                    for index in range(50)
                )
                records.extend(
                    {"id": f"{kind}-{stage}-n-{index}", "kind": kind, "stage": stage, "truth": "negative", "outcome": "dismissed", "candidate_latency_ms": 100.0, "review_latency_ms": 100.0}
                    for index in range(100)
                )

        report = evaluate_results(records, environment="field")
        self.assertEqual("PASS", report["metric_gate"]["status"])
        self.assertEqual("FAIL", report["field_gate"]["status"])
        self.assertIn("not proven", " ".join(report["field_gate"]["reasons"]).lower())


if __name__ == "__main__":
    unittest.main()
