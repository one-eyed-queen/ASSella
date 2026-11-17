#!/usr/bin/env bash
set -euo pipefail

# EXE-fingerprinter installer.
# Installs the TUI app into ~/.local/share/EXE-fingerprinter and a launcher into
# ~/.local/bin. No SLSsteam, no desktop daemons, no Qt. Needs: git, python3.

SOURCE_REPO="https://github.com/one-eyed-queen/ASSella.git"
BRANCH="${EXEFP_BRANCH:-main}"
INSTALL_DIR="${EXE_FINGERPRINTER_HOME:-$HOME/.local/share/EXE-fingerprinter}"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_PATH="$HOME/.local/share/icons/hicolor/256x256/apps/exe_fingerprinter.png"
DESKTOP_ENTRY="$DESKTOP_DIR/exe-fingerprinter.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$BIN_DIR/exe-fingerprinter" "$DESKTOP_ENTRY" "$ICON_PATH"
    rmdir "$INSTALL_DIR" 2>/dev/null || true
    echo "Removed launcher, desktop entry and install dir."
    exit 0
fi

for tool in git python3; do
    command -v "$tool" >/dev/null || { echo "Missing required tool: $tool"; exit 1; }
done

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$(dirname "$ICON_PATH")"

echo "Cloning $SOURCE_REPO ($BRANCH) into $INSTALL_DIR …"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
    git -C "$INSTALL_DIR" init -q 2>/dev/null || true
    git clone --branch "$BRANCH" --depth 1 "$SOURCE_REPO" "$INSTALL_DIR"
fi

echo "Creating virtualenv …"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

cat > "$BIN_DIR/exe-fingerprinter" <<LAUNCHER
#!/usr/bin/env bash
exec "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/src/main.py" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/exe-fingerprinter"

install -m644 "$INSTALL_DIR/exe_fingerprinter_logo.png" "$ICON_PATH" 2>/dev/null || true
cat > "$DESKTOP_ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Name=EXE-fingerprinter
Comment=Download a Steam game's .exe files only
Exec=$BIN_DIR/exe-fingerprinter
Icon=exe_fingerprinter
Terminal=true
Categories=Utility;
DESKTOP

update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
echo ""
echo "Installed. Run it with:   exe-fingerprinter"
echo "   (or: python3 $INSTALL_DIR/src/main.py)"
