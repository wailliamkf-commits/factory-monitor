# Local Windows launcher. It never opens a live capture before the operator starts it in the UI.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonBin = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonBin)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("未找到项目 .venv。请由项目维护者完成依赖安装。", "Factory Monitor")
    exit 1
}
Set-Location $ProjectDir
if (-not (Test-Path "config.json")) {
    & $PythonBin -m factory_monitor init --config config.json
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    # Only a just-created config is pointed at the prepared local-only model
    # server. Existing operator calibration/review settings are never rewritten.
    if ((Test-Path ".tools\ollama\ollama.exe") -and (Test-Path "models\ollama")) {
        & $PythonBin -c 'from pathlib import Path; from factory_monitor.config import load_config, save_config; p=Path("config.json"); c=load_config(p); c["review"]["endpoint"]="http://127.0.0.1:11435"; save_config(c,p)'
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}
& $PythonBin -m factory_monitor gui --config config.json --data-dir data --source demo
exit $LASTEXITCODE
