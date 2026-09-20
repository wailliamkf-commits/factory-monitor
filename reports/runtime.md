# T2 runtime, capture, evidence and inference report

Date: 2026-09-20  
Host: macOS arm64, Python 3.12.14  
Scope: `runtime.py`, `capture/`, `evidence.py`, `inference.py`, native capture helper and focused tests.

## Implemented boundary

- Capture, detection, evidence encoding and Ollama review run in separate spawned processes. Capture publishes to independent bounded detection, evidence and preview queues. Preview is the only lossy queue; event, analysis, health and evidence lifecycle messages use a reliable control queue.
- Demo frames and scripted facts exist only in `source="demo"` and are visibly marked synthetic. Video and live modes require the actual YOLO model and surface a missing model as an error.
- Ultralytics YOLO11 person inference supports `.pt`, ONNX, OpenVINO XML and CoreML package routes accepted by Ultralytics. Each camera owns a separate persistent `bytetrack.yaml` tracker/model state. The worker reports `warming` until the first complete inference pass. Untracked boxes receive unique transient negative IDs, preventing them from creating crossing/material continuity; boxes are clamped before the rule boundary.
- Ollama review is HTTP loopback-only, ignores proxy environment variables, supplies no tools, and sends one to six ordered timestamped frames. The controller owns the total queue-inclusive deadline and rejects late results. Output has a bounded generation length and a strict schema. Material support additionally requires `target_visible=true` and a target type of cable/wire/bundle/coil; a generic bag or person cannot be accepted as supported.
- Evidence uses a JPEG-compressed bounded pre-event ring, persists event frame files and manifest while in flight, records requested-window and mapping gaps, and streams decode/overlay/write one frame at a time during finalization. It marks any decode/encoder/disk/interruption failure incomplete and retains a clearly identified partial movie after a mid-stream decode failure. Active evidence is never retention-pruned. Disabled model review becomes terminal `uncertain` with manual review required rather than remaining pending.
- Live grid and detail frames are mapped only after exact, nonblank camera-label template readback. The expected label must be the unique best match with a configured score and margin against every enabled-camera template. Verified detail mode records the full detail image only for the selected camera; other cameras get explicit blind gaps and geometric rules remain suspended.
- Live inference and evidence require an independent visual heartbeat for every enabled camera, with separate grid/detail ROIs that have actually changed recently. Missing or stale proof disables only that camera; a changing client clock or CAM01 indicator cannot keep CAM02 observable. Identical pixels alone are reported as suspected stale, never proven frozen.
- Automatic detail/grid control is not delivered. RuntimeController keeps OS-global click dispatch unconditionally unavailable because foreground title/bounds checks do not prove the hit target under DPI scaling, client-area offsets or overlays. No configuration can enable the generic unsafe path: `request_view`/`return_grid` return this explicit capability reason, issue no click, and the operator must switch the client manually. A target-client native hit-test/DPI adapter is mandatory remaining implementation.
- Runtime startup holds an exclusive data-directory ownership lock, performs interrupted-store recovery once, and then recovers evidence manifests. Pending reviews orphaned by a stop or crash are terminally reconciled to `uncertain`, explicitly require manual review, are never re-enqueued, and become eligible for the configured finalized-event retention. Any recovery, native setup or worker-start exception stops already-started workers and releases ownership. Worker exits/crashes, observation stalls and review deadline expiry invalidate prior green state and reset rule continuity.

## TDD evidence

Representative RED runs observed before implementation/fixes:

- Initial focused collection: four import errors for missing `factory_monitor.capture`, `evidence`, `inference` and `capture.control`.
- Runtime collection: missing `factory_monitor.runtime`.
- Spawn integration: both runtime tests failed because locally scoped multiprocessing queues were garbage-collected before spawned children rebuilt semaphores (`FileNotFoundError`). Strong controller ownership of all queues fixed the root cause.
- Evidence regressions: missing pre-window was reported complete with no gap; corrupt non-first JPEG was silently omitted; mapping-gap API was absent. All three failed before the repairs.
- Qwen contract: ordered multi-frame input failed OpenCV encoding, invalid decisions were accepted, and a generic-bag response was accepted as `supported` before the kind-specific schema gate.
- Identity regression: strict detail template readback and the one-digit CAM01/CAM02 wrong-label case failed before template matching replaced pHash as proof.

Final verification on the settled tree:

```text
YOLO_CONFIG_DIR=$PWD/runtime-data/ultralytics .venv/bin/python -m pytest -q
77 passed in 12.47s

.venv/bin/ruff check <T2 source and tests>
All checks passed!

git diff --check
exit 0, no output
```

The focused T2 suite was `33 passed in 11.15s` before the final ownership-lock test was added. The final full suite includes actual YOLO replay, multiprocessing demo-to-evidence flow, native helper compilation, deadline/schema, gap/recovery, identity ambiguity and failure-visibility tests.

After that full-suite run, the orphan-review recovery and streaming-finalization fixes were verified without starting another model benchmark: `11 passed in 1.35s` across `tests/test_evidence.py` and four focused runtime lifecycle/recovery tests. The prior `77 passed` record is not presented as a post-fix full-suite result.

## Actual local runtime checks

### macOS ScreenCaptureKit

The bundled Swift 6.3 helper compiled and captured the exact dedicated window titled `Factory Monitor Synthetic Source`. It did not use a screenshot loop or full-display fallback.

```text
result: PASS for this synthetic exact-window smoke
captured shape: 1000 x 856 x 3 BGR
artifact: artifacts/native-screencapturekit-frame.jpg
sha256: 62f6d7eb7eb76ae723d68d6261720500dddd3f75aee333c96f998b841eeaf2c6
```

The first native attempt failed visibly with a CoreGraphics initialization assertion. Initializing `NSApplication.shared` and linking AppKit fixed that root cause; the following exact-window capture succeeded. This is Mac synthetic-window evidence only, not a real monitoring-client or permission-matrix acceptance.

### YOLO11n + per-camera ByteTrack

The checked local model `models/yolo11n.pt` (SHA-256 `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`) ran on the public bus sample with MPS. Direct detector observations:

```text
CAM01 cold: 3449.7 ms, 4 people, track IDs 1..4
CAM02 first independent tracker: 96.0 ms, 4 people, track IDs 1..4
CAM01 warm persistent tracker: 9.4 ms, 4 people, track IDs 1..4
```

The multiprocessing video-replay test built an eight-frame AVI from that public image, emitted a `warming` state, ran actual YOLO/ByteTrack, and returned at least three people in a non-synthetic frame: `1 passed in 5.07s`. These timings are a single-image/model smoke, not ten-camera p95 evidence.

### Local Qwen review

The exact client reached project-local Ollama `127.0.0.1:11435` with `qwen3-vl:2b-instruct`; a two-frame JSON response completed in 9.395s. A later material-specific prompt completed in 6.747s but incorrectly called a generic black bag `supported` while also saying it was not cable/wire/coil. That failure drove the stricter material response fields and semantic gate. The settled unit regression proves that same response is rejected to model-error/manual-review state. Therefore the local transport/model route is demonstrated, while model accuracy is explicitly unproven and the observed semantic failure counts against readiness.

## Remaining blockers

- Windows WGC is implemented through the `windows-capture` provider for an exact window, with explicit selected-display fallback only when backend is `display`. It has not been installed or executed on Windows; Windows acceptance is FAIL.
- Mac real-client capture, camera-label templates, heartbeat ROI, native focus/move/scale/popup fault tests, 100 switching cycles and a 72-hour run are absent; Mac field acceptance is FAIL.
- No real camera imagery or held-out per-class dataset was available. Candidate/final recall, false alarms, source-to-client latency, ten-camera p95 latency and 20-event retention under field load remain unmeasured.
- Screen capture can establish desktop-frame arrival, but each camera's playback freshness is unavailable without its own calibrated changing clock/heartbeat region. The runtime now fails closed per camera in that condition.
- OpenVINO, ONNX and CoreML are routed but were not executed in this Mac test. The verified local route was PyTorch MPS.

Software tests validate the implemented failure boundaries and local synthetic/public-fixture paths. They do not change the frozen production Gate from FAIL until the required target-OS and field evidence exists.
