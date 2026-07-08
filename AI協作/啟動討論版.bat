@echo off
REM Per-project AI collab board launcher (ASCII only; all config lives
REM in board_launcher.py next to this file - edit TOOL_DIR / PORT there).
py -3 -X utf8 "%~dp0board_launcher.py"
if errorlevel 1 pause
