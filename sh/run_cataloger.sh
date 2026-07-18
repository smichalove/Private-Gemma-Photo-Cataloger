#!/bin/bash
# ============================================================================
# GEMMA 4 VLM PHOTO CATALOGER RUNNER (BASH EDITION)
# ============================================================================
# This script acts as the main orchestrator for Linux hosts, Ubuntu clones,
# and WSL2 environments. It activates the virtual environment, loads pathing
# variables, and executes the python describer.
#
# Note: This script can be run natively under Ubuntu and other Linux clones,
# or under Windows WSL2 by running: wsl -u {your_user} ./sh/run_cataloger.sh
# ============================================================================

SCRIPT_DIR="$( cd "$( dirname "$0" )/.." && pwd )"
cd "$SCRIPT_DIR" || exit 1

# Activate virtual environment if it exists, otherwise fall back to system python3
PYTHON_EXEC="python3"
if [ -f "venv/bin/activate" ]; then
    echo "[INFO] Activating Python virtual environment..."
    source venv/bin/activate
    PYTHON_EXEC="python"
else
    echo "[WARNING] Virtual environment 'venv/bin/activate' not found. Falling back to system python3."
fi

# Ensure PYTHONPATH includes the local/ folder so internal modules resolve
export PYTHONPATH="$SCRIPT_DIR/local:$PYTHONPATH"

echo "=================================================="
echo "📸 Starting Local Photo Cataloger Pipeline"
echo "=================================================="
echo

"$PYTHON_EXEC" local/describe_photos.py --embed-exif --batch-size 2 "$@"
