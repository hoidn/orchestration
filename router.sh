#!/bin/bash
# Wrapper to run router via Python module.
# Place this at your project root or symlink to it.
#
# Usage: ./router.sh [args...]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If this script is in scripts/orchestration/, run as module from project root
if [[ "$(basename "$(dirname "$SCRIPT_DIR")")" == "scripts" ]]; then
    cd "$(dirname "$(dirname "$SCRIPT_DIR")")"
    exec python -m scripts.orchestration.router "$@"
else
    # Script is at project root, assume scripts/orchestration exists
    exec python -m scripts.orchestration.router "$@"
fi
