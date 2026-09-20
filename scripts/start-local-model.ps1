# Explicit local-only Ollama server for a prepared Windows project environment.
$ProjectDir = Split-Path -Parent $PSScriptRoot
$OllamaBin = Join-Path $ProjectDir ".tools\ollama\ollama.exe"
$ModelsDir = Join-Path $ProjectDir "models\ollama"
if (-not (Test-Path $OllamaBin) -or -not (Test-Path $ModelsDir)) {
    Write-Error "缺少项目内本地 Ollama 程序或模型目录。脚本不会下载或调用云端模型。"
    exit 1
}
$env:OLLAMA_HOST = "127.0.0.1:11435"
$env:OLLAMA_NO_CLOUD = "1"
$env:OLLAMA_MODELS = $ModelsDir
Write-Host "Starting local-only Ollama on 127.0.0.1:11435. Keep this window open while local review is needed."
& $OllamaBin serve
exit $LASTEXITCODE
