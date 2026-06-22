@echo off
:: Windows Batch script to activate the venv and run the Local Gemma 4 VLM Photo Cataloger
setlocal enabledelayedexpansion

echo ===================================================
echo   Starting Local Gemma 4 VLM Photo Cataloger
echo ===================================================

echo Resolved Paths at Runtime:
if exist "%~dp0venv\Scripts\activate.bat" (echo   - Virtual Env:  "%~dp0venv\Scripts\activate.bat" [FOUND]) else (echo   - Virtual Env:  "%~dp0venv\Scripts\activate.bat" [NOT FOUND])
if exist "%~dp0local\describe_photos.py" (echo   - Orchestrator: "%~dp0local\describe_photos.py" [FOUND]) else (echo   - Orchestrator: "%~dp0local\describe_photos.py" [NOT FOUND])
echo   - Working Dir:   "%CD%"
echo(

:: Check if virtual environment exists
if not exist "%~dp0venv\Scripts\activate.bat" (
    echo [ERROR] Python virtual environment 'venv' not found.
    echo Please create it first by running: python -m venv "%~dp0venv"
    pause
    exit /b 1
)

:: Activate the host virtual environment
echo Activating virtual environment...
call "%~dp0venv\Scripts\activate.bat"

:: Execute the cataloger orchestrator
echo Running orchestrator...
python "%~dp0local\describe_photos.py" --embed-exif --batch-size 2 %*

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Pipeline execution failed.
    pause
    exit /b %ERRORLEVEL%
)

echo Pipeline execution completed successfully.
pause
