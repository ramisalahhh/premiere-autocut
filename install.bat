@echo off
setlocal
echo ============================================
echo   Installing AutoCut for Premiere Pro
echo ============================================

rem Allow unsigned extensions (Premiere 2022 = CEP 11)
for %%N in (10 11 12) do reg add "HKCU\Software\Adobe\CSXS.%%N" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul

set "DEST=%APPDATA%\Adobe\CEP\extensions\AutoCut"
echo Copying to %DEST%
if not exist "%DEST%" mkdir "%DEST%"
robocopy "%~dp0." "%DEST%" /E /XD .venv cache .git .github tests docs /XF install.bat *.md pyproject.toml requirements-dev.txt .gitignore .gitattributes LICENSE /NFL /NDL /NJH /NJS /NP >nul
if %ERRORLEVEL% GEQ 8 (
  echo [!] Copy failed.
  pause
  exit /b 1
)

call "%DEST%\engine\setup_engine.bat"
