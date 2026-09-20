---
name: factory-monitor-windows
description: Prepare or review the Factory Monitor Windows engineering-preview handoff without claiming field acceptance or enabling disabled native controls.
---

# Factory Monitor Windows Handoff

Use this skill for a clean Windows-account setup, an operator-guided 1→3→10 camera trial, or review of the resulting local evidence. Read `STATUS.md`, `TEST_REPORT.md`, `START_HERE_zh.md`, and [the Windows handoff](../../docs/WINDOWS_HANDOFF_zh.md) before concluding readiness.

Keep the work bounded to the engineering preview. Use Python 3.12 and the repository-local `.venv`; do not alter global Python, PATH, registry, execution policy, or existing configuration. The setup script may install Python dependencies, but it must not create/replace configuration, download model weights, start a local model, or start live capture.

For a new machine, begin with the Windows handoff. Treat copied `.venv`, Mac binaries, camera footage, calibration data, and credentials as out of scope. Models and the Windows Ollama runtime, if needed, are manually prepared and must remain local.

Only an on-site operator may select the real monitoring client and explicitly start capture. If the active session has user-authorized local computer-use access, it may assist the local setup and inspect its state; it must still wait for the operator's explicit real-window/capture action. Keep cloud analysis off and local review loopback-only. Local review may be skipped for a synthetic demo, but not for a real candidate's final review. Automatic native clicking, enlarge, return-to-grid, and cross-view control are unconditionally disabled in this version. Do not propose configuration as a workaround; the operator changes the client view manually.

Expand a real-client check from one camera to three and then ten only after each enabled camera has a distinct identity and reliable per-camera heartbeat. Retain mapping failures, timeouts, gaps, and review failures. Do not turn setup, a synthetic demo, a dependency import, a saved configuration, a model response, or a visible GUI into a claim of field success.

Keep the final Gate at FAIL unless the evidence satisfies the separate Windows and macOS requirements in `docs/ACCEPTANCE.md`. The current ten-event local model burst misses the 15-second objective, and no hardware recommendation follows from this preview.
