#!/usr/bin/env bash
# ==============================================================================
# test_arm64_mock.sh — Launch EXE-fingerprinter simulating ARM64 & Armada OS environment
# ==============================================================================

export EXE_FINGERPRINTER_FORCE_ARM64=1
export EXE_FINGERPRINTER_FORCE_ARMADA=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "========================================================"
echo "Launching EXE-fingerprinter in Simulated ARM64 / Armada OS Mode"
echo "  • EXE_FINGERPRINTER_FORCE_ARM64=1"
echo "  • EXE_FINGERPRINTER_FORCE_ARMADA=1"
echo "  • SLSsteam Integration Gated Off"
echo "========================================================"

exec python3 src/main.py "$@"
