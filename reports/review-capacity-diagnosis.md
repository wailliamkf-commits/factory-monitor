# Local Qwen review capacity diagnosis

## Verdict

Two software serialization points are confirmed: the current runtime submits
reviews from one blocking worker, and Ollama 0.34.2 refused the requested
`OLLAMA_NUM_PARALLEL=4` for this model. The benchmark therefore did **not** test
four-way model parallelism. Ten warm HTTP requests were released concurrently,
but Ollama explicitly reported that `qwen3vl` does not support parallel
requests and launched its runner with `-np 1`. Five requests returned
schema-valid results within the unchanged 15-second deadline and five timed
out.

The Mac M5 / 32 GB machine's capacity for four genuinely parallel Qwen3-VL
reviews remains unproven because this trial never exercised four inference
slots. No hardware purchase is justified by this evidence. The production
Gate remains **FAIL**.

## Controlled trial

Command:

```text
.venv/bin/python scripts/qa_review_parallel_diagnosis.py \
  --endpoint http://127.0.0.1:11436 \
  --parallel 4 \
  --server-pid 59942 \
  --frames artifacts/qa-ten-camera-review-clean-20260919T175935Z/20260919T175935Z/evidence/058bca7c-1cf0-4dce-aa58-b309c26374c3/frames \
  --output reports/review-capacity-diagnosis.json
```

The isolated loopback service used the same local model cache, cloud disabled,
and `OLLAMA_NUM_PARALLEL=4`. It was stopped after the trial; the existing
service on port 11435 was not touched. The model was explicitly prewarmed with
the same six-frame review contract. Prewarm succeeded with schema-valid output
in 15,553 ms. The main trial then released ten requests together with one
shared queue-inclusive deadline of 15,000 ms. Inputs were six ordered frames
from the existing synthetic ten-camera smoke evidence. The production
`OllamaReviewer`, prompt, JSON schema validation, camera count, frame count and
deadline were unchanged.

This was a component benchmark. It did not exercise capture, detection,
evidence recording, the runtime controller, real cameras, private images, or
field accuracy.

## Measurements

| Measurement | Result |
|---|---:|
| Schema-valid completions by 15 s | 5 / 10 |
| Right-censored timeouts at 15 s | 5 / 10 |
| Errors other than timeout | 0 |
| Successful request durations | 2,930; 5,867; 8,758; 11,663; 14,595 ms |
| Batch observation duration | 15,128 ms |
| Peak Ollama process-tree RSS | 6,712,066,048 bytes (6.25 GiB) |
| Ollama-reported model VRAM | 5,468,061,694 bytes (5.09 GiB) |
| Minimum system-available memory | 8,251,162,624 bytes (7.68 GiB) |
| GPU utilization | unavailable; no unprivileged macOS/API counter was available |

The ten identical warm requests are favorable to prompt-cache reuse. Their
roughly 2.9-second completion cadence (rather than simultaneous completion)
matches the observed one-slot runner. This is an optimistic component result,
not evidence that diverse camera requests would also reach 5/10.

The original clean runtime smoke recorded 1/10 completion and 9/10 timeouts.
Its review process dequeues one task, performs the entire HTTP inference, and
only then dequeues the next. The 1/10 and 5/10 results are **not** an A/B
parallelism comparison: this diagnostic was prewarmed, repeated the same six
frames and camera-01 context ten times, benefited from near-total prompt-cache
reuse, and bypassed the runtime. The difference cannot be attributed to
concurrent submission. Ollama's startup log said `model architecture does not
currently support parallel requests` for `qwen3vl`, and the child command used
`-np 1` despite the requested value of four.

## Minimum zero-purchase conclusion

No zero-purchase production change is validated by this trial. Setting
`OLLAMA_NUM_PARALLEL=4` is ineffective for this Ollama/Qwen3-VL combination,
and changing the runtime to four workers would still feed a one-slot model
runner. The code inspection establishes queue serialization, but this
cache-favorable component run does not quantify the benefit of changing it.

The next zero-purchase investigation should first find a local multimodal
backend/version that demonstrably exposes more than one model slot, then run
ten distinct synthetic six-frame sequences through the full runtime with the
same 15-second absolute deadline. Only if that component can execute truly in
parallel should the runtime owner add a bounded concurrent dispatcher while
preserving timeout accounting and schema validation. Separately isolated local
runners are another hypothesis, but their aggregate memory and GPU contention
must be measured before they are recommended. The present 6.25 GiB process RSS
cannot be multiplied safely on a 32 GB unified-memory host without testing.

The acceptance signal remains ten schema-valid outcomes inside 15 seconds with
diverse synthetic frames and the full runtime path. Hardware sizing should wait
for that test; the present run shows one-slot memory use and did not exercise
true parallel GPU load.

Machine-readable evidence is in `reports/review-capacity-diagnosis.json`.
