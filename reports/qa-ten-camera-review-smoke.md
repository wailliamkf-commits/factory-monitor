# Ten-camera local-Qwen queue smoke

The first 31-second run began close to a just-finished full test suite, so its
10/10 timeout result is retained as a contaminated observation and is not used
for capacity interpretation. One clean bounded rerun followed after runtime
code freeze and the test suite stopped:

```text
.venv/bin/python scripts/qa_ten_camera_review_smoke.py \
  --root artifacts/qa-ten-camera-review-clean-20260919T175935Z --seconds 31
```

It used the actual loopback Ollama endpoint `127.0.0.1:11435`, local
`qwen3-vl:2b-instruct`, the real runtime review worker, and six ordered
timestamped frames per request. Candidate facts were explicitly synthetic:
ten empty station ROIs caused ten simultaneous absence candidates after the
fixture-only four-second threshold. The configured review deadline stayed at
15 seconds.

Clean-run result from a working tree based on commit
`c570804f6e9912e74b5023efff0b8e5960d78e0d` **plus uncommitted working
changes**. It must not be read as an exact result for that commit alone:

- Ten candidates, 0.0-second trigger spread, no pending reviews at shutdown.
- One local-Qwen `supported` result with a successful-model completion latency
  of 3,672.86 ms; nine watchdog timeouts. All ten outcomes remained in the SLA
  denominator and in SQLite/reporting.
- The nine timeout latency values cluster near 15,006 ms because they measure
  the controller's terminal timeout notification, not successful Qwen model
  completion. Successful-review p95 is 3,672.86 ms from one sample and is not
  a throughput conclusion; successful completion p95 for the other nine is
  unknown/censored. Terminal-notification p95 is 15,006.56 ms.
- Peak controller-plus-child RSS was 530,038,784 bytes. It excludes the
  separately running Ollama server, so it is not total model memory.

This demonstrates a concrete bottleneck: a single local Qwen review worker did
not process a simultaneous ten-event burst within the 15-second requirement
on this host/configuration. It is not field accuracy, Windows/macOS capture,
or hardware-sizing evidence by itself. The field Gate remains FAIL.
