#!/bin/sh
# paws-fingerprinter installer, any linux/macOS. no root, nothing outside ur home.
#
#   git clone https://github.com/one-eyed-queen/paws-fingerprinter && cd paws-fingerprinter && ./install.sh
#   — or, from anywhere:  sh -c "$(curl -fsSL https://raw.githubusercontent.com/one-eyed-queen/paws-fingerprinter/main/install.sh)"
#
# makes a private python env in ~/.local/share/paws-fingerprinter/venv, links `fingerprint` and `fp` into
# ~/.local/bin, adds the icon + app menu entry. works the same on arch, debian, ubuntu, fedora, opensuse,
# void, gentoo, steamos (read-only parts untouched) and macOS. run it again to update.
#
# options: --dry-run  --no-desktop  --no-launcher  --prefix DIR  --bin DIR  --python PATH  --help
set -eu

SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP=paws-fingerprinter
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
BIN="$HOME/.local/bin"
PYTHON=""
DRY=0
DESKTOP=1
LAUNCHER=1

say() { printf '%s\n' "$*"; }
die() { printf 'paws-fingerprinter installer: %s\n' "$*" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then say "  would run: $*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --no-desktop) DESKTOP=0 ;;
        --no-launcher) LAUNCHER=0 ;;
        --prefix) shift; [ $# -gt 0 ] || die "--prefix needs a folder"; PREFIX="$1" ;;
        --bin) shift; [ $# -gt 0 ] || die "--bin needs a folder"; BIN="$1" ;;
        --python) shift; [ $# -gt 0 ] || die "--python needs a path"; PYTHON="$1" ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

# run from a checkout, or clone one?
if [ ! -f "$SRC/src/main.py" ]; then
    command -v git >/dev/null 2>&1 || die "need git to clone the app"
    PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
    say "paws-fingerprinter: cloning into $PREFIX/repo …"
    run mkdir -p "$PREFIX"
    if [ ! -d "$PREFIX/repo/.git" ]; then
        run git clone --depth 1 https://github.com/one-eyed-queen/paws-fingerprinter.git "$PREFIX/repo"
    else
        run git -C "$PREFIX/repo" pull --ff-only
    fi
    SRC="$PREFIX/repo"
fi
[ -f "$SRC/src/main.py" ] || die "this doesn't look like a paws-fingerprinter checkout ($SRC has no src/main.py)"
[ "$(id -u)" -ne 0 ] || die "run it as your normal user, not root"

# ---- a python that's new enough (3.10+) ----------------------------------------------------------
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }

if [ -z "$PYTHON" ]; then
    for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cand" >/dev/null 2>&1 && py_ok "$cand"; then PYTHON=$(command -v "$cand"); break; fi
    done
fi
[ -n "$PYTHON" ] || die "need python 3.10 or newer (python3.13 recommended)"
py_ok "$PYTHON" || die "$PYTHON is older than 3.10"

say "paws-fingerprinter: installing from $SRC"
say "  python   $PYTHON ($("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
say "  into     $PREFIX/venv"
say "  command  $BIN/fingerprint (+ fp)"

# ---- the private environment ----------------------------------------------------------------------
VENV="$PREFIX/venv"
run mkdir -p "$PREFIX" "$BIN"
if [ "$DRY" = 0 ]; then
    if ! "$PYTHON" -m venv "$VENV" >/dev/null 2>&1; then
        hint="install the venv module for your python"
        if command -v apt >/dev/null 2>&1; then hint="sudo apt install python3-venv (debian, ubuntu, mint, pop)"; fi
        die "couldn't create a python environment. $hint, then run this again"
    fi
else
    say "  would run: $PYTHON -m venv $VENV"
fi

run "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$SRC/requirements.txt"

# ---- the `fingerprint` command -------------------------------------------------------------------
if [ "$LAUNCHER" = 1 ]; then
    write_launcher() {
        name="$1"
        cat > "$BIN/$name" <<EOF
#!/bin/sh
# paws-fingerprinter launcher
exec "$VENV/bin/python" "$SRC/src/main.py" "\$@"
EOF
        chmod +x "$BIN/$name"
    }
    if [ "$DRY" = 0 ]; then
        write_launcher fingerprint
        write_launcher fp
    else
        say "  would write $BIN/fingerprint and $BIN/fp"
    fi
fi

# ---- desktop bits ---------------------------------------------------------------------------------
if [ "$DESKTOP" = 1 ]; then
    APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
    SRC_ICON="$SRC/exe_fingerprinter_logo.png"
    if [ -d "$SRC" ] && [ -f "$SRC_ICON" ]; then
        for size in 16 24 32 48 64 96 128 256; do
            run mkdir -p "$ICONS/${size}x${size}/apps"
            if [ "$DRY" = 0 ]; then
                "$VENV/bin/python" - "$SRC_ICON" "$ICONS/${size}x${size}/apps/paws-fingerprinter.png" "$size" <<'PY' || cp "$SRC_ICON" "$ICONS/${size}x${size}/apps/paws-fingerprinter.png"
import sys
from PIL import Image
src, dst, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
Image.open(src).convert("RGBA").resize((n, n), Image.LANCZOS).save(dst, optimize=True)
PY
            else
                say "  would write $ICONS/${size}x${size}/apps/paws-fingerprinter.png"
            fi
        done
    fi
    run mkdir -p "$APPS"
    ENTRY="$APPS/paws-fingerprinter.desktop"
    write_entry() {
        cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=EXE Fingerprinter
GenericName=Fingerprint Steam .exe files
Comment=Search a Steam appid, see the base game's Windows .exes, and fingerprint or download just them
Exec=$BIN/fingerprint
Icon=paws-fingerprinter
Terminal=true
Categories=Utility;
Keywords=fingerprint;fingerprinter;exe;pe;steam;pawn;paw;
StartupNotify=true
DESKTOP
    }
    if [ "$DRY" = 0 ]; then write_entry; else say "  would write $ENTRY"; fi
    if command -v update-desktop-database >/dev/null 2>&1; then run update-desktop-database -q "$APPS" || true; fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then run gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true; fi
    say "  desktop entry $ENTRY (search \"fingerprint\" in your app menu)"
fi

case ":$PATH:" in
    *":${BIN}:"*) ;;
    *)
        say "  note: $BIN isn't on this shell's PATH yet. open a new terminal, or:"
        say "        export PATH=\"$BIN:\$PATH\""
        ;;
esac

if [ "$DRY" = 1 ]; then say "dry run: nothing was changed"; else say "done. type: fingerprint"; fi
