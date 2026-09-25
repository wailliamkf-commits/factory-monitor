# Active Observation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for assigned ownership. User authorized autonomous architecture and execution. One concentrated final review.

**Goal:** Deliver a runnable, isolated architecture experiment for person-independent scene changes and calibrated active inspection.
**Architecture:** A scene observer emits bounded change hints, a deterministic planner emits inspection suggestions, and a profile evaluator rejects unsupported learning claims. This experiment cannot drive the production desktop.
**Tech Stack:** Existing Python3.12, NumPy/OpenCV, standard-library JSON/dataclasses, pytest; no added model downloads or runtime dependency.
**Spec:** ../specs/2026-09-25-active-observation-design.md

## Global Constraints
- Production media remains local; test inputs explicitly synthetic.
- Existing packaged runtime and 180s/hour blindness budget remain unchanged.
- No unlocking current NativeWindowClicker or changing on-site recording/shutdown fixes.
- Fullscreen scaling is not evidence of increased source detail; no performance claims from simulation.

## Review Focus
- A static object changes without a person: scene test must emit a generic candidate.
- Global illumination / timestamp overlay: targeted exclusion and change rejection tests.
- Repeated high-priority requests: quiet camera deadline and event expiry tests.
- Wrong, stale or replayed result: planner must remain unverified/faulted and never advance.
- Fabricated profile success or synthetic-only records: evaluator must refuse field eligibility.

## Task A — Scene observer (Sol owns scene_watch.py and its tests)
- [x] Implement `SceneChangeWatch(roi=(0,0,1,1), exclude_rois=(), warmup_seconds=1, hold_seconds=1, pixel_delta=25, min_changed_ratio=.02, max_changed_ratio=.6, max_gap_seconds=2)`.
- [x] `observe(image, timestamp, view_epoch=0) -> dict` returns `state`, `candidate`, `changed_ratio`, `reason`; `reset()` clears state. Timestamp is finite monotonic seconds. Inputs fail validation for NaN/invalid ROI/non-image. No camera ID needed: instantiate once per camera.
- [x] Tests first: baseline cannot alarm; person-free object removal alarms once; persistent change does not get learned away; global brightness/overlay exclusion; view epoch/long gap resets; malformed inputs rejected.
- [x] Run focused tests and one CPU fixture measurement; label its scope.

## Task B — Learning receipt validator (bounded worker owns inspection_profile.py and tests)
- [x] `evaluate_profile(profile: dict, records: list[dict]) -> dict` validates an explicit schema. Profile fields: `schema_version=1`, `profile_id`, `fingerprint` (64 hex), `camera_ids` (unique nonempty strings), `client` {name,version}, `source_size` [w,h], `dpi_scale`, `min_cycles_per_camera`, `max_readback_seconds`.
- [x] Record fields: unique `cycle_id`, `profile_fingerprint`, `source` synthetic/onsite, `camera_id`, `requested_at`, `detail_frame_at`, `grid_requested_at`, `grid_frame_at`, `detail_camera_id`, `detail_verified`, `grid_verified`, `target_guard_verified`. Require finite ordered timestamps, fresh frames strictly after each action, readback deadlines, expected identity, strict bool fields; count malformed and duplicate records as failures, not silent exclusions.
- [x] Return local audit only: `replay_gate` PASS/FAIL, counts/reasons, `field_evidence_present`, `automatic_control_authorized=False`. Even structurally valid onsite records do not prove the native adapter or activate desktop control.
- [x] Tests cover wrong ID, stale/duplicate records, mismatched config fingerprint, one undercovered camera and synthetic scope.

## Task C — Parent planner, feasibility and executable experiment
- [x] New `active_inspection.py`: age/deadline fair single-detail scheduler with bounded pending items, expiry, profile fingerprint/freshness checks, explicit fault and reset; no OS integration.
- [x] New `view_feasibility.py`: validate inputs and compute effective pixel budget, round trip/revisit/rolling blind upper budget for serial vs persistent overview; unknown source detail remains unknown, not equal to fullscreen size.
- [x] New `scripts/experiment_active_observation.py`: generate own synthetic images, execute actual scene observer and planner faults, write replay JSON/Chinese findings; never captures user's screen.
- [x] Test public interface including a 16-camera run with all IDs eventually inspected and hot-camera flood bounded.
- [x] Combine tests; independent whole-change review; fix demonstrated failures and preserve raw reports. PR update/readback is recorded by the final delivery.

## Task D — Research synthesis and handoff
- [x] Primary-source audit of domestic GUI models and client controls, with verified/inferred/onsite columns.
- [x] Chinese design report: source-quality limits, no-person handling, teaching procedure, blind-time feasibility, native transition contract, commercial support matrix.
- [x] Prepare the allowlisted source/experiment packager and Windows launcher. The final ZIP is generated from the clean committed tree outside the repository, with its own manifest/readback receipt; it is a lab, not a runtime upgrade.
