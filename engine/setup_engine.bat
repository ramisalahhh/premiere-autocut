@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo   AutoCut engine setup
echo ============================================

set "PY="
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; assert sys.version_info[:2]>=(3,10)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo [!] Python 3.10 or newer not found.
  echo     Install Python from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH". Then run this file again.
  pause
  exit /b 1
)
echo Using: %PY%

if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

echo.
echo Downloading the Whisper model (about 3 GB, one time only)...
".venv\Scripts\python.exe" -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8'); print('model ready')" || goto :fail

echo.
echo Downloading the face-tracking model...
".venv\Scripts\python.exe" -c "import vision; print(vision.ensure_model())" || echo [!] Face model download failed - reading detection will be skipped until it succeeds.

echo.
".venv\Scripts\python.exe" check_setup.py
echo.
echo Done. Restart Premiere Pro and open Window ^> Extensions ^> AutoCut
pause
exit /b 0

:fail
echo.
echo [!] Setup failed - scroll up for the error.
pause
exit /b 1
