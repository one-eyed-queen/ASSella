#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/install.sh" ]; then
    exec "$SCRIPT_DIR/install.sh" --uninstall
else
    echo "Running EXE-fingerprinter Uninstaller..."
    INSTALL_DESTINATION="$HOME/.local/share/ACCELA"
    PRIMARY_DESKTOP_ENTRY="$HOME/.local/share/applications/exe_fingerprinter.desktop"
    LEGACY_DESKTOP_ENTRY="$HOME/.local/share/applications/accela.desktop"
    PRIMARY_ICON_PATH="$HOME/.local/share/icons/hicolor/256x256/apps/exe_fingerprinter.png"
    LEGACY_ICON_PATH="$HOME/.local/share/icons/hicolor/256x256/apps/accela.png"
    PRIMARY_SVG_ICON_PATH="$HOME/.local/share/icons/hicolor/scalable/apps/exe_fingerprinter.svg"
    LEGACY_SVG_ICON_PATH="$HOME/.local/share/icons/hicolor/scalable/apps/accela.svg"

    rm -f "$INSTALL_DESTINATION/ExeFingerprinter.AppImage"
    rm -f "$INSTALL_DESTINATION/ACCELA.AppImage"
    rm -f "$INSTALL_DESTINATION/version"
    rm -f "$PRIMARY_DESKTOP_ENTRY" "$LEGACY_DESKTOP_ENTRY"
    rm -f "$PRIMARY_ICON_PATH" "$LEGACY_ICON_PATH" "$PRIMARY_SVG_ICON_PATH" "$LEGACY_SVG_ICON_PATH"

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage.bak" ]; then
        mv "$INSTALL_DESTINATION/ACCELA.AppImage.bak" "$INSTALL_DESTINATION/ACCELA.AppImage"
        echo "Restored ACCELA.AppImage backup."
    fi

    echo "EXE-fingerprinter uninstalled."
fi
