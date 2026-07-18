#!/bin/bash
# sh/run_db_chat.sh
# Runs db_chat_repl.py in remote mode natively.
# Note: This script can be run under Windows WSL (e.g. wsl -u {your_user}) or natively on other Ubuntu clones.

SCRIPT_DIR="$( cd "$( dirname "$0" )/.." && pwd )"
cd "$SCRIPT_DIR" || exit 1

PYTHON_EXEC="python3"
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    PYTHON_EXEC="python"
fi

export PYTHONPATH="$SCRIPT_DIR/local:$PYTHONPATH"

echo "=================================================="
echo "Starting Database Chat REPL (Remote Mode)"
echo "=================================================="
echo

export DB_PATH="$SCRIPT_DIR/local/photo_catalog.db"
export PROMPT_FILE="db_prompt.txt"

"$PYTHON_EXEC" local/db_chat_repl.py --remote "$@"
