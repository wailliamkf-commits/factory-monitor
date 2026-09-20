# Hosted engineering checks

GitHub Actions runs the repository on `windows-latest` and `macos-latest` with
Python 3.12.  Each matrix job installs the desktop, vision, and development
extras; the Windows job uses `setup-windows.ps1` through Windows PowerShell 5.1
to create its project-local environment and install the Windows capture extras.
It then runs an import-only check for the frame-conversion method used by the
Windows adapter, a PowerShell syntax parse, dependency validation, Ruff, the
complete pytest suite with Qt offscreen,
the 94-second synthetic ten-camera capacity smoke, and wheel/sdist builds.

The smoke uses only the built-in labelled synthetic source.  Review is disabled
and the workflow does not download a model, connect to cameras or an NVR, use
live capture, use camera images, or upload generated evidence.  Its JSON report
is retained only in the job log; the generated local files disappear with the
ephemeral hosted runner.

This is portable engineering evidence, not capture or field acceptance.
Windows-hosted CI cannot prove a target factory monitoring client can be
captured, calibrated, enlarged, or returned to its grid; macOS-hosted CI cannot
grant the screen-recording permission or validate an installed client.  The
workflow also does not establish ten-camera model-review capacity, real footage
quality, false-alarm performance, switching safety, or the required field runs.
Those gates remain separate and require recorded evidence on the actual Windows
and macOS target environments.

The provider-frame check starts no capture session and does not require a
screen.  It checks the supported `windows-capture` 2.0.1
`Frame.convert_to_bgr` API used by the adapter.  It does not validate a real
capture source and therefore cannot establish Windows capture acceptance.

The hosted Windows job parses every PowerShell script before invoking the setup
script.  Its resulting project-local `.venv\\Scripts` directory is added to the
remaining job steps' path, so the regression checks use that setup path rather
than a second environment.
