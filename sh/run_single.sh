#!/bin/bash
# sh/run_single.sh
# Test runs the cataloger pipeline on a specific test image directory.
# Note: This script can be run under Windows WSL (e.g. wsl -u {your_user}) or natively on other Ubuntu clones.

SCRIPT_DIR="$( cd "$( dirname "$0" )/.." && pwd )"
cd "$SCRIPT_DIR" || exit 1

PYTHON_EXEC="python3"
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    PYTHON_EXEC="python"
fi

export PYTHONPATH="$SCRIPT_DIR/local:$PYTHONPATH"

# Run describe_photos.py on the images/ directory
"$PYTHON_EXEC" local/describe_photos.py --dir images --batch-size 1 "$@"
