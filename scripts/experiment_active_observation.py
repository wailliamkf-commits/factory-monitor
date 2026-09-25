#!/usr/bin/env python3
"""Local synthetic replay. Does not capture screens, call models, or click UI."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from factory_monitor.active_inspection import InspectionPlanner
from factory_monitor.inspection_profile import evaluate_profile
from factory_monitor.scene_watch import SceneChangeWatch
from factory_monitor.view_feasibility import evaluate_view_budget


def run_experiment():
    ids = [f"camera-{i:02}" for i in range(1, 17)]
    fingerprint = hashlib.sha256(b"SYNTHETIC ACTIVE OBSERVATION PROFILE v1").hexdigest()
    profile = dict(schema_version=1, profile_id="synthetic-16-grid-v1", fingerprint=fingerprint,
                   camera_ids=ids, client={"name": "SYNTHETIC fixture", "version": "1"},
                   source_size=[1920, 1080], dpi_scale=1, min_cycles_per_camera=1,
                   max_readback_seconds=2)
    observer = SceneChangeWatch()
    # A rectangle represents material. There are no person pixels/detections.
    before = np.full((240, 320, 3), 70, dtype=np.uint8)
    before[80:150, 130:200] = 180
    after = np.full_like(before, 70)
    scene = []
    for timestamp, frame in [(0, before), (1, before), (2, after), (3, after), (4, after)]:
        scene.append({"timestamp": timestamp, **observer.observe(frame, timestamp)})

    planner = InspectionPlanner(ids, fingerprint, inspection_interval=60,
                                detail_seconds=1, transition_timeout=2, grid_hold_seconds=0)
    planner.arm_overview(now=0, frame_at=0, fingerprint=fingerprint, camera_ids=ids)
    records, trace = [], []
    for i in range(16):
        now = i * 1.3
        planner.submit(ids[0], "scene_change", now=now, ttl=10)
        detail = planner.tick(now)
        if detail is None:
            raise RuntimeError("synthetic sweep unexpectedly blocked")
        detail_ok = planner.confirm(detail["request_id"], now=now + .1, frame_at=now + .1,
                                    view="detail", camera_id=detail["camera_id"],
                                    fingerprint=fingerprint, target_verified=True)
        grid = planner.tick(now + 1.1)
        if grid is None:
            raise RuntimeError("synthetic return unexpectedly blocked")
        grid_ok = planner.confirm(grid["request_id"], now=now + 1.2, frame_at=now + 1.2,
                                  view="grid", camera_id=None, fingerprint=fingerprint,
                                  target_verified=True)
        records.append(dict(cycle_id=f"cycle-{i}", profile_fingerprint=fingerprint,
                            source="synthetic", camera_id=detail["camera_id"], requested_at=now,
                            detail_frame_at=now + .1, grid_requested_at=now + 1.1,
                            grid_frame_at=now + 1.2, detail_camera_id=detail["camera_id"],
                            detail_verified=detail_ok, grid_verified=grid_ok,
                            target_guard_verified=True))
        trace.append({"detail": detail, "return": grid, "snapshot": planner.snapshot(now + 1.2)})
    valid_profile = evaluate_profile(profile, records)
    invalid = deepcopy(records)
    invalid[0]["detail_camera_id"] = "WRONG CAMERA"
    invalid_profile = evaluate_profile(profile, invalid)

    faults = {}
    for name in ("stale_frame", "wrong_camera", "timeout", "return_failure"):
        p = InspectionPlanner(ids, fingerprint, detail_seconds=1, grid_hold_seconds=0)
        p.arm_overview(now=0, frame_at=0, fingerprint=fingerprint, camera_ids=ids)
        action = p.tick(0)
        if name == "timeout":
            p.tick(3)
        elif name == "return_failure":
            p.confirm(action["request_id"], now=.1, frame_at=.1, view="detail", camera_id=ids[0],
                      fingerprint=fingerprint, target_verified=True)
            back = p.tick(1.1)
            p.confirm(back["request_id"], now=1.2, frame_at=1.2, view="detail", camera_id=ids[0],
                      fingerprint=fingerprint, target_verified=True)
        else:
            p.confirm(action["request_id"], now=1, frame_at=0 if name == "stale_frame" else 1,
                      view="detail", camera_id="wrong" if name == "wrong_camera" else ids[0],
                      fingerprint=fingerprint, target_verified=True)
        faults[name] = p.snapshot(5)

    # Bounded component measurement, excludes all capture/model/UI workloads.
    watchers = [SceneChangeWatch() for _ in ids]
    for watcher in watchers:
        watcher.observe(before, 0)
        watcher.observe(before, 1)
    rounds = []
    for i in range(30):
        start = time.perf_counter()
        for watcher in watchers:
            watcher.observe(after, 2 + i * .1)
        rounds.append((time.perf_counter() - start) * 1000)

    budgets = {"serial_9": evaluate_view_budget(9, (1920, 1080), (3, 3)),
               "serial_16": evaluate_view_budget(16, (1920, 1080), (4, 4)),
               "source_limited_16": evaluate_view_budget(
                   16, (1920, 1080), (4, 4), preview_source_size=(640, 360),
                   detail_source_size=(640, 360), object_width_fraction=.00625),
               "persistent_overview_assumption": evaluate_view_budget(
                   16, (1920, 1080), (4, 4), persistent_overview=True)}
    checks = {
        "person_free_change_once": sum(row["candidate"] for row in scene) == 1,
        "quiet_cameras_not_starved": [r["camera_id"] for r in records] == ids,
        "valid_synthetic_receipts": valid_profile["replay_gate"] == "PASS",
        "wrong_identity_receipts_rejected": invalid_profile["replay_gate"] == "FAIL",
        "faults_stop_planner": all(s["state"] == "fault" for s in faults.values()),
        "synthetic_never_authorizes_os_control": valid_profile["automatic_control_authorized"] is False,
        "single_screen_budget_failure_visible": budgets["serial_16"]["blind_budget_gate"] == "FAIL",
    }
    return {
        "schema_version": 1, "source": "synthetic", "field_gate": "NOT_TESTED",
        "experiment_gate": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture_sha256": {"before": hashlib.sha256(before.tobytes()).hexdigest(),
                           "after": hashlib.sha256(after.tobytes()).hexdigest()},
        "scene_trace": scene, "profile": profile, "records": records,
        "planner_trace": trace, "faults": faults, "valid_profile": valid_profile,
        "invalid_profile": invalid_profile, "budgets": budgets,
        "component_measurement": {"scope": "16 CPU scene observers; synthetic 320x240; no capture/model/UI",
                                  "round_ms": rounds, "p50_ms": float(np.percentile(rounds, 50)),
                                  "p95_ms": float(np.percentile(rounds, 95))},
    }


def render_report(report):
    measurement = report["component_measurement"]
    serial = report['budgets']['serial_16']
    checks = '\n'.join(f"- {name}：{'PASS' if passed else 'FAIL'}" for name, passed in report['checks'].items())
    return (
        f"# 主动观察合成实验\n\n实验：{report['experiment_gate']}；现场：NOT_TESTED。"
        "没有采集屏幕、执行点击或调用视觉模型。\n\n"
        f"各项实际检查：\n\n{checks}\n\n"
        "原始轨迹、时间、失败和假设保存在 experiment.json；合成回执不能授予自动控制。\n\n"
        f"按当前假设，16 路名义回访 {serial['nominal_revisit_seconds']:.0f} 秒，"
        f"每小时约 {serial['overview_unavailable_seconds_per_hour']:.0f} 秒失去总览；"
        f"180 秒预算下长期平均一轮至少 {serial['budget_limited_mean_sweep_seconds'] / 60:.0f} 分钟。"
        "这是规划计算，不是实际切换测量。\n\n"
        f"当前机器 16 个 320×240 合成场景观察器顺序计算，每轮 p50={measurement['p50_ms']:.3f} ms，"
        f"p95={measurement['p95_ms']:.3f} ms；只测 CPU 组件，不代表十路端到端或 4060 性能。\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/local/active-observation-20260925"))
    args = parser.parse_args()
    report = run_experiment()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "experiment.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    (args.output / "结果说明.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"experiment_gate": report["experiment_gate"], "field_gate": "NOT_TESTED",
                      "checks": report["checks"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["experiment_gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
