@echo off
:: Windows Batch script to start the WSL2 model server and load weights in advance
setlocal enabledelayedexpansion

:: Ensure the working directory is the script's directory
cd /d "%~dp0"

echo ==================================================
echo   Starting WSL2 VLM Model Server...
echo ==================================================
echo(

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

:: Execute wsl_client startup sequence
echo Launching model server and loading weights...
python -c "import sys; sys.path.insert(0, 'local'); import wsl_client; wsl_client.start_wsl_server()"

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to start model server.
    pause
    exit /b %ERRORLEVEL%
)

echo(
echo Model server started and listening successfully!
pause
