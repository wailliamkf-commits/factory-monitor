#!/bin/bash
# Local macOS launcher.  It opens no capture source until the operator presses Start.
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
python_bin="$project_dir/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  osascript -e 'display alert "Factory Monitor" message "未找到项目 .venv。请由项目维护者完成依赖安装。"'
  exit 1
fi

cd "$project_dir"
if [ ! -f "config.json" ]; then
  "$python_bin" -m factory_monitor init --config config.json
  # Only a just-created config is pointed at the bundled local-only model
  # server. Existing operator calibration/review settings are never rewritten.
  if [ -x ".tools/ollama/ollama" ] && [ -d "models/ollama" ]; then
    "$python_bin" -c 'from pathlib import Path; from factory_monitor.config import load_config, save_config; p=Path("config.json"); c=load_config(p); c["review"]["endpoint"]="http://127.0.0.1:11435"; save_config(c,p)'
  fi
fi
exec "$python_bin" -m factory_monitor gui --config config.json --data-dir data --source demo
