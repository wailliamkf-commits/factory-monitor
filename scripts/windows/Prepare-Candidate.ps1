#requires -Version 5.1
<#
.SYNOPSIS
Build an isolated Windows candidate from a repaired source tree using offline assets.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SourceProject,

    [Parameter(Mandatory = $false)]
    [string]$Destination,

    [Parameter(Mandatory = $false)]
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$PackageRoot = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "offline_bundle.py")) {
    $PSScriptRoot
} else {
    Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$LockFile = Join-Path $PackageRoot "requirements-windows.lock"
$Wheelhouse = Join-Path $PackageRoot "resources\wheelhouse"
$PatchFile = Join-Path $PackageRoot "changes.patch"
$UpgradeScript = Join-Path $PackageRoot "upgrade.py"
$BundleVerifier = Join-Path $PackageRoot "offline_bundle.py"
$GitArchive = Join-Path $PackageRoot "resources\git\mingit.zip"
$YoloWeights = Join-Path $PackageRoot "resources\models\yolo11n.pt"
$OllamaModels = Join-Path $PackageRoot "resources\models\ollama"

function Assert-PathExists([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description is missing: $Path"
    }
}

function Resolve-PythonCommand {
    if ([string]::IsNullOrWhiteSpace($PythonExe)) {
        $launcher = Get-Command "py" -ErrorAction SilentlyContinue
        if ($null -eq $launcher) {
            throw "Python Launcher 'py' was not found. Install Python 3.12 x64 with the launcher or pass -PythonExe."
        }
        return @{
            Executable = $launcher.Source
            PrefixArgs = @("-3.12")
        }
    }

    if (Test-Path -LiteralPath $PythonExe -PathType Leaf) {
        return @{
            Executable = (Resolve-Path -LiteralPath $PythonExe).Path
            PrefixArgs = @()
        }
    }

    $command = Get-Command $PythonExe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Python executable or command was not found: $PythonExe"
    }
    return @{
        Executable = $command.Source
        PrefixArgs = @()
    }
}

function Invoke-CheckedNative([string]$Executable, [string[]]$Arguments, [string]$FailureMessage) {
    & $Executable @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "$FailureMessage Exit code: $code"
    }
}

function Invoke-CheckedPython([string[]]$Arguments, [string]$FailureMessage) {
    $allArguments = @($python.PrefixArgs) + @($Arguments)
    Invoke-CheckedNative $python.Executable $allArguments $FailureMessage
}

function Get-NormalizedPath([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ([string]::Equals($full, $root, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $full
    }
    return $full.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar))
}

$sourceFull = Get-NormalizedPath $SourceProject
Assert-PathExists $sourceFull "Source project directory"
if (-not (Test-Path -LiteralPath $sourceFull -PathType Container)) {
    throw "SourceProject must be a directory: $sourceFull"
}

if ([string]::IsNullOrWhiteSpace($Destination)) {
    $parent = Split-Path -Parent $sourceFull
    $leaf = Split-Path -Leaf $sourceFull
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Destination = Join-Path $parent "$leaf-candidate-$stamp"
}
$destinationFull = Get-NormalizedPath $Destination
if ([string]::Equals($sourceFull, $destinationFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination must be different from SourceProject."
}
if (Test-Path -LiteralPath $destinationFull) {
    throw "Destination already exists; refusing to overwrite or clean it: $destinationFull"
}

Assert-PathExists $UpgradeScript "Candidate upgrade script"
Assert-PathExists $BundleVerifier "Offline bundle verifier"
Assert-PathExists $PatchFile "Candidate patch"
Assert-PathExists $LockFile "Hash-locked Windows requirements"
Assert-PathExists $Wheelhouse "Offline wheelhouse"
Assert-PathExists $GitArchive "Portable Git archive"
Assert-PathExists $YoloWeights "YOLO11n model weights"
Assert-PathExists $OllamaModels "Complete local Ollama model directory"

$python = Resolve-PythonCommand
$probe = "import json,struct,sys; print(json.dumps({'version':list(sys.version_info[:3]),'bits':struct.calcsize('P')*8}))"
$probeArguments = @($python.PrefixArgs) + @("-c", $probe)
$pythonInfoText = & $python.Executable @probeArguments
$probeExit = $LASTEXITCODE
if ($probeExit -ne 0) {
    throw "Could not inspect Python version and architecture. Exit code: $probeExit"
}
try {
    $pythonInfo = ($pythonInfoText -join "").Trim() | ConvertFrom-Json
} catch {
    throw "Python did not return readable version/architecture metadata."
}
if ($pythonInfo.version.Count -lt 2 -or $pythonInfo.version[0] -ne 3 -or $pythonInfo.version[1] -ne 12) {
    throw "Expected Python 3.12; found $($pythonInfo.version -join '.')."
}
if ($pythonInfo.bits -ne 64) {
    throw "Expected 64-bit Python 3.12; found $($pythonInfo.bits)-bit Python."
}
Write-Host "Using Python $($pythonInfo.version -join '.') ($($pythonInfo.bits)-bit): $($python.Executable)"
Invoke-CheckedPython @($BundleVerifier, "--verify", $PackageRoot) "Offline bundle manifest verification failed; no extraction or candidate upgrade was attempted."

$gitTemp = Join-Path ([System.IO.Path]::GetTempPath()) ("FactoryMonitorGit-" + [guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Path $gitTemp -Force | Out-Null
    Expand-Archive -LiteralPath $GitArchive -DestinationPath $gitTemp -Force
    $gitCandidates = @(Get-ChildItem -LiteralPath $gitTemp -Filter "git.exe" -File -Recurse |
        Where-Object { $_.FullName -match "[\\/]cmd[\\/]git\.exe$" })
    if ($gitCandidates.Count -ne 1) {
        throw "Portable Git archive must contain exactly one cmd\git.exe; found $($gitCandidates.Count)."
    }
    $gitExe = $gitCandidates[0].FullName

    $upgradeArgs = @(
        $UpgradeScript,
        "--source", $sourceFull,
        "--destination", $destinationFull,
        "--patch", $PatchFile,
        "--git", $gitExe
    )
    Invoke-CheckedPython $upgradeArgs "Could not create the isolated candidate from the repaired source tree."
} finally {
    if (Test-Path -LiteralPath $gitTemp) {
        Remove-Item -LiteralPath $gitTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $destinationFull -PathType Container)) {
    throw "Upgrade script returned success but the candidate directory is missing: $destinationFull"
}

$candidateModelsDir = Join-Path $destinationFull "models"
$candidateYoloWeights = Join-Path $candidateModelsDir "yolo11n.pt"
if (Test-Path -LiteralPath $candidateYoloWeights) {
    $packagedHash = (Get-FileHash -LiteralPath $YoloWeights -Algorithm SHA256).Hash
    $candidateHash = (Get-FileHash -LiteralPath $candidateYoloWeights -Algorithm SHA256).Hash
    if ($packagedHash -ne $candidateHash) {
        throw "Candidate already contains different YOLO weights; refusing to overwrite them."
    }
} else {
    New-Item -ItemType Directory -Path $candidateModelsDir -Force | Out-Null
    Copy-Item -LiteralPath $YoloWeights -Destination $candidateYoloWeights
    $packagedHash = (Get-FileHash -LiteralPath $YoloWeights -Algorithm SHA256).Hash
    $candidateHash = (Get-FileHash -LiteralPath $candidateYoloWeights -Algorithm SHA256).Hash
    if ($packagedHash -ne $candidateHash) {
        throw "Copied YOLO weights do not match the verified offline package."
    }
}

$venvDir = Join-Path $destinationFull ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (Test-Path -LiteralPath $venvDir) {
    throw "Candidate .venv already exists after upgrade; refusing to reuse an unexpected environment."
}
Invoke-CheckedPython @("-m", "venv", $venvDir) "Could not create the candidate-local Python environment."

$venvVersionText = & $venvPython -c "import struct,sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+'|'+str(struct.calcsize('P')*8))"
$venvVersionExit = $LASTEXITCODE
if ($venvVersionExit -ne 0) {
    throw "Could not verify the new candidate Python environment. Exit code: $venvVersionExit"
}
$venvParts = ($venvVersionText -join "").Trim().Split("|")
if ($venvParts.Count -ne 2 -or $venvParts[0] -ne "3.12" -or $venvParts[1] -ne "64") {
    throw "Candidate .venv must use 64-bit Python 3.12; found '$($venvVersionText -join '')'."
}

Invoke-CheckedNative $venvPython @("-m", "pip", "install", "--no-index", "--find-links", $Wheelhouse, "--require-hashes", "-r", $LockFile) "Offline hash-locked dependency installation failed."
Invoke-CheckedNative $venvPython @("-m", "pip", "install", "--no-index", "--no-build-isolation", "--no-deps", $destinationFull) "Offline project installation failed. Check that the locked environment includes the configured pyproject build backend (setuptools) before retrying."
Invoke-CheckedNative $venvPython @("-m", "pip", "check") "Installed offline dependency consistency check failed."

$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $destinationFull "src"
    $testPaths = @(
        (Join-Path $destinationFull "tests\test_shared_detection.py"),
        (Join-Path $destinationFull "tests\test_inference.py"),
        (Join-Path $destinationFull "tests\test_native_capture.py")
    )
    foreach ($testPath in $testPaths) {
        Assert-PathExists $testPath "Focused Windows validation test"
    }
    Invoke-CheckedNative $venvPython (@("-m", "pytest", "-q") + $testPaths) "Focused Windows validation failed."
} finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $previousPythonPath
    }
}

Write-Host "Offline candidate preparation completed. No old configuration was read, no real camera was started, and no live capture was performed."
Write-Host "Candidate: $destinationFull"
Write-Host "Next: inspect the generated source hash and review the new candidate before any explicitly authorized Seetong session."
