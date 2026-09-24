#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CandidatePath,
    [ValidateSet('GUI','Preflight','Capacity','Collect')][string]$Mode='GUI'
)
$ErrorActionPreference='Stop'
$bundle=$PSScriptRoot
$ops=Join-Path $bundle 'candidate_ops.py'
$candidate=(Resolve-Path -LiteralPath $CandidatePath).Path
$python=Join-Path $candidate '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run Prepare-Candidate first.' }
$env:PYTHONPATH=Join-Path $candidate 'src'
$env:YOLO_CONFIG_DIR=Join-Path $candidate 'runtime\yolo-settings'
$reports=Join-Path $candidate 'reports\local\windows-validation'
New-Item -ItemType Directory -Path $reports -Force | Out-Null
$config=Join-Path $candidate 'runtime\monitor.json'
$data=Join-Path $candidate 'runtime\data'
$model=Join-Path $bundle 'resources\models\yolo11n.pt'
if (-not (Test-Path -LiteralPath $config)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $config) -Force | Out-Null
    & $python $ops init --candidate $candidate --bundle $bundle
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated uncalibrated configuration.' }
}
Push-Location $candidate
try {
    if ($Mode -eq 'GUI') {
        Write-Host 'Select the actual monitoring window and calibrate cameras before Start. Candidate alerts require human review.'
        & $python -m factory_monitor gui --config $config --data-dir $data --source live
        if ($LASTEXITCODE -ne 0) { throw 'Desktop exited with an error.' }
    } elseif ($Mode -eq 'Preflight') {
        & $python -m factory_monitor preflight --config $config --data-dir $data | Out-File -LiteralPath (Join-Path $reports 'preflight.json') -Encoding utf8
        if ($LASTEXITCODE -ne 0) { throw 'Preflight command failed.' }
        & $python $ops gpu | Out-File -LiteralPath (Join-Path $reports 'gpu.json') -Encoding utf8
        if ($LASTEXITCODE -ne 0) { throw 'GPU probe failed.' }
        Write-Host "Readiness facts saved to $reports. This is not acceptance."
    } elseif ($Mode -eq 'Capacity') {
        & $python $ops gpu --require-cuda
        if ($LASTEXITCODE -ne 0) { throw 'CUDA capacity test cannot run on this environment.' }
        $baseline=Join-Path $bundle 'fixtures\baseline_inference.py'
        $image=Join-Path $bundle 'fixtures\public-bus.jpg'
        & $python (Join-Path $candidate 'scripts\benchmark_detector.py') --worker shared --baseline-module $baseline --model $model --image $image --rounds 10 --worker-report (Join-Path $reports 'detector.json')
        if ($LASTEXITCODE -ne 0) { throw 'Actual detector capacity test failed.' }
        & $python (Join-Path $candidate 'scripts\benchmark_local_review.py') --image $image --frames 2 --output (Join-Path $reports 'review-burst.json')
        if ($LASTEXITCODE -ne 0) { throw 'Review timing or public-negative gate failed. Results were saved; do not relax the deadline.' }
        Write-Host 'Component timing run completed. Inspect supported_on_negative_fixture: a timing pass does not prove accuracy.'
    } else {
        $stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
        $archive=Join-Path $candidate "windows-validation-$stamp.zip"
        $files=@(Get-ChildItem -LiteralPath $reports -File | Where-Object { $_.Extension -in @('.json','.log','.txt') })
        $receipt=Join-Path $candidate 'source-fingerprints.json'
        if (Test-Path -LiteralPath $receipt) { $files += Get-Item -LiteralPath $receipt }
        if ($files.Count -eq 0) { throw 'No diagnostic results exist yet.' }
        Compress-Archive -LiteralPath @($files.FullName) -DestinationPath $archive
        Write-Host "Diagnostic package saved locally: $archive"
        Write-Host 'No monitoring images, evidence videos, credentials, camera configuration, or uploads were included.'
    }
} finally { Pop-Location }
