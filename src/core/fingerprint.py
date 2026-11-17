"""Static fingerprinting of .exe files.

Everything obtainable from the bytes of a Windows PE — no network, no
debugger, no harness. Collects:

- identity: name, size, MD5/SHA-1/SHA-256, magic, MZ/PE offset, overlay
- layout: arch, subsystem, entry point, image base, sections, sizes, entropy
- payload: imports, exports, .NET flag, version info, CodeView/PDB GUID
- trust: Authenticode cert-table size, signer subject (best-effort)
- content: disassembly around the entry point, interesting strings

Outputs a Python dict (``fingerprint_file``) and a TOML document
(``fingerprint_to_toml``) meant for archiving alongside the fingerprint.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TOOL = "EXE-fingerprinter"
TOOL_VERSION = "3.0.0"

_ARCH = {
    0x14C: "x86",
    0x8664: "x64",
    0x1C0: "arm",
    0x1C4: "arm",
    0xAA64: "arm64",
}

_FILE_CHARACTERISTICS = (
    (0x0002, "executable"),
    (0x0020, "large-address-aware"),
    (0x0080, "bytes-reversed-lo"),
    (0x0100, "32bit-machine"),
    (0x0200, "debug-stripped"),
    (0x1000, "system"),
    (0x2000, "dll"),
    (0x4000, "up-system-only"),
)

_DLL_CHARACTERISTICS = (
    (0x0020, "high-entropy-va"),
    (0x0040, "dynamic-base-aslr"),
    (0x0080, "force-integrity"),
    (0x0100, "nx-compat-dep"),
    (0x0200, "no-isolation"),
    (0x0400, "no-seh"),
    (0x0800, "do-not-bind"),
    (0x1000, "appcontainer"),
    (0x2000, "wdm-driver"),
    (0x4000, "guard-cf-cfg"),
    (0x8000, "terminal-server-aware"),
)

_RESOURCE_TYPES = {
    1: "CURSOR", 2: "BITMAP", 3: "ICON", 4: "MENU", 5: "DIALOG", 6: "STRING",
    7: "FONTDIR", 8: "FONT", 9: "ACCELERATOR", 10: "RCDATA", 11: "MESSAGETABLE",
    12: "GROUP_CURSOR", 13: "GROUP_FONT", 14: "GROUP_ICON", 16: "VERSION",
    17: "DLGINCLUDE", 19: "PLUGPLAY", 20: "VXD", 21: "ANICURSOR", 22: "ANIICON",
    23: "HTML", 24: "MANIFEST",
}

_SUBSYSTEM = {
    1: "native",
    2: "windows-gui",
    3: "windows-console",
    5: "os2-console",
    7: "posix-console",
    9: "windows-ce-gui",
    10: "efi-application",
    11: "efi-boot-service",
    12: "efi-runtime",
    13: "efi-rom",
    14: "xbox",
    16: "windows-boot",
}

_SECTION_FLAG_NAMES = (
    (0x20000000, "executable"), (0x40000000, "readable"), (0x80000000, "writable"),
    (0x02000000, "discardable"), (0x04000000, "not-cached"), (0x08000000, "not-paged"),
)

_INTERESTING_PATTERNS = (
    re.compile(rb"https?://[\w.\-/?&=_%+#:~]{4,140}"),
    re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(rb"HK(?:LM|CU|CR|U|LM-USER)\\[A-Za-z0-9_\\.\-]{3,120}"),
    re.compile(rb"[A-Za-z]:\\\\[^\s\"'<>|\x00]{3,120}"),
    re.compile(rb"%[A-Za-z_]{2,30}%"),
)


# ── hashing ────────────────────────────────────────────────────────────────

def _hashes(path: Path, chunk_size: int = 1 << 16) -> Dict[str, str]:
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    blake2b = hashlib.blake2b(digest_size=32)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
            sha512.update(chunk)
            blake2b.update(chunk)
    return {
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
        "sha256": sha256.hexdigest(),
        "sha512": sha512.hexdigest(),
        "blake2b256": blake2b.hexdigest(),
    }


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    total = len(data)
    entropy = 0.0
    for count in counts:
        if not count:
            continue
        p = count / total
        entropy -= p * math.log2(p)
    return round(entropy, 3)


def _section_flags(characteristics: int) -> List[str]:
    return [name for bit, name in _SECTION_FLAG_NAMES if characteristics & bit]


def _flag_names(value: int, table) -> List[str]:
    return [name for bit, name in table if value & bit]


def _file_ts(path: Path) -> Dict[str, Any]:
    """Filesystem metadata: timestamps, identity, ownership."""
    try:
        st = path.stat()
    except OSError as exc:
        return {"error": str(exc)}

    def _iso(ts: float) -> Optional[str]:
        try:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return None

    birth = getattr(st, "st_birthtime", None)
    try:
        import pwd

        owner = pwd.getpwuid(st.st_uid).pw_name
    except Exception:
        owner = str(st.st_uid)
    try:
        import grp

        group = grp.getgrgid(st.st_gid).gr_name
    except Exception:
        group = str(st.st_gid)
    out: Dict[str, Any] = {
        "size_bytes": st.st_size,
        "modified": _iso(st.st_mtime),
        "changed": _iso(st.st_ctime),
        "accessed": _iso(st.st_atime),
        "created": _iso(birth) if birth is not None else None,
        "inode": st.st_ino,
        "hard_links": st.st_nlink,
        "uid": st.st_uid,
        "gid": st.st_gid,
        "owner": owner,
        "group": group,
        "mode": oct(st.st_mode & 0o7777),
    }
    return out


def _pe_checksum(data: bytes, checksum_offset: int, checksum_valid_range: Optional[int] = None) -> int:
    """The PE header checksum over ``data`` with the field zeroed."""
    end = checksum_valid_range if checksum_valid_range is not None else len(data)
    buf = bytearray(data[:end])
    if 0 <= checksum_offset and checksum_offset + 4 <= len(buf):
        buf[checksum_offset: checksum_offset + 4] = b"\x00\x00\x00\x00"
    if len(buf) % 2:
        buf += b"\x00"
    total = 0
    for i in range(0, len(buf), 2):
        total += int.from_bytes(buf[i: i + 2], "little")
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(buf)) & 0xFFFFFFFF


def _timedatestamp_note(stamp: int) -> Dict[str, Any]:
    try:
        from datetime import datetime, timezone

        moment = datetime.fromtimestamp(stamp, tz=timezone.utc)
        iso = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return {"timedatestamp_raw": stamp, "timedatestamp": None, "timedatestamp_note": "out of range"}
    out: Dict[str, Any] = {"timedatestamp_raw": stamp, "timedatestamp": iso}
    from datetime import datetime as _dt, timezone as _tz

    if stamp == 0:
        out["timedatestamp_note"] = "zero — reproducible-build or scrubbed binary"
    elif moment > _dt.now(_tz.utc):
        out["timedatestamp_note"] = "in the future — the build clock was wrong at link time"
    elif moment.year < 1996:
        out["timedatestamp_note"] = "implausibly old for a PE — likely scrubbed or synthetic"
    return out


def _resources(pe) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        root = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
        for entry in getattr(root, "entries", []) or []:
            rtype = entry.id
            if isinstance(rtype, int):
                type_name = _RESOURCE_TYPES.get(rtype, f"RT_{rtype}")
            else:
                type_name = str(rtype)
            level2 = getattr(getattr(entry, "directory", None), "entries", []) or []
            langs = 0
            for name_entry in level2:
                level3 = getattr(getattr(name_entry, "directory", None), "entries", []) or []
                langs += len(level3)
            out.append(
                {
                    "type": type_name,
                    "type_id": rtype if isinstance(rtype, int) else None,
                    "entries": len(level2),
                    "languages": langs,
                }
            )
    except Exception as exc:
        logger.debug("[Fingerprint] resources unreadable: %s", exc)
    return out


def _version_info(pe) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        if not hasattr(pe, "FileInfo"):
            return out
        for entry in pe.FileInfo:
            if isinstance(entry, (list, tuple)):
                items = entry
            elif hasattr(entry, "__iter__"):
                items = list(entry)
            else:
                items = [entry]
            for item in items:
                if not hasattr(item, "StringTable"):
                    continue
                for table in item.StringTable:
                    for key, value in (getattr(table, "entries", {}) or {}).items():
                        try:
                            k = key.decode("utf-8", "ignore") if isinstance(key, bytes) else str(key)
                            v = value.decode("utf-8", "ignore").strip() if isinstance(value, bytes) else str(value).strip()
                        except Exception:
                            continue
                        if k and v:
                            out[k] = v
    except Exception as exc:
        logger.debug("[Fingerprint] version info unreadable: %s", exc)
    return out


def _debug_info(pe) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        for entry in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []) or []:
            if getattr(entry, "entry_type", None) != 2:
                continue
            pdb = getattr(entry, "PdbFileName", None)
            checksum = getattr(entry, "PdbChecksum", None)
            if pdb:
                out["pdb_path"] = pdb.decode("utf-8", "ignore") if isinstance(pdb, bytes) else str(pdb)
            if checksum:
                raw = bytes(checksum)
                guid = raw[:16].hex()
                out["guid"] = f"{guid[:8]}-{guid[8:12]}-{guid[12:16]}-{guid[16:20]}-{guid[20:32]}"
                out["guid_raw"] = raw.hex()
            age = getattr(entry, "PdbAge", None)
            if age is not None:
                out["age"] = age
    except Exception as exc:
        logger.debug("[Fingerprint] debug directory unreadable: %s", exc)
    return out





def _interesting_strings(path: Path, limit: int = 48) -> List[str]:
    interesting: List[str] = []
    seen = set()
    try:
        with path.open("rb") as handle:
            data = handle.read(16 * 1024 * 1024)
    except OSError:
        return interesting
    for pattern in _INTERESTING_PATTERNS:
        for match in pattern.finditer(data):
            try:
                text = match.group(0).decode("utf-8", "ignore").strip(" ,;.")
            except Exception:
                continue
            if len(text) < 4 or text in seen:
                continue
            seen.add(text)
            interesting.append(text)
            if len(interesting) >= limit:
                return interesting
    return interesting


def _disasm(pe, limit: int = 24) -> List[Dict[str, str]]:
    try:
        if pe.FILE_HEADER.Machine == 0x8664:
            from capstone import CS_ARCH_X86, CS_MODE_64, Cs

            mode = CS_MODE_64
            arch = CS_ARCH_X86
        elif pe.FILE_HEADER.Machine == 0x14C:
            from capstone import CS_ARCH_X86, CS_MODE_32, Cs

            mode = CS_MODE_32
            arch = CS_ARCH_X86
        else:
            return []
        entry_rva = pe.OPTIONAL_HEADER.AddressOfEntryPoint
        entry_va = pe.OPTIONAL_HEADER.ImageBase + entry_rva
        code = Cs(arch, mode)
        code.detail = False
        offset = pe.get_offset_from_rva(entry_rva)
        buf = pe.__data__[offset : offset + 128] if hasattr(pe, "__data__") else None
        if buf is None:
            with Path(pe.filename).open("rb") as handle:
                handle.seek(offset)
                buf = handle.read(128)
        out: List[Dict[str, str]] = []
        for insn in code.disasm(buf, entry_va):
            out.append(
                {
                    "va": f"0x{insn.address:08x}",
                    "bytes": " ".join(f"{b:02x}" for b in insn.bytes),
                    "op": f"{insn.mnemonic} {insn.op_str}".strip(),
                }
            )
            if len(out) >= limit:
                break
        return out
    except Exception as exc:
        logger.debug("[Fingerprint] disassembly failed: %s", exc)
        return []


# ── public API ─────────────────────────────────────────────────────────────

def fingerprint_file(path: str | os.PathLike, source: str = "local file", process: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    path = Path(path)
    size = path.stat().st_size
    hashes = _hashes(path)
    fs_info = _file_ts(path)

    try:
        with path.open("rb") as handle:
            probe = handle.read(16 * 1024 * 1024)
        overall_entropy = _shannon_entropy(probe)
    except OSError:
        overall_entropy = None

    report: Dict[str, Any] = {
        "file": {
            "name": path.name,
            "path": str(path),
            "size_bytes": size,
            **hashes,
            "magic": "MZ",
        },
        "fs": fs_info,
        "source": source,
        "meta": {"tool": TOOL, "tool_version": TOOL_VERSION},
    }
    if process:
        report["process"] = process

    try:
        import pefile

        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DEBUG"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
                ]
            )
        except Exception:
            pass
    except Exception as exc:
        report["error"] = f"not a PE file or unreadable: {exc}"
        report["meta"]["generated"] = _iso_now()
        return report

    fh = pe.FILE_HEADER
    oh = pe.OPTIONAL_HEADER

    # Checksum: verify the PE header checksum. The algorithm excludes the
    # certificate table, so truncate there when one exists (same rule
    # Authenticode uses). Files over 512 MB are skipped rather than slurped.
    checksum_info: Dict[str, Any] = {"checksum_stored": f"0x{oh.CheckSum:x}"}
    try:
        if size <= 512 * 1024 * 1024:
            raw = path.read_bytes()
            cert_dir = None
            try:
                cert_dir = oh.DATA_DIRECTORY[4]
            except Exception:
                pass
            cert_offset = int(cert_dir.VirtualAddress) if cert_dir and cert_dir.Size else None
            e_lfanew = pe.DOS_HEADER.e_lfanew
            oh_start = e_lfanew + 4 + 20
            checksum_offset = oh_start + 64
            computed = _pe_checksum(raw, checksum_offset, cert_offset)
            stored = int(oh.CheckSum or 0)
            checksum_info["checksum_computed"] = f"0x{computed:x}"
            if stored == 0:
                checksum_info["checksum_valid"] = "absent (linker wrote 0 — common for .NET assemblies)"
            else:
                checksum_info["checksum_valid"] = "valid" if stored == computed else "mismatch (file modified after link, e.g. signed)"
        else:
            checksum_info["checksum_valid"] = "skipped (file too large)"
    except Exception as exc:
        logger.debug("[Fingerprint] checksum computation failed: %s", exc)
        checksum_info["checksum_valid"] = "uncomputable"

    resources = _resources(pe)
    file_chars = _flag_names(fh.Characteristics, _FILE_CHARACTERISTICS)
    dll_chars = _flag_names(oh.DllCharacteristics, _DLL_CHARACTERISTICS)
    stamp_info = _timedatestamp_note(int(getattr(fh, "TimeDateStamp", 0) or 0))

    sections = []
    for section in pe.sections:
        try:
            raw = section.get_data()
        except Exception:
            raw = b""
        sections.append(
            {
                "name": section.Name.decode("utf-8", "ignore").rstrip("\x00").strip(),
                "virtual_size": section.Misc_VirtualSize,
                "raw_size": section.SizeOfRawData,
                "entropy": _shannon_entropy(raw[: (1 << 20)]),
                "flags": _section_flags(section.Characteristics),
            }
        )

    imports: List[str] = []
    import_functions = 0
    try:
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
            dll = getattr(entry, "dll", b"")
            imports.append(dll.decode("utf-8", "ignore") if isinstance(dll, bytes) else str(dll))
            import_functions += len(getattr(entry, "imports", []) or [])
    except Exception as exc:
        logger.debug("[Fingerprint] imports unreadable: %s", exc)

    exports: List[str] = []
    try:
        export_table = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        symbols = getattr(export_table, "symbols", None) or []
        for symbol in symbols:
            name = getattr(symbol, "name", None)
            if name:
                exports.append(name.decode("utf-8", "ignore") if isinstance(name, bytes) else str(name))
    except Exception as exc:
        logger.debug("[Fingerprint] exports unreadable: %s", exc)

    report["pe"] = {
        "arch": _ARCH.get(fh.Machine, f"0x{fh.Machine:x}"),
        "subsystem": _SUBSYSTEM.get(oh.Subsystem, str(oh.Subsystem)),
        "entry_point_rva": f"0x{oh.AddressOfEntryPoint:x}",
        "entry_point_va": f"0x{oh.ImageBase + oh.AddressOfEntryPoint:x}",
        "image_base": f"0x{oh.ImageBase:x}",
        "number_of_sections": fh.NumberOfSections,
        "size_of_image": oh.SizeOfImage,
        "size_of_headers": oh.SizeOfHeaders,
        "size_of_code": oh.SizeOfCode,
        **checksum_info,
        "linker_version": f"{oh.MajorLinkerVersion}.{oh.MinorLinkerVersion}",
        "dll_characteristics": oh.DllCharacteristics,
        "dll_characteristic_names": dll_chars,
        "file_characteristics": fh.Characteristics,
        "file_characteristic_names": file_chars,
        "entropy": overall_entropy,
        "resources": resources,
        **stamp_info,
        "sections": sections,
        "imported_dlls": imports,
        "imported_function_count": import_functions,
        "exported_function_count": len(exports),
        "exported_functions": exports[:24],
        "is_dotnet": bool(hasattr(pe, "DIRECTORY_ENTRY_COM_DESCRIPTOR")),
    }
    try:
        if pe.sections:
            last = pe.sections[-1]
            report["pe"]["overlay_offset"] = last.PointerToRawData + last.SizeOfRawData
        else:
            report["pe"]["overlay_offset"] = None
    except Exception as exc:
        logger.debug("[Fingerprint] overlay offset failed: %s", exc)
        report["pe"]["overlay_offset"] = None

    report["version"] = _version_info(pe)
    report["debug"] = _debug_info(pe)
    try:
        from core.authenticode import analyze as _authenticode_analyze

        report["signature"] = _authenticode_analyze(pe, path)
    except Exception as exc:
        logger.debug("[Fingerprint] authenticode analysis failed: %s", exc)
        report["signature"] = {"present": False, "error": str(exc)}
    report["strings"] = _interesting_strings(path)
    report["disasm"] = _disasm(pe)
    report["meta"]["generated"] = _iso_now()

    return report


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── TOML export ────────────────────────────────────────────────────────────

def _toml_str(value: Any) -> str:
    if value is None:
        return '""'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _toml_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return _toml_str(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return _toml_str(value)


def _toml_array(values: Any) -> str:
    if not values:
        return "[]"
    return "[ " + ", ".join(_toml_value(v) for v in values) + " ]"


def fingerprint_to_toml(report: Dict[str, Any]) -> str:
    lines: List[str] = []

    def section(name: str):
        lines.append(f"[{name}]")

    def kv(key: str, value: Any):
        if value is None:
            return
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")

    def array_kv(key: str, values: Any):
        if values is None:
            return
        lines.append(f"{_toml_key(key)} = {_toml_array(values)}")

    section("meta")
    kv("tool", report.get("meta", {}).get("tool", TOOL))
    kv("tool_version", report.get("meta", {}).get("tool_version", TOOL_VERSION))
    kv("generated", report.get("meta", {}).get("generated", _iso_now()))
    kv("source", report.get("source", "local file"))
    if report.get("error"):
        kv("error", report["error"])

    file_info = report.get("file", {})
    section("file")
    for key in ("name", "path", "size_bytes", "md5", "sha1", "sha256", "sha512", "blake2b256", "magic"):
        kv(key, file_info.get(key))

    fs = report.get("fs") or {}
    if fs:
        section("fs")
        for key in ("size_bytes", "modified", "changed", "accessed", "created",
                    "inode", "hard_links", "uid", "gid", "owner", "group", "mode"):
            kv(key, fs.get(key))

    process = report.get("process") or {}
    if process:
        section("process")
        for key in ("pid", "ppid", "name", "state", "started_at", "exe", "cwd",
                    "user", "uid", "gid", "threads", "rss_kb", "vms_kb",
                    "utime_seconds", "stime_seconds", "argv_redacted",
                    "exe_error", "modules_error", "error"):
            kv(key, process.get(key))
        array_kv("argv", process.get("argv"))
        array_kv("modules", process.get("modules"))

    pe = report.get("pe")
    if pe:
        section("pe")
        for key in (
            "arch",
            "subsystem",
            "entry_point_rva",
            "entry_point_va",
            "image_base",
            "number_of_sections",
            "size_of_image",
            "size_of_headers",
            "size_of_code",
            "checksum_stored",
            "checksum_computed",
            "checksum_valid",
            "linker_version",
            "dll_characteristics",
            "file_characteristics",
            "entropy",
            "imported_function_count",
            "exported_function_count",
            "is_dotnet",
            "overlay_offset",
            "timedatestamp_raw",
            "timedatestamp",
            "timedatestamp_note",
        ):
            kv(key, pe.get(key))
        array_kv("file_characteristic_names", pe.get("file_characteristic_names"))
        array_kv("dll_characteristic_names", pe.get("dll_characteristic_names"))
        array_kv("imported_dlls", pe.get("imported_dlls"))
        array_kv("exported_functions", pe.get("exported_functions"))
        for index, section_entry in enumerate(pe.get("sections") or []):
            lines.append("[[pe.sections]]")
            for key in ("name", "virtual_size", "raw_size", "entropy"):
                kv(key, section_entry.get(key))
            lines.append(f"flags = {_toml_array(section_entry.get('flags') or [])}")
        for resource in pe.get("resources") or []:
            lines.append("[[pe.resources]]")
            kv("type", resource.get("type"))
            kv("type_id", resource.get("type_id"))
            kv("entries", resource.get("entries"))
            kv("languages", resource.get("languages"))

    version = report.get("version") or {}
    if version:
        section("version_info")
        for key, value in version.items():
            kv(key, value)

    debug = report.get("debug") or {}
    if debug:
        section("debug")
        for key, value in debug.items():
            kv(key, value)

    signature = report.get("signature") or {}
    if signature:
        section("signature")
        for key, value in signature.items():
            if key in ("certificates", "integrity"):
                continue
            kv(key, value)
        integrity = signature.get("integrity") or {}
        if integrity:
            section("signature.integrity")
            for key, value in integrity.items():
                kv(key, value)
        for certificate in signature.get("certificates") or []:
            lines.append("[[signature.certificates]]")
            for key, value in certificate.items():
                kv(key, value)

    disasm = report.get("disasm") or []
    for insn in disasm:
        lines.append("[[disasm]]")
        for key, value in insn.items():
            kv(key, value)

    strings = report.get("strings") or []
    if strings:
        section("strings")
        lines.append("interesting = [")
        for s in strings:
            lines.append(f"  {_toml_str(s)},")
        lines.append("]")

    return "\n".join(lines) + "\n"


def fingerprint_to_text(report: Dict[str, Any]) -> List[str]:
    """Human-readable lines for the TUI report view."""
    out: List[str] = []
    file_info = report.get("file", {})

    def kv(key: str, value: Any, indent: int = 2):
        if value not in (None, "", []):
            out.append(f"{' ' * indent}{key}: {value}")

    out.append(f"━━ {file_info.get('name', '?')} ━━")
    kv("path", file_info.get("path"))
    kv("size", f"{file_info.get('size_bytes', 0):,} bytes")
    kv("md5", file_info.get("md5"))
    kv("sha1", file_info.get("sha1"))
    kv("sha256", file_info.get("sha256"))
    kv("sha512", file_info.get("sha512"))

    fs = report.get("fs") or {}
    if fs:
        out.append("── filesystem ──")
        kv("modified", fs.get("modified"))
        kv("created", fs.get("created"))
        kv("changed", fs.get("changed"))
        kv("owner", f"{fs.get('owner')}:{fs.get('group')}")
        kv("mode", fs.get("mode"))
        kv("inode", fs.get("inode"))

    process = report.get("process") or {}
    if process:
        out.append("── process ──")
        kv("pid", process.get("pid"))
        kv("ppid", process.get("ppid"))
        kv("name", process.get("name"))
        kv("state", process.get("state"))
        kv("started", process.get("started_at"))
        kv("user", process.get("user"))
        kv("exe", process.get("exe") or process.get("exe_error"))
        kv("cwd", process.get("cwd"))
        kv("threads", process.get("threads"))
        kv("rss", f"{process.get('rss_kb')} KB" if process.get("rss_kb") else None)
        if process.get("argv"):
            marker = "  (redacted)" if process.get("argv_redacted") else ""
            out.append(f"  argv: {' '.join(process['argv'])}{marker}")
        if process.get("modules"):
            kv("modules", f"{len(process['modules'])} mapped files (see TOML)")

    if report.get("error"):
        out.append(f"error: {report['error']}")
        return out

    pe = report.get("pe") or {}
    out.append("── PE layout ──")
    kv("arch", pe.get("arch"))
    kv("subsystem", pe.get("subsystem"))
    kv("entry (RVA)", pe.get("entry_point_rva"))
    kv("entry (VA)", pe.get("entry_point_va"))
    kv("image base", pe.get("image_base"))
    kv("sections", pe.get("number_of_sections"))
    kv("size of image", f"{pe.get('size_of_image', 0):,}")
    kv("checksum", pe.get("checksum_stored"))
    kv("checksum valid", pe.get("checksum_valid"))
    kv("timedatestamp", f"{pe.get('timedatestamp')} ({pe.get('timedatestamp_note') or 'ok'})" if pe.get("timedatestamp") else None)
    kv("linker", pe.get("linker_version"))
    kv("dll characteristics", ", ".join(pe.get("dll_characteristic_names") or []))
    kv("file characteristics", ", ".join(pe.get("file_characteristic_names") or []))
    kv("entropy", pe.get("entropy"))
    kv(".NET", pe.get("is_dotnet"))
    kv("overlay", f"0x{pe['overlay_offset']:x}" if pe.get("overlay_offset") else None)
    if pe.get("imported_dlls"):
        out.append(f"  dlls: {', '.join(pe['imported_dlls'])}")
    kv("imported functions", pe.get("imported_function_count"))
    kv("exported functions", pe.get("exported_function_count"))
    resources = pe.get("resources") or []
    if resources:
        out.append("  resources: " + ", ".join(f"{r.get('type')}×{r.get('entries')}" for r in resources))
    for index, section_entry in enumerate(pe.get("sections") or []):
        flags = ", ".join(section_entry.get("flags") or [])
        out.append(
            f"  [{section_entry.get('name')}] vsize={section_entry.get('virtual_size')} "
            f"raw={section_entry.get('raw_size')} entropy={section_entry.get('entropy')} {flags}"
        )

    version = report.get("version") or {}
    if version:
        out.append("── version info ──")
        for key, value in version.items():
            kv(key, value)

    debug = report.get("debug") or {}
    if debug:
        out.append("── debug ──")
        kv("pdb", debug.get("pdb_path"))
        kv("guid", debug.get("guid"))
        kv("age", debug.get("age"))

    signature = report.get("signature") or {}
    if signature:
        out.append("── signature ──")
        kv("present", signature.get("present"))
        kv("cert table", f"{signature.get('cert_table_size')} bytes" if signature.get("cert_table_size") else None)
        kv("signer", signature.get("signer_subject"))
        kv("fingerprint", signature.get("signer_fingerprint_sha256"))
        kv("chain verified", signature.get("chain_verified"))
        kv("chain detail", signature.get("chain_detail"))
        integrity = signature.get("integrity") or {}
        if integrity:
            kv("integrity", (
                f"algorithm {integrity.get('algorithm')} — signed byte range intact"
                if integrity.get("verified")
                else "no computed digest found in the signature (modified after signing, or an unexpected algorithm)"
            ))
        for certificate in signature.get("certificates") or []:
            out.append(
                f"  cert: {certificate.get('subject', '?')} — issuer {certificate.get('issuer', '?')} "
                f"— notAfter {certificate.get('not_after', '?')}"
            )

    disasm = report.get("disasm") or []
    if disasm:
        out.append("── disassembly (entry point) ──")
        for insn in disasm:
            out.append(f"  {insn['va']}  {insn['bytes']:<24}  {insn['op']}")

    strings = report.get("strings") or []
    if strings:
        out.append("── interesting strings ──")
        for s in strings:
            out.append(f"  {s}")

    out.append(f"── generated {report.get('meta', {}).get('generated', '')} · source: {report.get('source', '')} ──")
    return out
