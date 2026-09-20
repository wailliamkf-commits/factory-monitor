# Acceptance protocol

## Non-negotiable evidence boundary

Synthetic demo/replay, a configuration file, a CLI report, a local model
response, or an application screen can validate limited software behavior.
None proves real client calibration, camera identity, model accuracy, switching
integrity, or production readiness. The final Gate remains **FAIL** until both
Windows and macOS have their own reviewed field evidence.

## Required collection

For each event class (`material_candidate`, `station_absence`) and stage
(`candidate`, `final`), collect separately held-out, human-labelled data with
at least 50 positive and 100 negative/confuser examples. Preserve a case ID,
camera ID, event time, fixture/source type, evidence path or explicit missing
gap, truth label, outcome, measured candidate/review latency, and reviewer.
Use [the case template](../scripts/acceptance/case-metadata.template.json).

Run:

```bash
.venv/bin/python -m factory_monitor evaluate --input cases.json --output reports/evaluation.json --environment field
```

The resulting `metric_gate` checks sample counts, recall, and measured p95
against 3 s candidate and 15 s review targets. Timeouts, unknowns, misses,
errors, and missing/non-finite latency remain failure samples. They are not
dropped to improve a percentile. The command's `field_gate` remains FAIL by
design: an evaluator cannot establish the operational evidence below.

## Final field evidence checklist

Record the following in a reviewed field evidence bundle using
[the template](../scripts/acceptance/field-evidence.template.json):

- Separate actual-client Windows and macOS runs, with OS/build, capture backend,
  selected client identity, calibrated layout readback, and explicit permissions
  observed locally. Do not write a permission claim when none was observed.
- 100 actual-client detail/grid cycles, including scaling, focus loss, popups,
  and restart faults, with zero ID misbindings. Each cycle needs a verified
  local readback.
- A 72-hour ten-camera run per target OS, including gaps, crash/freeze/timeout,
  disk pressure, reboot, retention, and peak ten simultaneous-event evidence.
- Normal-shift camera exposure hours and final false alarms per camera per
  eight-hour equivalent. Include evidence paths or a precise missing-range
  record for every candidate.
- Candidate recall ≥95%, final recall ≥90%, candidate p95 ≤3 s, initial review
  p95 ≤15 s including queue, and at most one final false alarm/camera/8 h.

Only an independent review of this evidence can change the final Gate. A
missing row, missing evidence file, unknown, or timeout remains a failure or
an explicit gap, never normal activity.
