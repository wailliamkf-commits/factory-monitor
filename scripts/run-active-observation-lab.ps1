param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $labRoot
if (-not $PythonPath) {
    $localPython = Join-Path $labRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) {
        $PythonPath = $localPython
    } else {
        Write-Host 'Select python.exe from the existing offline project .venv\Scripts folder.'
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = 'Select existing offline project .venv\Scripts\python.exe'
        $dialog.Filter = 'Python executable (python.exe)|python.exe'
        if ($dialog.ShowDialog() -ne 'OK') { exit 2 }
        $PythonPath = $dialog.FileName
    }
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Python executable not found.' }
& $PythonPath -c "import sys,numpy,cv2; assert sys.version_info[:2] == (3,12), 'Python 3.12 required'"
if ($LASTEXITCODE -ne 0) { throw 'Use the prepared offline Python 3.12 environment with numpy and opencv.' }
& $PythonPath (Join-Path $PSScriptRoot 'experiment_active_observation.py') --output (Join-Path $labRoot 'results')
if ($LASTEXITCODE -ne 0) { throw 'Synthetic experiment failed. Preserve results for diagnosis.' }
Write-Host 'Synthetic experiment finished. Results are in the results folder. No screen capture or UI action occurred.'
