# T3 GUI verification record

Date: 2026-09-20 (macOS development host)

## Scope delivered

- PySide6 local desktop workbench with ten preview panes, mode banner, source start/stop controls, diagnostics, candidate list and manual review labels.
- Stop-gated capture configuration: explicit capture backend and target, expected native pixel size, deliberate camera-ID/layout confirmation, ten camera IDs/crops, local floorplan import and point placement, related views/heading, actual local preview-image ROI/exit-line drawing, shift/break schedules and absence threshold.
- Operator-selected fixed camera-label templates saved only under `data/calibration_templates`, with a normalized label rectangle, expected label, strict similarity/margin thresholds, and explicit incomplete/mapping-unverified status. Layout-target edits clear the verification data rather than retaining stale identity evidence.
- Runtime-bound error/health/frame rendering. The GUI blocks uncalibrated live starts and shows local errors rather than simulated live detections.
- Operator launch/build scripts for macOS and Windows plus a selectable `Factory Monitor Synthetic Source` test window. The synthetic window only draws labelled moving blocks; it is not footage, a detection fixture, or model evidence.

## Test-first evidence

Initial GUI test execution was blocked by the incomplete project environment (`No module named pytest`), then by the not-yet-present core configuration module. After dependencies and core modules were available, the first offscreen GUI execution identified two defects: a contradictory test assertion around the explicit “non-field” label, and an event review test that had not persisted the runtime-owned event first. The tests were corrected to assert the intended behaviour against a real SQLite event.

An additional interaction test then exposed a real Qt event-type issue: `QRect.contains(QPointF)` raised in floorplan point placement. `FloorplanCanvas` now converts the mouse position to `QPoint` before containment. A final test exposed that source/floorplan controls remained editable while running. `_set_running()` now locks every configuration input until Stop.

Final command:

```text
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest tests/test_gui_offscreen.py -q
```

Result: `17 passed in 0.66s`.

The seventeen offscreen interactions cover explicit synthetic labelling, live-mode calibration guard, dimensions-only no-calibration protection, deliberate calibration/crop atomic save and readback, schedule-only preservation of an already-confirmed layout, ROI/schedule/floorplan placement save and readback, camera-relative preview cropping from a full grid image, actual local preview-image loading for ROI drawing, fixed-label template persistence with mapping left unverified, clearing identity templates after a layout change, full configuration lock during a running session, visible model-error plus SQLite manual false-alarm persistence, analysis-state updates, system-open-only evidence feedback with list/dict gap rendering, candidate/manual-review notifications, bounded health diagnostics, read-only SQLite reconciliation retaining all in-flight plus the newest 20 completed events, and aspect-ratio-preserving camera tiles in the 4×3 grid.

## Integrated synthetic GUI run

Command:

```text
QT_QPA_PLATFORM=offscreen ./.venv/bin/python scripts/render_gui_snapshot.py \
  --active-demo-seconds 15 --output artifacts/gui-active-demo.png
```

Result: passed after 15 seconds with the real multiprocess `RuntimeController` in `demo` mode. The re-inspected 1420×920 screenshot at `artifacts/gui-active-demo.png` shows ten observable synthetic frames in a 4×3, aspect-ratio-preserving layout and compact running worker states. Its `SYNTHETIC / DEMO` banner remains visible. This is local GUI/runtime integration evidence only: it does not use a real screen, camera feed, trained-model claim, or field acceptance result.

Additional static check:

```text
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m compileall -q src/factory_monitor/gui scripts/demo_monitor_window.py
git diff --check
```

Result: passed.

Packaging check:

```text
scripts/build-macos.command
```

Result: passed on this macOS host, producing `dist/factory_monitor-0.1.0.tar.gz` and `dist/factory_monitor-0.1.0-py3-none-any.whl`. The build output included the GUI package and `capture/assets/ScreenCaptureKitHelper.swift`. This is build-package evidence only, not native macOS capture evidence and not a Windows build/test.

## Limits

This record does not establish native macOS capture, model availability, real camera identity, detection accuracy, switching correctness, Windows behaviour, or field acceptance. Those require runtime integration and separate actual-client evidence.
