#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure python3 is available
if ! command -v python3 &>/dev/null; then
    echo "[claude-monitor] python3 not found. Installing..."
    sudo pacman -S --noconfirm python
fi

# Ensure python-venv is available
if ! python3 -m venv --help &>/dev/null; then
    echo "[claude-monitor] python-venv not found. Installing..."
    sudo pacman -S --noconfirm python-virtualenv
fi

# Create venv and install dependencies on first run
if [ ! -d "venv" ]; then
    echo "[claude-monitor] First run: creating virtual environment..."
    python3 -m venv venv
    echo "[claude-monitor] Installing dependencies..."
    venv/bin/pip install -q -r requirements.txt
    echo "[claude-monitor] Setup complete."
fi

exec venv/bin/python claude_monitor.py "$@"
