# Bounded repair verification — 2026-09-20

Software Gate: **FAIL** at this verification snapshot. Field Gate: **FAIL** independently. This is verification of the initial review findings and the requested store/GUI repairs, not a new broad review. Owners were applying final corrections concurrently; any later changes require only targeted confirmation of the remaining items below. No production code changed by this reviewer.

## Verified repairs

- Initial finding1: capture previews now use a separate lossy queue; lifecycle/analysis/evidence messages are not evicted by preview publication.
- Initial finding2: both live consumers use verified grid/detail mapping. Unverified views are gaps and verified detail is assigned only to its selected camera. The latest controller also requires a distinct detail-layout discriminator. Camera freshness proof remains a blocker below.
- Initial finding3: missing pre-event coverage is explicit; a corrupt nonfirst JPEG now finalizes incomplete instead of silently disappearing. Encoded playback shows frame timestamps and gap annotation. Relevant regression tests pass.
- Initial finding4: controller watchdog produces terminal timeout independently of reviewer progress; late results for expired entries are ignored. Safe probe: expired pending event -> watchdog -> late supported result remains timeout. This verifies failure visibility; it does not establish real ten-camera model latency or successful recovery of a stalled model service.
- Initial finding5: manual detail requests enqueue through SwitchPolicy instead of directly issuing clicks. Grid return remains governed by the serialized policy/dwell. Field switching remains unverified.
- Initial finding7: readback uses capture monotonic timestamps against the action boundary. Safe probe delivered a newly notified but old capture; readback correctly rejected it.
- Store readers no longer run crash recovery. Recovery runs under exclusive runtime directory ownership; concurrent-reader/core tests pass. Lock release integration remains a blocker below.
- GUI now crops the ROI drawing image to the selected camera, exposes local evidence opening/preview and gaps, sends cautious candidate/manual-review notifications with beep, caps diagnostics and bounds finalized visible events. Targeted GUI tests pass.

## Remaining MUST-FIX

### P1 — Lifecycle lock prevents normal GUI Stop/Start

`src/factory_monitor/runtime.py:385-400,486-505`; `src/factory_monitor/gui/app.py:852-869` at inspection. RuntimeController retains its file lock after stop and offers no explicit dispose; GUI retains that stopped controller and evaluates construction of its replacement before overwriting the old object. The next Start fails with `runtime data directory is already owned`.

Safe probe: construct controller, call stop, construct replacement for same directory while retaining the old object -> reproduced ownership failure. This is directly the GUI's object lifetime pattern. Add an explicit close/dispose lifecycle (after workers and pump stop), integrate it with GUI replacement/exit/failure, and test actual-controller Start/Stop/Start or equivalent stopped-object replacement. Do not remove single-owner protection while a controller is active.

### P1 — Final retention is not triggered after pending analysis finishes

`src/factory_monitor/runtime.py:660-668,832-840` at inspection. Store correctly protects completed evidence whose analysis remains pending, but analysis completion and watchdog timeout never invoke `_prune_completed`. Consequently, a final batch that finishes evidence before analysis leaves more than20 fully finalized events indefinitely, until another evidence completion happens.

Safe probe: create21 evidence-complete/pending events, run prune (correctly keeps21), deliver21 analysis completions -> still21 finalized database rows. Trigger final retention after every transition making an event fully final, including normal analysis, timeout and applicable error paths; test no subsequent event is needed.

### P1 — One full-screen heartbeat incorrectly establishes freshness for all cameras

`src/factory_monitor/runtime.py:207-218,315-323` at inspection. Both live consumers call one `verification.source_heartbeat` against the full frame and use its boolean for all cameras. A ticking client clock or CAM01 clock continues while CAM02 freezes; CAM02 is still treated observable and its absence timer advances. Camera identity alone cannot prove live feed freshness.

Require camera-specific reliable source-clock/heartbeat proof associated with each camera's grid crop and correctly calibrated detail view. Missing/stale proof must make only the affected camera unavailable and reset its continuity; a client clock must not prove ten independent feeds fresh. Add two-camera test where one feed/clock freezes while the other clock advances. If reliable per-camera proof cannot be calibrated, live rules must remain fail-closed for that camera.

### P1 — Native automation still lacks proof that a global click reaches the intended unobscured target

`src/factory_monitor/capture/control.py:224-233,256-304` at inspection, plus native controller construction. Repair adds current foreground title/origin/size checks and pre-click identity readback, but those do not establish the hit target or coordinate scale. An overlay can cover a click point without covering camera identity labels or changing foreground title/bounds; window capture still sees the intended client while global input can hit the overlay. A Retina/DPI capture-pixel-to-desktop-coordinate mismatch likewise passes title/bounds comparison unless actual scale is verified.

Keep global-click automation disabled when target hit-testing/occlusion/scale proof is unavailable, or implement target-specific validated dispatch. Default disabled configuration is appropriate but is not a safety gate once incomplete calibration enables NativeWindowClicker. Tests must prove refusal before issuing input for popup/occlusion/scale faults. No real clicks were made in this verification. This feature is field-blocked; do not claim actual-client switching is verified.

## Executed evidence

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_core.py tests/test_evidence.py tests/test_view_control.py tests/test_gui_offscreen.py tests/test_runtime.py -k 'not video_replay_uses_actual_yolo and not demo_runtime and not synthetic_demo and not video_runtime_surfaces'`

Result: **55 passed,4 deselected in0.81s**. Exclusions avoid real model/MPS work while another owner benchmarks local Qwen. Two additional temporary-directory probes confirmed lifecycle-lock/retention failures and watchdog/freshness repairs. No network calls, real capture, private frames or model inference were used.

A final full-suite/build remains root-owned. Passing software tests never substitutes for target Windows evidence, real client camera calibration, held-out accuracy,100 switch cycles/fault injections, ten-camera queue-inclusive latency and72-hour runs on each target OS. Field Gate remains FAIL.

## Root final resolution — 2026-09-20

Fresh final regressions cleared the previously reported lifecycle-lock and
pending-analysis retention failures: runtime ownership now releases correctly
across stop/start, orphaned pending reviews terminalize, and final retention
re-evaluates after terminal analysis transitions. Per-camera heartbeat handling
is likewise covered by fresh regressions so a stale feed pauses that camera's
rule continuity rather than inheriting a full-screen/client heartbeat.

Native automatic click remains a required missing feature, not a resolved
capability. The disabled-by-default and runtime-unconditionally-disabled path
mitigates the unproven hit-target/occlusion/DPI risk; it does not establish
actual client switching. The overall Gate is therefore **FAIL** despite final
software checks. See [final pytest evidence](final-pytest.log),
[final validation](final-validation.json), and the
[ten-camera synthetic evidence report](qa-ten-camera-smoke.md).
