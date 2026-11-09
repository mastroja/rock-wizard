@echo off
setlocal
cd /d "%~dp0"
echo === RockWizard: one-time setup ===
where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause & exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
set PY=.venv\Scripts\python.exe
%PY% -m pip install --upgrade pip
echo Installing PyTorch with NVIDIA GPU support ^(large download^)...
%PY% -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 (echo GPU build failed, falling back to CPU build... & %PY% -m pip install torch torchaudio)
%PY% -m pip install -r requirements.txt
%PY% -m pip install --no-deps basic-pitch
if errorlevel 1 (echo Setup failed. & pause & exit /b 1)
echo.
echo Setup complete. Double-click run_app.bat to start.
pause
