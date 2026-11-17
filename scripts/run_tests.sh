#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== EXE-fingerprinter pre-release check ==="
PYTHONPATH="$REPO_ROOT/src" python3 "$SCRIPT_DIR/prerelease_check.py"
