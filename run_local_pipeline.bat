@echo off
:: Windows Batch script to activate the venv and run the Local Gemma 4 VLM Photo Cataloger
setlocal enabledelayedexpansion

echo ===================================================
echo   Starting Local Gemma 4 VLM Photo Cataloger
echo ===================================================

:: Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Python virtual environment 'venv' not found.
    echo Please create it first by running: python -m venv venv
    pause
    exit /b 1
)

:: Activate the host virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

:: Execute the cataloger orchestrator
echo Running orchestrator...
python local/describe_photos.py --embed-exif --batch-size 2 %*

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Pipeline execution failed.
    pause
    exit /b %ERRORLEVEL%
)

echo Pipeline execution completed successfully.
pause
