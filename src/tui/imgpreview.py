"""Steam header-art cache for the detail panel.

Downloads the store header once per app into a temp dir, opens it as a PIL
image for the terminal picture widget. Steam art is SFW by nature; anything
else (all logic paths, nothing stored long-term) is treated the same.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CACHE = Path(tempfile.mkdtemp(prefix="exe-fingerprinter-art-"))


def _download(url: str, timeout: float = 20.0) -> Optional[Path]:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        import hashlib

        key = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:24]
        ext = ".png" if "png" in resp.headers.get("content-type", "") else ".jpg"
        path = CACHE / f"{key}{ext}"
        path.write_bytes(resp.content)
        return path
    except Exception as exc:
        logger.debug("[Art] header download failed: %s", exc)
        return None


def cached_path(url: str) -> Optional[Path]:
    if not url:
        return None
    import hashlib

    key = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:24]
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path = CACHE / f"{key}{ext}"
        if path.exists():
            return path
    return None


def get_header_image(url: str) -> Optional["object"]:
    """Return a PIL image of the store header, or None."""
    if not url:
        return None
    path = cached_path(url) or _download(url)
    if path is None:
        return None
    try:
        from PIL import Image

        img = Image.open(path)
        img.load()
        return img
    except Exception as exc:
        logger.debug("[Art] header open failed: %s", exc)
        return None


def cleanup() -> None:
    shutil.rmtree(CACHE, ignore_errors=True)
