@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First create the environment: py -3.12 -m venv .venv
  echo Then install: .venv\Scripts\python.exe -m pip install -r runtime\deeptutor_shchem\desktop_requirements.txt
  pause
  exit /b 2
)
".venv\Scripts\python.exe" "runtime\deeptutor_shchem\desktop_teacher_workbench.pyw"
if errorlevel 1 pause
