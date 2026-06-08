@echo off
:: Ensure working directory is the script's directory (utilities/)
cd /d "%~dp0"

echo ==================================================
echo       Lossless Video Frame Extractor Runner
echo ==================================================
echo(

:: 1. Check if the virtual environment activation script exists
if not exist "..\venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment activation script not found at:
    echo         ..\venv\Scripts\activate.bat
    echo Please verify that the environment directory is correctly set up.
    goto end
)

:: 2. Attempt to activate the virtual environment
echo Activating virtual environment...
call ..\venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate the virtual environment.
    goto end
)

:: 3. Verify python is available in path
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in path after environment activation.
    goto end
)

:: 4. Execute the frame extractor
echo Starting frame extraction...
python extract_video_frames.py

if errorlevel 1 (
    echo(
    echo [ERROR] Frame extraction failed - Code %ERRORLEVEL%.
) else (
    echo(
    echo [SUCCESS] Frame extraction execution completed.
)

:end
echo(
pause
