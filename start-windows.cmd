@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1 || (echo Python 3 is required. Install Python from python.org and try again. & pause & exit /b 1)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe (echo Unable to create virtual environment. & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install ".[chat]"
if errorlevel 1 (echo Dependency installation failed. & pause & exit /b 1)
start "" /b .venv\Scripts\pythonw.exe -m codex_provider_launcher.app
