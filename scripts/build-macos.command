#!/bin/bash
# Builds a macOS-local wheel/sdist only. It does not produce or validate a Windows build.
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
python_bin="$project_dir/.venv/bin/python"
cd "$project_dir"
exec "$python_bin" -m build
