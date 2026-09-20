# Operator guide

## Before starting

Run `preflight` with the exact configuration and intended data directory. It
reads operating system, RAM, usable disk, Python dependencies, model-file
presence, and the configured local Ollama endpoint. It does not select a
window, capture a screen, request a permission, change an OS setting, or start
Ollama.

```bash
.venv/bin/python -m factory_monitor preflight --config config.json --data-dir data
```

Read its JSON rather than treating an exit code as readiness. At least 50 GB
free disk is the pilot storage target. A missing model or an unreachable local
Ollama endpoint is an explicit local readiness problem. The default endpoint
is `http://127.0.0.1:11434`; use a saved loopback-only configuration if the
local smoke server deliberately uses another port such as 11435. Never enable
cloud review in V1.

## Calibrate a local client window

1. Open the authorized local monitoring client and arrange its normal grid.
   Do not use a production camera image in an external service.
2. In the application, choose the intended local capture backend and select
   the exact monitoring window. Record the observed pixel dimensions and a
   specific title that distinguishes it from other windows.
3. Draw normalized crop regions only after the grid is stable. Set floorplan
   positions, related views, material ROI, station ROI, exit line, active
   schedule, and breaks for each camera. Empty ROI/line fields disable that
   rule; they are not default detections.
4. Save, reopen, and compare the source title, expected size, layout version,
   camera IDs, crops, and rules. A resized client, changed source identity, or
   changed layout version invalidates the mapping and must stop continuity.
5. Run a local demo or a synthetic replay to exercise software flow. Treat its
   status as synthetic. It is not permission evidence, live-capture evidence,
   or field accuracy evidence.

The configuration validator will not accept `source.calibrated=true` for a
window backend without positive dimensions and a nonblank specific title. It
does not grant screen-recording permission or assert that the selected title
matches an actual window; the operator must verify those facts locally.

## Live operation

Launch with:

```bash
.venv/bin/python -m factory_monitor gui --config config.json --data-dir data --source live
```

The application may surface `observable`, `blind`, `frozen`,
`mapping_invalid`, or `unavailable`. Stop and recalibrate on mapping-invalid or
unavailable. A failed or unverified switch is unknown coverage internally and
is passed to rules as mapping-invalid; it cannot continue an absence timer.

Automatic detail/grid requests are currently unavailable under every
configuration. A target-client input adapter with native hit-testing and
coordinate-scale proof remains required implementation; calibration alone
cannot enable the generic input path. See `LIVE_CALIBRATION.md` for per-camera
identity and heartbeat requirements. Operators must switch the existing client
manually; unverified mappings suspend analysis. Operators should not infer cross-camera identity and must not use
this product to accuse a person of theft, laziness, or any wrongdoing.

Candidates are observations for review. Confirm or mark false alarm only after
viewing available evidence. If recording is missing, incomplete, timed out, or
crashed, retain that gap as a visible condition; do not infer normal activity.

## Headless controlled runs

`demo` is visibly synthetic. `replay` reads a supplied video through the real
local detector path; it does not replace a missing model with scripted facts.

```bash
.venv/bin/python -m factory_monitor demo --config config.json --data-dir data --seconds 30 --report reports/demo.json
.venv/bin/python -m factory_monitor replay --config calibrated-video.json --data-dir data --input sample.mp4 --seconds 30 --report reports/replay.json
```

The optional report records event counts, surfaced errors, worker state, and
`field_verified:false`. It is a bounded software run summary, not an
acceptance certificate.
