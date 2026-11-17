#!/bin/sh
# paws-fingerprinter uninstaller — Linux and macOS. Removes the `fingerprint`/`fp`
# launchers, the app menu entry and the icons, and asks before deleting the venv.
set -eu

BIN="$HOME/.local/bin"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/paws-fingerprinter"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"

rm -f "$BIN/fingerprint" "$BIN/fp"
rm -f "$APPS/paws-fingerprinter.desktop"
for size in 16 24 32 48 64 96 128 256; do
    rm -f "$ICONS/${size}x${size}/apps/paws-fingerprinter.png"
done
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q "$APPS" || true; fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true; fi
echo "removed: fingerprint, fp, the app menu entry and icons"

if [ -d "$DATA" ]; then
    printf "delete %s (venv + cloned repo, ~100 MB)? [y/N] " "$DATA"
    read -r answer || answer=""
    case "$answer" in
        y*|Y*) rm -rf "$DATA"; echo "removed $DATA" ;;
        *) echo "kept $DATA (delete it yourself if you want it gone)" ;;
    esac
fi
echo "settings were kept in ~/.config/EXE-fingerprinter (delete that folder yourself too, if you want)"
