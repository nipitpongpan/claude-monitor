@echo off
cd /d "%~dp0"

:: Ensure python3 is available
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [claude-monitor] Python not found. Please install Python from https://python.org and re-run.
    pause
    exit /b 1
)

:: Create venv and install dependencies on first run
if not exist "venv\" (
    echo [claude-monitor] First run: creating virtual environment...
    python -m venv venv
    echo [claude-monitor] Installing dependencies...
    venv\Scripts\pip install -q -r requirements.txt
    echo [claude-monitor] Setup complete.
)

venv\Scripts\python.exe claude_monitor.py %*
