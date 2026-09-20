<#
.SYNOPSIS
Create this repository's Windows-only Python 3.12 environment.

.DESCRIPTION
This script creates only .venv under the repository, then installs the local
project with its development, desktop, vision and Windows extras. It does not
write machine/user environment settings, create or alter config.json, start
capture, start Ollama, or download YOLO/Ollama model weights.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectDir ".venv"
$PythonBin = Join-Path $VenvDir "Scripts\python.exe"

function Stop-Setup([string]$Message) {
    Write-Error "Factory Monitor Windows setup failed: $Message"
    exit 1
}

if ($env:OS -ne "Windows_NT") {
    Stop-Setup "This script must run in Windows PowerShell or PowerShell on Windows."
}

try {
    $PythonVersion = & py -3.12 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>&1
    if ($LASTEXITCODE -ne 0) { throw $PythonVersion }
} catch {
    Stop-Setup "Python 3.12 was not found through 'py -3.12'. Install Python 3.12 x64, enable the Python Launcher, then reopen PowerShell."
}

if (Test-Path $PythonBin) {
    try {
        $VenvVersion = & $PythonBin -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
        if ($LASTEXITCODE -ne 0 -or $VenvVersion.Trim() -ne "3.12") { throw $VenvVersion }
    } catch {
        Stop-Setup "Existing .venv is not a usable Python 3.12 environment. Remove only this repository's .venv after checking it is disposable, then rerun this script."
    }
    Write-Host "Reusing project-local Python 3.12 environment: $VenvDir"
} elseif (Test-Path $VenvDir) {
    Stop-Setup ".venv exists but .venv\Scripts\python.exe is missing. Do not overwrite it automatically; inspect or remove this repository-local directory, then rerun."
} else {
    Write-Host "Creating project-local .venv with Python $PythonVersion"
    & py -3.12 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Stop-Setup "Could not create .venv." }
}

Set-Location $ProjectDir
& $PythonBin -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Stop-Setup "Could not update pip inside .venv. Check network/proxy policy and retry." }

& $PythonBin -m pip install -e ".[dev,desktop,vision,windows]"
if ($LASTEXITCODE -ne 0) { Stop-Setup "Could not install project dependencies into .venv. The error above identifies the failed package." }

& $PythonBin -m pip check
if ($LASTEXITCODE -ne 0) { Stop-Setup "Dependency consistency check failed inside .venv." }

& $PythonBin -c "import cv2, httpx, numpy, psutil, PySide6, ultralytics; import windows_capture; print('Python desktop/vision/Windows imports OK')"
if ($LASTEXITCODE -ne 0) { Stop-Setup "Required desktop, vision, or Windows capture import failed. Resolve the error above; do not substitute screenshot polling for WGC."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "ffmpeg is not on PATH. The environment is installed, but install ffmpeg before media inspection or evidence troubleshooting."
} else {
    & ffmpeg -version | Select-Object -First 1
}

if (-not (Test-Path (Join-Path $ProjectDir "models\yolo11n.pt"))) {
    Write-Warning "models\yolo11n.pt is absent. This script never downloads model weights. Follow docs\WINDOWS_HANDOFF_zh.md to manually obtain and verify the approved weight before a live test."
}
if (-not (Test-Path (Join-Path $ProjectDir ".tools\ollama\ollama.exe")) -or -not (Test-Path (Join-Path $ProjectDir "models\ollama"))) {
    Write-Warning "Project-local Ollama executable and/or models are absent. Local model review remains unavailable until they are manually prepared."
}

Write-Host "Windows environment ready. No config was created, no capture was started, and no model was downloaded."
Write-Host "Next: .\\.venv\\Scripts\\python.exe -m factory_monitor init --config config.json"
