#!/bin/bash
# sh/stop_server.sh
# Stops the VLM model server.
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
echo "Stopping VLM Model Server..."
echo "=================================================="
echo

"$PYTHON_EXEC" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR/local'); import wsl_client; wsl_client.stop_wsl_server()"
