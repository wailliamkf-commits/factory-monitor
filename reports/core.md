# T1 core implementation report

## Scope

Implemented the frozen T1 boundaries in `src/factory_monitor/`:

- `config.py`: V1 schema validation, ten normalized 4×3-mosaic camera defaults,
  local-only review endpoint enforcement, calibration identity/dimension guards,
  and atomic save/readback.
- `rules.py`: normalized geometry, per-camera track/region deduplication,
  material-entry/exit candidates, schedule-aware station absence, and reset on
  blind, frozen, unavailable, mapping-invalid, layout mismatch, timestamp
  reversal, or source-observation gaps.
- `store.py`: WAL SQLite events, parameterized writes, completed-only pruning
  that returns metadata without deleting files, and interrupted-recording
  recovery to a visible incomplete state.
- `switching.py`: one global priority queue (material before absence), verified
  detail/grid state transitions, fixed dwell/grid intervals measured from the
  request, bounded verification expiry, rolling blindness accounting that
  includes return uncertainty, and fail-closed coverage when verification or
  budget fails.
- `evaluation.py`: machine-readable sample/latency accounting and separate
  software-validation versus actual-field Gate reports.

No field acceptance is claimed. Defaults remain uncalibrated, cloud review is
rejected, and switching remains disabled unless both source and switch
calibration are explicitly true.

## Evaluation input schema

`evaluate_results(records, *, environment="synthetic")` accepts one record per
hand-labelled held-out case or result:

```json
{
  "id": "case-001",
  "kind": "material_candidate",
  "stage": "candidate",
  "truth": "positive",
  "outcome": "supported",
  "candidate_latency_ms": 840.0,
  "review_latency_ms": 2100.0
}
```

`kind` is `material_candidate` or `station_absence`; `stage` is `candidate` or
`final` (defaults to `final` for compatibility); `truth` is `positive` or
`negative`; and `outcome` is `supported`, `dismissed`, `unknown`, `timeout`,
`missed`, or `error`. A field dataset must retain the case identifier and
human ground-truth label outside this report as well as evidence links and the
camera/time exposure used to audit false alarms.

Unknown, timeout, missed, error, and absent/non-numeric latency values are
retained as SLA failure samples. They are never dropped from a denominator.
The latency report separates `*_p95_measured_ms` from censored and failure
counts: it never invents a made-up latency for a timeout. Empty positive sets
return `recall: null`, never a perfect score. The report contains both event
classes and stages even when a dataset supplies zero cases, which makes the
required separate minimums visible: 50 positives and 100 negative/confuser
cases per class/stage. Candidate p95 uses 3000 ms; final review p95 uses 15000
ms.

`metric_gate` reports those deterministic sample/recall/latency checks.
`field_gate.status` is always `FAIL`/not proven in this module, including when
the caller provides `environment="field"`. The final Gate must independently
read target-OS 72-hour ten-camera evidence, 100 verified zero-misbinding
switches, camera exposure and false-alarm evidence, and playable/missing
evidence plus fault-test results. This prevents a caller flag or an isolated
result table from being presented as field acceptance.

## Test evidence

The initial RED run was intentionally before the package existed:

```text
.venv/bin/python -m unittest tests/test_core.py -v
ModuleNotFoundError: No module named 'factory_monitor.config'
```

The subsequent red-to-green slices initially failed for the new
`evaluate_results(..., environment=...)` contract, an incorrectly passable
field label, calibration without a source identity/dimensions, and unconfirmed
detail/grid switch expiry. Each passed after implementation.

Final focused verification:

```text
.venv/bin/python -m pytest tests/test_core.py -q
17 passed in 0.02s

.venv/bin/python -m compileall -q src/factory_monitor
exit 0
```

After the runtime fixture was made explicitly calibrated, final full-suite
verification passed:

```text
.venv/bin/python -m pytest -q
55 passed in 4.27s
```

After that suite, a concurrent-reader regression was found: opening an
`EventStore` from GUI/manual-review code could mark another process's active
recording incomplete. Recovery is now explicit through
`recover_interrupted_recordings()` for the exclusive runtime-startup owner;
ordinary opens are read-safe. Pruning also excludes events whose analysis is
still pending. The test-first regression sequence was:

```text
tests/test_core.py::StoreContractTests::test_concurrent_reader_does_not_mutate_a_live_recording
FAILED: recording became incomplete

.venv/bin/python -m pytest tests/test_core.py -q
19 passed in 0.02s
```

The runtime owner must invoke explicit recovery before workers start; root will
repeat the full suite and long smoke after that batched runtime change.

Final narrow rule regressions then covered two geometry/state boundaries:

- An exit-line candidate now requires the person-foot trajectory to intersect
  the finite configured segment; crossing its infinite extension does not
  emit a candidate.
- Track/region de-duplication retains a departed track for 60 seconds, then
  removes its tokens. This avoids immediate repeated candidates from a brief
  tracking gap while bounding state during long synthetic track-ID churn.

Both tests were RED against the prior implementation and pass after the fix:

```text
.venv/bin/python -m pytest tests/test_core.py::RuleContractTests -q
4 passed in 0.01s
```
