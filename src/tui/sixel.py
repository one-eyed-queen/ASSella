"""Sixel image support for the TUI.

The banner is drawn with sixel graphics when the terminal supports them, and
falls back to Rich text when it does not. Detection is deliberately
conservative: a wrong guess prints escape garbage, so only terminals known to
understand sixel are used, and ``--no-sixel`` or ``EXEFP_NO_SIXEL=1`` always
wins.

On Windows the practical sixel terminal is Windows Terminal (1.22+); without
``WT_SESSION`` the TUI uses the text banner. Encoding is done in-process with
Pillow when ``img2sixel`` is not installed.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Terminals that implement DEC sixel graphics.
_SIXEL_TERM_HINTS = (
    "sixel",
    "wezterm",
    "foot",
    "mlterm",
    "yaft",
    "contour",
    "mintty",
)


def _forced() -> bool | None:
    """True/False from CLI-style env vars, None when the terminal decides."""
    if os.environ.get("EXEFP_NO_SIXEL") or os.environ.get("EXEFP_SIXEL") == "0":
        return False
    if os.environ.get("EXEFP_SIXEL") == "1":
        return True
    return None


def supports_sixel() -> bool:
    forced = _forced()
    if forced is not None:
        return forced

    term = (os.environ.get("TERM", "") + " " + os.environ.get("TERM_PROGRAM", "")).lower()
    if any(hint in term for hint in _SIXEL_TERM_HINTS):
        return True

    if os.environ.get("WT_SESSION"):
        # Windows Terminal understands sixel from 1.22 onwards. There is no
        # reliable version probe, so assume a modern build.
        return sys.platform == "win32" or bool(os.environ.get("WT_SESSION"))
    if os.environ.get("LC_TERMINAL", "").lower() == "iterm2":
        return True  # iTerm2 has sixel support (disabled by default, harmless)
    return False


def _encode_with_img2sixel(data: bytes, width_px: int) -> bytes | None:
    binary = shutil.which("img2sixel")
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "-w", str(width_px)],
            input=data,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("[TUI] img2sixel failed: %s", exc)
    return None


def _encode_with_pillow(path: str, width_px: int, background: str) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        image = Image.open(path).convert("RGBA")
    except OSError as exc:
        logger.debug("[TUI] Could not open banner image: %s", exc)
        return None

    ratio = image.height / max(image.width, 1)
    height_px = max(6, int(width_px * ratio))
    image = image.resize((width_px, height_px), Image.Resampling.LANCZOS)

    # Composite transparency over the theme background.
    try:
        bg = Image.new("RGBA", image.size, background)
        image = Image.alpha_composite(bg, image)
    except (ValueError, TypeError):
        image = image.convert("RGB")

    quantized = image.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    palette = quantized.getpalette() or []
    pixels = quantized.load()
    width, height = quantized.size

    out = bytearray(b"\x1bPq")  # DCS, P1=0 P2=0 P3=0, sixel
    out += b'"1;1;%d;%d' % (width, height)

    for band_start in range(0, height, 6):
        band = []
        for y in range(band_start, min(band_start + 6, height)):
            band.append([pixels[x, y] for x in range(width)])
        used = sorted({value for row in band for value in row})
        for colour_index in used:
            r = palette[colour_index * 3] if colour_index * 3 < len(palette) else 0
            g = palette[colour_index * 3 + 1] if colour_index * 3 + 1 < len(palette) else 0
            b = palette[colour_index * 3 + 2] if colour_index * 3 + 2 < len(palette) else 0
            # Sixel palette entries are percentages of 255.
            out += b"#%d;2;%d;%d;%d" % (
                colour_index,
                round(r * 100 / 255),
                round(g * 100 / 255),
                round(b * 100 / 255),
            )
            run_char = None
            run_len = 0
            for x in range(width):
                bits = 0
                for offset, row in enumerate(band):
                    if row[x] == colour_index:
                        bits |= 1 << offset
                char = 0x3F + bits
                if char == run_char:
                    run_len += 1
                else:
                    if run_char is not None:
                        out += bytes([run_char]) if run_len == 1 else b"!%d%c" % (run_len, run_char)
                    run_char = char
                    run_len = 1
            if run_char is not None:
                out += bytes([run_char]) if run_len == 1 else b"!%d%c" % (run_len, run_char)
            out += b"$"  # carriage return to the left edge for the next colour
        out += b"-"  # next six-row band

    out += b"\x1b\\"  # ST
    return bytes(out)


def render_banner(path: str | Path, width_cells: int = 40, background: str = "#111318") -> bytes | None:
    """Return sixel bytes for ``path``, or None when unavailable."""
    path = str(path)
    if not os.path.exists(path):
        return None
    width_px = max(64, width_cells * 8)
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None

    encoded = _encode_with_img2sixel(data, width_px)
    if encoded:
        return encoded
    return _encode_with_pillow(path, width_px, background)


def find_logo() -> str | None:
    """Locate the EXE-fingerprinter logo used for the TUI banner."""
    candidates = []
    try:
        from utils.helpers import get_base_path

        base = Path(get_base_path())
    except Exception:
        base = Path(__file__).resolve().parent.parent

    candidates += [
        base / "exe_fingerprinter_logo.png",
        base / "res" / "logo" / "exe_fingerprinter_logo.png",
        Path(__file__).resolve().parent.parent / "res" / "logo" / "exe_fingerprinter_logo.png",
        Path(__file__).resolve().parent.parent.parent / "exe_fingerprinter_logo.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None
