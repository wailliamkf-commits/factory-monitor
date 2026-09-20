# Ten-camera multiprocess synthetic capacity smoke

Run after the runtime repair batch reported ready, from a working tree based on
commit `c570804f6e9912e74b5023efff0b8e5960d78e0d` **plus uncommitted working
changes**. It must not be read as an exact result for that commit alone:

```text
.venv/bin/python scripts/qa_ten_camera_smoke.py \
  --root artifacts/qa-ten-camera-20260919T175529Z --seconds 94
```

The real runtime used the built-in labelled synthetic demo at 2 FPS, disabled
review, default 30-second pre/60-second post/30-second preview settings, and a
fixture-only 31-second absence threshold. All ten camera station ROIs were
the unused upper tile region; material ROIs and exit lines were empty.

Results from the generated JSON report:

- Ten station-absence candidates, exactly one per CAM01–CAM10, had a 0.0-second
  trigger spread. The configured in-flight limit was 10 and no runtime error or
  queue-rejection event surfaced.
- SQLite contained ten completed events. Each had a preview, an empty gaps
  field, and an OpenCV-decodable MP4 with 181 frames at 2 FPS (90.5 seconds).
  This is a representative default pre/post evidence window for all ten
  simultaneous recordings.
- Peak observed controller-plus-child-process RSS was 662,159,360 bytes.
  This is a host-specific smoke measurement, not a hardware recommendation.
- Event stream counts were 10 candidates, 30 event updates, 1,980 frames, and
  1,980 health messages. No local model request was made.

This is capacity evidence for the local synthetic software loop only. It does
not establish detector accuracy, model-review p95, actual camera identity,
Windows/macOS native capture, switching correctness, or field readiness. The
report's field Gate remains FAIL.
