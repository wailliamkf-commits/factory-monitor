# Multiprocess synthetic runtime smoke

Historical, pre-review run. The later independent review established that the early absence event's missing pre-roll was not being reported. Its `complete`/empty-gap result below is the observed old behavior, **not** proof of full requested evidence coverage. Corrected-tree runs and final status belong in `TEST_REPORT.md` and the later ten-camera report.

Run on 2026-09-20 UTC against repository revision
`c570804f6e9912e74b5023efff0b8e5960d78e0d` using:

```text
.venv/bin/python scripts/qa_runtime_smoke.py \
  --root artifacts/qa-smoke-20260919T174035Z --seconds 94
```

The script wrote an isolated report and SQLite/evidence tree at
`artifacts/qa-smoke-20260919T174035Z/20260919T174035Z/`. Its explicitly
synthetic calibrated fixture used a 960×540 scripted demo, 2 FPS, no local
review, a CAM01 material ROI entered at approximately 30 seconds, and an
accelerated two-second absence threshold. The threshold applies only to that
fixture.

Observed real runtime output:

- Two candidate events: one `station_absence` and one `material_candidate`.
  They overlapped from material trigger at `1789839665.760` until the absence
  recording completed at `1789839697.795`.
- Both SQLite rows read back as `complete`, with `recording_status=complete`,
  preview paths, MP4 evidence paths, and empty reported gaps.
- OpenCV opened and decoded both MP4s. The material clip had 181 frames at
  2 FPS (90.5 seconds), covering the intended roughly 30-second pre-event
  cache and 60-second post-event retention; the absence clip had 125 frames
  at 2 FPS (62.5 seconds).
- A second four-second run was stopped deliberately before the post window.
  A new `RuntimeController` startup and independent SQLite readback retained
  one `incomplete` event rather than erasing it.

The script now also asserts overlapping recordings on future runs. It performs
no real capture, sends no imagery remotely, and sets its field Gate to FAIL.
This is software-loop evidence only, not Windows/macOS, client-identity,
accuracy, switching, or production acceptance evidence.

## Package check

```text
.venv/bin/python -m build --wheel --outdir TMP
.venv/bin/python -m pip install --no-deps --target TMP_TARGET WHEEL
PYTHONPATH=TMP_TARGET .venv/bin/python -c 'import factory_monitor, factory_monitor.cli; ...'
```

The wheel built successfully, imported from an isolated target, and included
`factory_monitor/capture/assets/ScreenCaptureKitHelper.swift`. Wheel contents
also included the CLI and GUI modules. This confirms packaging structure on
the current macOS build host; it does not prove Windows native capture.
