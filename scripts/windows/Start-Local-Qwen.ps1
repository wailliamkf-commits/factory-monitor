#requires -Version 5.1
<#
.SYNOPSIS
Extract and start the packaged Ollama server for a candidate using local assets only.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$CandidatePath
)

$ErrorActionPreference = "Stop"
$PackageRoot = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "offline_bundle.py")) {
    $PSScriptRoot
} else {
    Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$OllamaArchive = Join-Path $PackageRoot "resources\ollama\ollama-windows-amd64.zip"
$BundleVerifier = Join-Path $PackageRoot "offline_bundle.py"
$ModelsDir = Join-Path $PackageRoot "resources\models\ollama"

function Assert-PathExists([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description is missing: $Path"
    }
}

function Get-OllamaExecutable([string]$Root) {
    $matches = @(Get-ChildItem -LiteralPath $Root -Filter "ollama.exe" -File -Recurse)
    if ($matches.Count -ne 1) {
        throw "Expected exactly one ollama.exe under $Root; found $($matches.Count)."
    }
    return $matches[0].FullName
}

function Test-PortInUse([int]$Port) {
    $getNetTcp = Get-Command "Get-NetTCPConnection" -ErrorAction SilentlyContinue
    if ($null -ne $getNetTcp) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        if ($listeners.Count -gt 0) {
            return $true
        }
    }

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($pending)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$candidateFull = [System.IO.Path]::GetFullPath($CandidatePath)
Assert-PathExists $candidateFull "Candidate directory"
if (-not (Test-Path -LiteralPath $candidateFull -PathType Container)) {
    throw "CandidatePath must be a directory: $candidateFull"
}
Assert-PathExists $BundleVerifier "Offline bundle verifier"
$candidatePython = Join-Path $candidateFull ".venv\Scripts\python.exe"
Assert-PathExists $candidatePython "Candidate-local Python 3.12 interpreter"
& $candidatePython $BundleVerifier --verify $PackageRoot
$verifyExit = $LASTEXITCODE
if ($verifyExit -ne 0) {
    throw "Offline bundle manifest verification failed; Ollama was not extracted or started. Exit code: $verifyExit"
}
Assert-PathExists $OllamaArchive "Original packaged Ollama archive"
Assert-PathExists $ModelsDir "Packaged Ollama model directory"

$modelManifest = Join-Path $ModelsDir "manifests\registry.ollama.ai\library\qwen3-vl\2b-instruct"
$modelBlobs = Join-Path $ModelsDir "blobs"
Assert-PathExists $modelManifest "qwen3-vl:2b-instruct model manifest"
Assert-PathExists $modelBlobs "Ollama model blob directory"
if (@(Get-ChildItem -LiteralPath $modelBlobs -File -ErrorAction Stop).Count -eq 0) {
    throw "Packaged Ollama model blob directory is empty: $modelBlobs"
}

if (Test-PortInUse 11435) {
    throw "Port 11435 is already in use. No process was stopped; inspect the existing listener before retrying."
}

$toolsDir = Join-Path $candidateFull ".tools"
$ollamaDir = Join-Path $toolsDir "ollama"
New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null

if (Test-Path -LiteralPath $ollamaDir) {
    $ollamaExe = Get-OllamaExecutable $ollamaDir
} else {
    $stagingDir = Join-Path $toolsDir (".ollama-stage-" + [guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $stagingDir | Out-Null
        Expand-Archive -LiteralPath $OllamaArchive -DestinationPath $stagingDir -Force
        Get-OllamaExecutable $stagingDir | Out-Null
        Move-Item -LiteralPath $stagingDir -Destination $ollamaDir
    } catch {
        throw "Could not safely extract the packaged Ollama distribution: $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $stagingDir) {
            Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    $ollamaExe = Get-OllamaExecutable $ollamaDir
}

$oldNoCloud = $env:OLLAMA_NO_CLOUD
$oldHost = $env:OLLAMA_HOST
$oldModels = $env:OLLAMA_MODELS
try {
    $env:OLLAMA_NO_CLOUD = "1"
    $env:OLLAMA_HOST = "127.0.0.1:11435"
    $env:OLLAMA_MODELS = $ModelsDir
    Write-Host "Starting the packaged local Ollama server at 127.0.0.1:11435."
    Write-Host "Cloud features are disabled; model storage is read from the offline package."
    & $ollamaExe serve
    $serverExit = $LASTEXITCODE
    if ($serverExit -ne 0) {
        throw "Ollama server exited with code $serverExit."
    }
} finally {
    if ($null -eq $oldNoCloud) { Remove-Item Env:OLLAMA_NO_CLOUD -ErrorAction SilentlyContinue } else { $env:OLLAMA_NO_CLOUD = $oldNoCloud }
    if ($null -eq $oldHost) { Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue } else { $env:OLLAMA_HOST = $oldHost }
    if ($null -eq $oldModels) { Remove-Item Env:OLLAMA_MODELS -ErrorAction SilentlyContinue } else { $env:OLLAMA_MODELS = $oldModels }
}
