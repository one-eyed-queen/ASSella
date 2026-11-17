#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$SCRIPT_DIR/install.sh" ]]; then
    exec "$SCRIPT_DIR/install.sh" --uninstall
fi
INSTALL_DIR="$HOME/.local/share/EXE-fingerprinter"
rm -f "$HOME/.local/bin/exe-fingerprinter" \
      "$HOME/.local/share/applications/exe-fingerprinter.desktop" \
      "$HOME/.local/share/icons/hicolor/256x256/apps/exe_fingerprinter.png"
rmdir "$INSTALL_DIR" 2>/dev/null || true
echo "EXE-fingerprinter uninstalled."
