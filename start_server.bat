@echo off
:: Windows Batch script to start the WSL2 model server and load weights in advance
setlocal enabledelayedexpansion

echo ==================================================
echo   Starting WSL2 VLM Model Server...
echo ==================================================

echo Resolved Paths at Runtime:
if exist "%~dp0venv\Scripts\activate.bat" (echo   - Virtual Env: "%~dp0venv\Scripts\activate.bat" [FOUND]) else (echo   - Virtual Env: "%~dp0venv\Scripts\activate.bat" [NOT FOUND])
if exist "%~dp0local\wsl_client.py" (echo   - WSL Client:  "%~dp0local\wsl_client.py" [FOUND]) else (echo   - WSL Client:  "%~dp0local\wsl_client.py" [NOT FOUND])
echo   - Working Dir: "%CD%"
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

:: Execute wsl_client startup sequence
echo Launching model server and loading weights...
python -c "import sys; sys.path.insert(0, r'%~dp0local'); import wsl_client; wsl_client.start_wsl_server()"

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to start model server.
    pause
    exit /b %ERRORLEVEL%
)

echo(
echo Model server started and listening successfully!
pause

