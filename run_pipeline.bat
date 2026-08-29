@echo off
setlocal
cd /d "%~dp0"
set "PIPELINE_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PIPELINE_PYTHON=.venv\Scripts\python.exe"
"%PIPELINE_PYTHON%" scripts\run_pipeline.py %*
exit /b %errorlevel%
