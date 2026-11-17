"""Process discovery for the "fingerprint a running process" flow.

Linux reads ``/proc`` (no dependencies, works on any box). Windows is a
best-effort fallback through ``tasklist`` and ``powershell`` — the commandline
and image path come from ``Win32_Process``. Anything that cannot be read is
reported as ``None``/``"unavailable"`` — never guessed.

Privacy rule: command-line arguments that look like credentials
(``--token=...``, ``password=...`` and friends) are redacted before anything
reaches a report or a TOML file — the TOML may be pasted into public issues.
The environment block (which usually holds the real secrets) is never read.
"""

from __future__ import annotations

import logging
import os
import pwd
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

_SECRET_ARG_RE = re.compile(
    r"(^|[\s/])(-{1,2})?\"?(pass(word|wd|phrase)?|token|secret|api[-_]?key|access[-_]?key|auth[-_]?token|client[-_]?secret|private[-_]?key)"
    r"[\w-]*(\"?[=:]\"?[^\s\"']+)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"^[^\s=]*(pass(word|wd)?|token|secret|api[-_]?key|access[-_]?key)[\w-]*[=:].*$",
    re.IGNORECASE,
)


def redact_args(argv: List[str]) -> List[str]:
    """Mask credential-looking arguments. Conservative: masks whole tokens."""
    out: List[str] = []
    for arg in argv:
        text = arg
        if _SECRET_VALUE_RE.match(text):
            text = re.sub(r"([=:]).*$", r"\1***", text)
        else:
            text = _SECRET_ARG_RE.sub(lambda m: m.group(0).split("=")[0] + "=***", text)
        out.append(text)
    return out


def _linux_btime() -> Optional[float]:
    try:
        for line in Path("/proc/stat").read_text("utf-8").splitlines():
            if line.startswith("btime"):
                return float(line.split()[1])
    except OSError:
        return None
    return None


def _split_stat(stat: str) -> Optional[List[str]]:
    start = stat.find("(")
    end = stat.rfind(")")
    if start == -1 or end == -1 or end <= start:
        return None
    rest = stat[end + 1:].split()
    return [stat[:start].strip(), stat[start + 1:end]] + rest


def linux_processes() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return out
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = _split_stat((entry / "stat").read_text("utf-8"))
            if not stat:
                continue
            comm = stat[1]
            ppid = int(stat[3])
            utime = int(stat[13])
            stime = int(stat[14])
            starttime = int(stat[21])
            threads = int(stat[19])
            proc_state = stat[2]
            try:
                exe = os.readlink(entry / "exe")
            except OSError:
                exe = None
            out.append(
                {
                    "pid": pid,
                    "ppid": ppid,
                    "name": comm,
                    "state": proc_state,
                    "exe": exe,
                    "utime_seconds": round(utime / _CLK_TCK, 1),
                    "stime_seconds": round(stime / _CLK_TCK, 1),
                    "threads": threads,
                    "_starttime_ticks": starttime,
                }
            )
        except (OSError, ValueError, IndexError):
            continue
    out.sort(key=lambda row: (row["name"].lower(), row["pid"]))
    return out


def linux_describe(pid: int) -> Dict[str, Any]:
    proc = Path(f"/proc/{pid}")
    out: Dict[str, Any] = {"pid": pid}
    btime = _linux_btime()

    stat = _split_stat((proc / "stat").read_text("utf-8"))
    if stat:
        out["name"] = stat[1]
        out["ppid"] = int(stat[3])
        out["state"] = stat[2]
        out["threads"] = int(stat[19])
        utime = int(stat[13])
        stime = int(stat[14])
        out["utime_seconds"] = round(utime / _CLK_TCK, 1)
        out["stime_seconds"] = round(stime / _CLK_TCK, 1)
        if btime is not None:
            started = btime + int(stat[21]) / _CLK_TCK
            out["started_at"] = datetime.fromtimestamp(started, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

    try:
        status = (proc / "status").read_text("utf-8")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                out["rss_kb"] = int(line.split()[1])
            elif line.startswith("VmSize:"):
                out["vms_kb"] = int(line.split()[1])
            elif line.startswith("Uid:"):
                out["uid"] = int(line.split()[1])
            elif line.startswith("Gid:"):
                out["gid"] = int(line.split()[1])
    except OSError:
        pass

    if "uid" in out:
        try:
            out["user"] = pwd.getpwuid(out["uid"]).pw_name
        except (KeyError, OSError):
            out["user"] = str(out["uid"])

    try:
        raw = (proc / "cmdline").read_bytes()
        argv = [part.decode("utf-8", "ignore") for part in raw.split(b"\x00") if part]
        if argv:
            out["argv"] = redact_args(argv)
            out["argv_redacted"] = any(a != b for a, b in zip(argv, out["argv"]))
    except OSError:
        pass

    try:
        out["exe"] = os.readlink(proc / "exe")
    except OSError as exc:
        out["exe"] = None
        out["exe_error"] = str(exc)

    try:
        out["cwd"] = os.readlink(proc / "cwd")
    except OSError:
        out["cwd"] = None

    modules: List[str] = []
    try:
        seen = set()
        for line in (proc / "maps").read_text("utf-8", "ignore").splitlines():
            parts = line.split()
            if len(parts) >= 6:
                candidate = parts[-1]
                if candidate.startswith("/") and candidate not in seen and Path(candidate).exists():
                    seen.add(candidate)
                    modules.append(candidate)
                    if len(modules) >= 64:
                        break
    except OSError as exc:
        out["modules_error"] = str(exc)
    out["modules"] = modules
    return out


def windows_processes() -> List[Dict[str, Any]]:
    """Best effort, untested on this machine: name/pid/memory via tasklist."""
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=20, check=False
        ).stdout.decode("utf-8", "ignore")
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: List[Dict[str, Any]] = []
    import csv
    import io

    for row in csv.reader(io.StringIO(out)):
        if len(row) < 5:
            continue
        name = row[0]
        try:
            pid = int(row[1])
        except ValueError:
            continue
        mem = row[4].replace(",", "").replace(" K", "").strip()
        rows.append({"pid": pid, "ppid": None, "name": name, "exe": None,
                     "rss_kb": int(mem) if mem.isdigit() else None})
    rows.sort(key=lambda row: (row["name"].lower(), row["pid"]))
    return rows


def windows_describe(pid: int) -> Dict[str, Any]:
    """Best effort via PowerShell CIM; fields that fail are simply absent."""
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\"; "
        "if ($p) { $p | Select-Object Name, ProcessId, ParentProcessId, ExecutablePath, "
        "CommandLine, CreationDate, WorkingSetSize | ConvertTo-Json -Compress }"
    )
    out: Dict[str, Any] = {"pid": pid}
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=30, check=False,
        ).stdout.decode("utf-8", "ignore").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["error"] = f"powershell unavailable: {exc}"
        return out
    if not raw:
        out["error"] = "process not found"
        return out
    try:
        import json

        data = json.loads(raw)
    except ValueError:
        out["error"] = "unexpected powershell output"
        return out
    out["name"] = data.get("Name")
    out["ppid"] = data.get("ParentProcessId")
    out["exe"] = data.get("ExecutablePath") or None
    if data.get("CommandLine"):
        import shlex

        argv = shlex.split(data["CommandLine"], posix=False)
        out["argv"] = redact_args(argv)
        out["argv_redacted"] = any(a != b for a, b in zip(argv, out["argv"]))
    if data.get("CreationDate"):
        out["started_at"] = str(data["CreationDate"])
    if data.get("WorkingSetSize"):
        out["rss_kb"] = int(int(data["WorkingSetSize"]) / 1024)
    return out


def supported() -> bool:
    if sys.platform.startswith("linux"):
        return Path("/proc").is_dir()
    if sys.platform in ("win32", "darwin"):
        return True
    return False


def list_processes() -> List[Dict[str, Any]]:
    if sys.platform.startswith("linux"):
        return linux_processes()
    if sys.platform in ("win32", "darwin"):
        return windows_processes()
    return []


def describe_process(pid: int) -> Dict[str, Any]:
    if sys.platform.startswith("linux"):
        try:
            return linux_describe(int(pid))
        except (OSError, ValueError) as exc:
            return {"pid": int(pid), "error": str(exc)}
    if sys.platform in ("win32", "darwin"):
        return windows_describe(int(pid))
    return {"pid": int(pid), "error": "process discovery is not supported on this platform"}
