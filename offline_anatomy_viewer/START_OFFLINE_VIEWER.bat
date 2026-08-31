@echo off
cd /d "%~dp0"
python server.py --data-root "..\imaios_data\all_modules"
if errorlevel 1 pause
