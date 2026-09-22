# Factory Monitor V1

Local desktop software for reviewing candidates from an existing monitoring
window. It starts with ten placeholder camera regions, not ten verified camera
identities. Cloud image analysis is disabled and every result requires a
human review label. This is an engineering preview: automatic client switching
is not delivered and cannot be enabled by configuration. It needs a
client-specific input adapter with verified hit-testing and coordinate scaling.
The measured ten-event local-model burst also misses the 15-second requirement.
Overall acceptance remains FAIL. Start with [the Chinese handoff](START_HERE_zh.md).

For an ongoing Windows test, versioned downloads, evidence handoff, updates and rollback,
read the [stable operation procedure](docs/STABLE_OPERATION_zh.md) first. Target-device
synthetic evidence gaps and shutdown failure remain unresolved; a documentation update
is not a runtime fix.

For a clean Windows account, use the bounded [Windows handoff](docs/WINDOWS_HANDOFF_zh.md). It creates a Python 3.12 project-local environment but does not provide an installer, an `.exe`, model weights, live capture, or field acceptance.

Prepare the project environment using the platform handoff first. The CLI never
creates an environment or installs dependencies.

```bash
.venv/bin/python -m factory_monitor init --config config.json
.venv/bin/python -m factory_monitor preflight --config config.json --data-dir data
.venv/bin/python -m factory_monitor gui --config config.json --data-dir data --source demo
```

The generated configuration is deliberately uncalibrated. Before any live
start, set a positive observed window size, a specific operator-window title,
the backend/layout version, and all ten camera mappings in the UI; save and
reopen the configuration to confirm the exact values. See
[the operator guide](docs/OPERATOR_GUIDE.md) and
[the acceptance protocol](docs/ACCEPTANCE.md).

For headless software checks only:

```bash
.venv/bin/python -m factory_monitor demo --config config.json --data-dir data --seconds 30 --report reports/demo.json
.venv/bin/python -m factory_monitor replay --config calibrated-video.json --data-dir data --input fixture.mp4 --seconds 30 --report reports/replay.json
.venv/bin/python -m factory_monitor evaluate --input scripts/acceptance/case-metadata.template.json --output reports/evaluation.json --environment synthetic
```

`demo` is synthetic and cannot establish model accuracy, throughput, switching,
or field readiness. `preflight`, `evaluate`, and runtime summaries always keep
the field Gate at `FAIL`/not proven until the independently collected field
evidence is reviewed.
