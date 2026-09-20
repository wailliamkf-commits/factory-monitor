# Run on Windows to create a local wheel/sdist. This repository does not cross-compile Windows artifacts.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonBin = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonBin)) {
    Write-Error "未找到项目 .venv；请先由项目维护者准备 Windows 环境。"
    exit 1
}
Set-Location $ProjectDir
& $PythonBin -m build
exit $LASTEXITCODE
