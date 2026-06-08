@echo off
:: Windows Batch script to force VLM description and metadata embedding on a single image file
setlocal enabledelayedexpansion

:: Ensure working directory is the script's directory
cd /d "%~dp0"

echo ==================================================
echo       Force Re-evaluation on Single File
echo ==================================================
echo(

:: 1. Check if the virtual environment activation script exists
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment 'venv' not found.
    echo Please create it first by running: python -m venv venv
    goto end
)

:: 2. Prompt user for target file path (supports Drag and Drop)
set /p TARGET_FILE="Enter or drag-and-drop the absolute path to the image file: "
if "%TARGET_FILE%"=="" (
    echo [ERROR] No file path specified. Exiting.
    goto end
)

:: Strip surrounding quotes if the user dragged and dropped the file
set TARGET_FILE=%TARGET_FILE:"=%

if not exist "%TARGET_FILE%" (
    echo [ERROR] The specified file does not exist:
    echo         "%TARGET_FILE%"
    goto end
)

:: 3. Attempt to activate the virtual environment
echo(
echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate the virtual environment.
    goto end
)

:: 4. Verify python is available in path
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in path after environment activation.
    goto end
)

:: 5. Execute cataloger for single file with EXIF embedding enabled
echo(
echo Starting single file cataloger run...
python local/describe_photos.py --file "%TARGET_FILE%" --batch-size 1 --embed-exif

if errorlevel 1 (
    echo(
    echo [ERROR] Single file re-evaluation failed - Code %ERRORLEVEL%.
) else (
    echo(
    echo [SUCCESS] Single file re-evaluation completed successfully.
)

:end
echo(
pause
