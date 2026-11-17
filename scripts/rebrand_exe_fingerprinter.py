#!/usr/bin/env python3
"""Rename Executable Downloader -> EXE-fingerprinter.

Protects upstream URLs, protocol constants and historical changelogs.
"""
import os
import re

ROOT = "/home/ken/ASSella"
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"}
EXCLUDE_PREFIXES = [os.path.join(ROOT, "docs", "changelogs")]
EXCLUDE_FILES = {
    os.path.join(ROOT, "CHANGELOG.md"),
    os.path.join(ROOT, "scripts", "rebrand_oneshot.py"),
    os.path.join(ROOT, "scripts", "rebrand_phase2.py"),
    os.path.join(ROOT, "scripts", "rebrand_exe_fingerprinter.py"),
}

REPLACEMENTS = [
    ("Executable Downloader", "EXE-fingerprinter"),
    ("EXECUTABLE DOWNLOADER", "EXE-FINGERPRINTER"),
    ("ExecutableDownloader", "ExeFingerprinter"),
    ("executable downloader", "exe-fingerprinter"),
    ("executabledownloader", "exefingerprinter"),
    ("exe_downloader", "exe_fingerprinter"),
    ("EXE_DOWNLOADER", "EXE_FINGERPRINTER"),
    ("exe-downloader", "exe-fingerprinter"),
]

# Lines containing these are left completely untouched (upstream links, protocol)
PROTECTED = (
    "github.com",
    "assella.pages.dev",
    "raw.githubusercontent.com",
    "X-Assella-Token",
    "ASSella_Hubcap_Bypass",
    "ASSella_Worker_Token",
    "ASSella_Token",
)

URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s)\]>\"'<]*")


def should_skip(path):
    rel = os.path.relpath(path, ROOT)
    parts = rel.split(os.sep)
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if path in EXCLUDE_FILES:
        return True
    for prefix in EXCLUDE_PREFIXES:
        if path.startswith(prefix + os.sep):
            return True
    return False


changed = 0
for dirpath, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    for name in files:
        path = os.path.join(dirpath, name)
        if should_skip(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue

        new_lines = []
        dirty = 0
        for line in text.splitlines(keepends=True):
            if any(tok in line for tok in PROTECTED):
                new_lines.append(line)
                continue
            # Mask URLs so brand text in URLs is preserved
            spans = list(URL_RE.finditer(line))
            pieces = []
            last = 0
            for m in spans:
                pieces.append(("text", line[last:m.start()]))
                pieces.append(("url", m.group(0)))
                last = m.end()
            pieces.append(("text", line[last:]))
            out = []
            for kind, chunk in pieces:
                if kind == "text":
                    for old, new in REPLACEMENTS:
                        chunk = chunk.replace(old, new)
                out.append(chunk)
            new_line = "".join(out)
            if new_line != line:
                dirty += 1
            new_lines.append(new_line)
        if dirty:
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(new_lines))
            changed += 1

print(f"Renamed across {changed} files")
