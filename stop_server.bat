@echo off
:: Windows Batch script to stop the WSL2 model server and release VRAM
setlocal enabledelayedexpansion

echo ==================================================
echo Stopping WSL2 model server to release GPU VRAM...
echo ==================================================

echo Resolved Paths at Runtime:
if exist "%~dp0.env" (echo   - Config File: "%~dp0.env" [FOUND]) else (echo   - Config File: "%~dp0.env" [NOT FOUND (using defaults)])
echo   - Working Dir: "%CD%"
echo(

:: Default fallback variables
set WSL_USER=workbench
set VLM_CONTAINER_NAME=trt_llm_build

:: Load configurations from .env if present
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,2 delims==" %%i in ("%~dp0.env") do (
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

