@echo off
cd /d "%~dp0"
venv\Scripts\python.exe claude_monitor.py %*
