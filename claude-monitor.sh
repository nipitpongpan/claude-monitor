#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    echo "[claude-monitor] First run: creating virtual environment..."
    python3 -m venv venv
    venv/bin/pip install -q -r requirements.txt
    echo "[claude-monitor] Setup complete."
fi

exec venv/bin/python claude_monitor.py "$@"
