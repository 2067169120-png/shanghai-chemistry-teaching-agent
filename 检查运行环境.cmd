@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Run: py -3.12 -m venv .venv
  pause
  exit /b 2
)
".venv\Scripts\python.exe" "runtime\deeptutor_shchem\check_desktop_environment.py"
pause
