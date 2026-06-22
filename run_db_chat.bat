@echo off
setlocal enabledelayedexpansion

echo ==================================================
echo Running Gemma SQLite Photo Database Chat REPL (Remote Mode)
echo ==================================================
echo [WARNING] CAUTION: Do not run this chat REPL concurrently with an active
echo           photo cataloger run (describe_photos.py) on the same GPU, as
echo           it can saturate VRAM and trigger CUDA out-of-memory crashes.
echo           Only run them concurrently if a second/remote server is configured.
echo(

echo Resolved Paths at Runtime:
if exist "%~dp0venv\Scripts\activate.bat" (echo   - Virtual Env: "%~dp0venv\Scripts\activate.bat" [FOUND]) else (echo   - Virtual Env: "%~dp0venv\Scripts\activate.bat" [NOT FOUND])
if exist "%~dp0local\db_chat_repl.py" (echo   - Chat REPL:   "%~dp0local\db_chat_repl.py" [FOUND]) else (echo   - Chat REPL:   "%~dp0local\db_chat_repl.py" [NOT FOUND])
if exist "%~dp0local\photo_catalog.db" (echo   - Database:    "%~dp0local\photo_catalog.db" [FOUND]) else (echo   - Database:    "%~dp0local\photo_catalog.db" [NOT FOUND])
if exist "%~dp0local\db_prompt.txt" (echo   - Prompt:      "%~dp0local\db_prompt.txt" [FOUND]) else (echo   - Prompt:      "%~dp0local\db_prompt.txt" [NOT FOUND])
echo   - Working Dir: "%CD%"
echo(

:: 1. Check if the virtual environment activation script exists
if not exist "%~dp0venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment activation script not found at:
    echo         "%~dp0venv\Scripts\activate.bat"
    echo Please verify that the environment directory is correctly set up.
    goto end
)

:: 2. Attempt to activate the virtual environment
echo Activating virtual environment...
call "%~dp0venv\Scripts\activate.bat"
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

:: 4. Check if the database chat script exists
if not exist "%~dp0local\db_chat_repl.py" (
    echo [ERROR] db_chat_repl.py was not found in local/ directory:
    echo         "%~dp0local"
    goto end
)

:: 5. Execute database chat REPL in remote mode by default
echo Starting database chat REPL (Remote)...
python "%~dp0local\db_chat_repl.py" --db "%~dp0local\photo_catalog.db" --prompt "%~dp0local\db_prompt.txt" --remote %*

if errorlevel 1 (
    echo(
    echo [ERROR] The database chat script exited with errors - Code %ERRORLEVEL%.
) else (
    echo(
    echo [SUCCESS] Database chat session completed.
)

:end
:: Keep window open on completion/crash
echo(
pause

