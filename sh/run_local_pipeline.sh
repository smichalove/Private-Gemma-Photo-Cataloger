#!/bin/bash
# sh/run_local_pipeline.sh
# Runs the local photo describing/indexing pipeline.
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
echo "Starting Local Photo Cataloger Pipeline"
echo "=================================================="
echo

"$PYTHON_EXEC" local/describe_photos.py "$@"
