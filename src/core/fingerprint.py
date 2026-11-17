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
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(), "sha256": sha256.hexdigest()}


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


def _signature_info(pe, path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"present": False}
    try:
        directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4]
        size = directory.Size
        if size:
            out["present"] = True
            out["cert_table_size"] = size
            out["cert_table_offset"] = directory.VirtualAddress
            with path.open("rb") as handle:
                handle.seek(directory.VirtualAddress)
                blob = handle.read(min(size, 512 * 1024))
            subject = _find_certificate_subject(blob)
            if subject:
                out["signer_subject"] = subject
            out["size_bytes"] = len(blob)
            try:
                if pe.verify_signature():
                    out["verified"] = True
            except Exception:
                out["verified"] = False
    except Exception as exc:
        logger.debug("[Fingerprint] signature check failed: %s", exc)
    return out


_CERT_SUBJECT_RE = re.compile(rb"CN=([\w\.\- &,]{2,80})")


def _find_certificate_subject(blob: bytes) -> Optional[str]:
    # naive but effective: the signing cert's CN attribute is plaintext inside the ASN.1
    match = _CERT_SUBJECT_RE.search(blob)
    if not match:
        return None
    try:
        return match.group(1).decode("utf-8", "ignore").strip() or None
    except Exception:
        return None


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

def fingerprint_file(path: str | os.PathLike, source: str = "local file") -> Dict[str, Any]:
    path = Path(path)
    size = path.stat().st_size
    hashes = _hashes(path)

    report: Dict[str, Any] = {
        "file": {
            "name": path.name,
            "path": str(path),
            "size_bytes": size,
            **hashes,
            "magic": "MZ",
        },
        "source": source,
        "meta": {"tool": TOOL, "tool_version": TOOL_VERSION},
    }

    try:
        import pefile

        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY_RESOURCE,
                    pefile.DIRECTORY_ENTRY_DEBUG,
                    pefile.DIRECTORY_ENTRY_IMPORT,
                    pefile.DIRECTORY_ENTRY_EXPORT,
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
        "checksum": f"0x{oh.CheckSum:x}" if getattr(oh, "CheckSum", None) is not None else None,
        "linker_version": f"{oh.MajorLinkerVersion}.{oh.MinorLinkerVersion}",
        "dll_characteristics": oh.DllCharacteristics,
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
    report["signature"] = _signature_info(pe, path)
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
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _toml_str(value)


def fingerprint_to_toml(report: Dict[str, Any]) -> str:
    lines: List[str] = []

    def section(name: str):
        lines.append(f"[{name}]")

    def kv(key: str, value: Any):
        if value is None:
            return
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")

    section("meta")
    kv("tool", report.get("meta", {}).get("tool", TOOL))
    kv("tool_version", report.get("meta", {}).get("tool_version", TOOL_VERSION))
    kv("generated", report.get("meta", {}).get("generated", _iso_now()))
    kv("source", report.get("source", "local file"))
    if report.get("error"):
        kv("error", report["error"])

    file_info = report.get("file", {})
    section("file")
    for key in ("name", "path", "size_bytes", "md5", "sha1", "sha256", "magic"):
        kv(key, file_info.get(key))

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
            "checksum",
            "linker_version",
            "dll_characteristics",
            "imported_function_count",
            "exported_function_count",
            "is_dotnet",
            "overlay_offset",
        ):
            kv(key, pe.get(key))
        kv("imported_dlls", "; ".join(pe.get("imported_dlls") or []))
        kv("exported_functions", "; ".join(pe.get("exported_functions") or []))
        for index, section_entry in enumerate(pe.get("sections") or []):
            section(f"pe.sections.{index}")
            for key in ("name", "virtual_size", "raw_size", "entropy"):
                kv(key, section_entry.get(key))
            flags = section_entry.get("flags") or []
            lines.append(f"flags = [ {', '.join(_toml_str(f) for f in flags)} ]")

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
            kv(key, value)

    disasm = report.get("disasm") or []
    if disasm:
        section("disasm")
        for index, insn in enumerate(disasm):
            lines.append(f"# {index}")
            section(f"disasm.{index}")
            for key, value in insn.items():
                kv(key, value)

    strings = report.get("strings") or []
    if strings:
        section("strings")
        lines.append(
            "interesting = [\n"
            + "\n".join(f"  {_toml_str(s)}," for s in strings)
            + "\n]"
        )

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
    kv("checksum", pe.get("checksum"))
    kv("linker", pe.get("linker_version"))
    kv(".NET", pe.get("is_dotnet"))
    kv("overlay", f"0x{pe['overlay_offset']:x}" if pe.get("overlay_offset") else None)
    if pe.get("imported_dlls"):
        out.append(f"  dlls: {', '.join(pe['imported_dlls'])}")
    kv("imported functions", pe.get("imported_function_count"))
    kv("exported functions", pe.get("exported_function_count"))
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
        kv("verified", signature.get("verified"))

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
