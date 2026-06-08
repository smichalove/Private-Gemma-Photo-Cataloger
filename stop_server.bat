@echo off
:: Windows Batch script to stop the WSL2 model server and release VRAM
setlocal enabledelayedexpansion

:: Ensure the working directory is the script's directory
cd /d "%~dp0"

echo ==================================================
echo Stopping WSL2 model server to release GPU VRAM...
echo ==================================================
echo(

:: Default fallback variables
set WSL_USER=workbench
set VLM_CONTAINER_NAME=trt_llm_build

:: Load configurations from .env if present
if exist .env (
    for /f "usebackq tokens=1,2 delims==" %%i in (".env") do (
        set "key=%%i"
        set "val=%%j"
        if "!key!"=="WSL_USER" set "WSL_USER=!val!"
        if "!key!"=="VLM_CONTAINER_NAME" set "VLM_CONTAINER_NAME=!val!"
    )
)

:: Stop the model server container
if "%WSL_USER%"=="" (
    wsl docker stop %VLM_CONTAINER_NAME%
) else (
    wsl -u %WSL_USER% docker stop %VLM_CONTAINER_NAME%
)

echo(
echo Model server stopped successfully! VRAM released.
pause
