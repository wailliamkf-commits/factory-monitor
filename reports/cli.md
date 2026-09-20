# T4 CLI and operator handoff report

Implemented `factory_monitor.cli` and `python -m factory_monitor` with these
operator commands:

- `init --config PATH` creates the validated, uncalibrated configuration and
  refuses to overwrite an existing file.
- `gui --config PATH --data-dir PATH --source demo|video|live [--input PATH]`
  launches the existing desktop UI.
- `preflight --config PATH --data-dir PATH` reports local OS/RAM/disk,
  dependencies, model-file presence, loopback Ollama reachability, and saved
  capture calibration without capture, permission, or OS-setting changes.
- `demo` and `replay` run bounded headless runtime sessions and can atomically
  save a JSON summary with `field_verified:false`.
- `evaluate --input JSON --output JSON --environment synthetic|field` writes
  the core machine-readable metric report. Its field Gate remains FAIL/not
  proven by design.

Added the README, operator guide, acceptance protocol, and empty metadata/
field-evidence templates. They distinguish synthetic software checks from the
separate Windows/macOS actual-client acceptance requirements and do not claim
screen-recording permissions.

## Test evidence

The CLI tests were written first and initially failed at collection because
`factory_monitor.cli` did not exist. Focused verification after implementation:

```text
.venv/bin/python -m pytest tests/test_core.py tests/test_cli.py -q
20 passed in 0.04s
```

A real bounded headless command was also run with a temporary uncalibrated
config:

```text
.venv/bin/python -m factory_monitor demo --config TMP/config.json --data-dir TMP/data --seconds 0.5 --report TMP/demo.json
```

It produced 30 synthetic frames/health messages, no surfaced errors, and a
saved report whose field Gate was FAIL. This validates local process flow only.
Final full-suite verification was `55 passed in 4.27s`.
