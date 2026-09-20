#!/bin/bash
# Explicit local-only Ollama server. The GUI never starts this process for you.
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
ollama_bin="$project_dir/.tools/ollama/ollama"
models_dir="$project_dir/models/ollama"

if [ ! -x "$ollama_bin" ] || [ ! -d "$models_dir" ]; then
  osascript -e 'display alert "Factory Monitor" message "缺少项目内的本地 Ollama 程序或模型目录。不会下载或使用云端模型。"'
  exit 1
fi

echo "Starting local-only Ollama on 127.0.0.1:11435. Keep this Terminal window open while local review is needed."
exec env OLLAMA_HOST="127.0.0.1:11435" OLLAMA_NO_CLOUD=1 OLLAMA_MODELS="$models_dir" "$ollama_bin" serve
