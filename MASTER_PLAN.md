# Ten-camera factory monitoring V1 — accepted 2026-09-20

## Frozen deliverable
Windows first, then Mac local desktop software reading an existing monitoring window. Ten independent camera regions, floorplan with related viewpoints, person/ROI material candidates and five-minute staffed-station absence excluding breaks. Human review only; never label a person a thief or lazy. Cloud imagery off, no hardware purchases, twenty completed events. No RTSP/NVR prerequisite. Existing cameras, NVR, client, synced sources and FDE repository remain untouched.

## Architecture
Python 3.12 + PySide6 + SQLite. Native OS capture and recording, detection, and visual review run in separate processes. YOLO11n person detection + per-camera ByteTrack. Deterministic temporal/ROI rules produce candidates. Local Ollama qwen3-vl:2b-instruct reviews timestamped frames; model unavailability/timeout is manual-review-required, never normal. VLX10B is backlog. Persistent camera state distinguishes observable, blind, frozen, mapping_invalid, unavailable.

Window crop uses a versioned calibrated layout. Window size/source identity changes invalidate mapping. Missing observations reset continuous absence. Frozen-source detection must distinguish a motionless scene from proven stale playback; no claim of camera freshness without a reliable source clock.

Automatic view control is disabled until local client calibration and readback are verified. Single global switch queue: material before absence. Verify ID after switching and all mappings on return. Maximum detail dwell 15s, minimum grid interval 30s, automatic per-camera blindness <=180s/rolling hour. Unverifiable state disables automation and requests manual intervention. Adjacent views are manually selected, no cross-camera identity inference or PTZ control. Switching is supplementary and does not block first cached-frame review.

Capture caches pre-event 30s and records post-event 60s; preview after 30s. Every candidate is persisted before analysis. Protect in-flight evidence; cap in-flight count/disk and visibly report overload. Retain newest 20 completed events. Mark missing time ranges explicitly. Crash, timeout or disk pressure must never silently erase an event. Record event timestamps, inference latency, processing state and manual review separately.

## Acceptance
1 -> 3 -> 10 camera tests. Candidate p95 <=3s, initial model review p95 <=15s including queue. Start time from human-labelled first observable frame (absence starts at rule threshold); source-to-client latency reported separately if unknown. Timeouts/unknown count as failures.
Per event class: separate calibration and held-out sets, >=50 positive and >=100 negative/confuser examples. Candidate recall >=95%, final recall >=90%, <=1 final false alarm/camera/8h. Report candidate workload separately.
100 actual-client switching cycles with zero ID misbinding, plus scaling/focus/popup/restart faults. 72h ten-camera run on actual target OS; crash/freeze/timeout/disk/reboot/retention scenarios, observability and gap metrics. Evidence playable or explicitly missing. Peak simultaneous ten events. Windows and Mac have separate acceptance. Final Gate PASS or FAIL with mandatory remaining issues. Local/synthetic tests do not establish field accuracy, performance or production readiness.

## Existing evidence / missing inputs
Current development host is Mac M5 32GB. Windows from older conversation: i5-14400/16GB/UHD730/512GB, unverified on site. Monitoring client name unknown, user confirms click-to-enlarge is available; no camera sample, floorplan or on-site machine currently provided. These block field acceptance, not independent implementation. At least50GB available SSD for pilot.
FDE main 87226f4f has generic preflight/delivery/evaluation skills, no monitoring implementation. Architecture reference 036d5128 is a separate unmerged branch. Harness localhost3088/3089 was unavailable; auxiliary only, no real-time dependency.

## Execution
Core/state (Terra), runtime/capture/models (Sol), GUI/configuration (Terra). Tests first for meaningful state/failure behavior; integrated tests and one concentrated final review. Second similar failure -> root cause and upgrade. No broad refactor or optional feature expansion. Record commands and results in reports and minimal state files.
