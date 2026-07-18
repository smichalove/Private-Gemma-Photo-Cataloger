@echo off
:: ============================================================================
:: GEMMA 4 VLM PHOTO CATALOGER RUNNER
:: ============================================================================
:: This Windows Command script acts as the main orchestrator for the local photo
:: indexing and metadata tagging pipeline. It handles the local environment setup,
:: activates the virtual environment, resolves directory mappings, and launches
:: the Python-based VLM parser.
::
:: Workflow:
::   1. Virtual Env Setup: Assures the workspace-isolated 'venv' exists.
::   2. Environment Activation: Activates the venv to resolve dependency packages.
::   3. Argument Forwarding: Passes all CLI flags (like --dir, --max-photos, etc.)
::      directly to the underlying Python orchestrator.
:: ============================================================================
setlocal enabledelayedexpansion

echo ===========================================================================
echo 📸 Starting Local Gemma 4 VLM Photo Cataloger
echo ===========================================================================
echo [VIBE NOTE] This runner acts as the lightweight host orchestrator.
echo             It runs directory crawling and EXIF embedding natively on your 
echo             Windows host, while dispatching vision queries to the Ubuntu server.
echo ===========================================================================
echo(

:: ----------------------------------------------------------------------------
:: STEP 1: RESOLVING RUNTIME ENVIRONMENT
:: ----------------------------------------------------------------------------
:: We check if the python virtual environment exists. The script expects a standard
:: 'venv' directory located alongside this script (%~dp0 resolves to the batch
:: file's parent folder directory).
:: ----------------------------------------------------------------------------
echo [INFO] Inspecting system paths...
if exist "%~dp0venv\Scripts\activate.bat" (
    echo   - Virtual Env:  "%~dp0venv\Scripts\activate.bat" [FOUND]
) else (
    echo   - Virtual Env:  "%~dp0venv\Scripts\activate.bat" [NOT FOUND]
)
if exist "%~dp0local\describe_photos.py" (
    echo   - Orchestrator: "%~dp0local\describe_photos.py" [FOUND]
) else (
    echo   - Orchestrator: "%~dp0local\describe_photos.py" [NOT FOUND]
)
echo   - Working Dir:   "%CD%"
echo(

:: ----------------------------------------------------------------------------
:: STEP 2: VERIFYING VIRTUAL ENVIRONMENT INTEGRITY
:: ----------------------------------------------------------------------------
:: If the activation script is missing, we stop execution immediately. Vibe coders
:: can create the environment by running: python -m venv venv
:: ----------------------------------------------------------------------------
if not exist "%~dp0venv\Scripts\activate.bat" (
    echo [ERROR] Python virtual environment 'venv' was not found.
    echo         Please create it first in the project root:
    echo         python -m venv "%~dp0venv"
    echo         And install requirements: pip install -r requirements.txt
    echo(
    pause
    exit /b 1
)

:: ----------------------------------------------------------------------------
:: STEP 3: ACTIVATING ISOLATED PYTHON ENVIRONMENT
:: ----------------------------------------------------------------------------
:: Triggers the virtual environment activation. This overrides current command
:: shell pathing variables to target venv/Scripts/python.exe.
:: ----------------------------------------------------------------------------
echo [INFO] Activating virtual environment...
call "%~dp0venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate the virtual environment.
    pause
    exit /b %ERRORLEVEL%
)

:: ----------------------------------------------------------------------------
:: STEP 4: EXECUTING ORCHESTRATOR PIPELINE
:: ----------------------------------------------------------------------------
:: Spawns the main Python script ('local/describe_photos.py') forwarding:
::   - --embed-exif: Enables native EXIF tag embedding via ExifTool.
::   - --batch-size 2: Standard safe concurrent vision processing.
::   - %*: Forwards all additional CLI parameters specified by the developer.
:: ----------------------------------------------------------------------------
echo [INFO] Running orchestrator...
python "%~dp0local\describe_photos.py" --embed-exif --batch-size 2 %*

if %ERRORLEVEL% neq 0 (
    echo(
    echo [ERROR] Pipeline execution failed with exit code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)

echo(
echo [SUCCESS] Pipeline execution completed successfully.
pause

