"""Authenticode analysis for PE files.

Everything here is *static* and local: the certificate table is carved out of
the PE with pefile, the PKCS#7 content is decoded with the system ``openssl``
CLI when it is available, and the integrity of the signed byte range is
verified with a digest search (the authenticode digest is embedded verbatim in
the PKCS#7 as an octet string, so finding it proves the signed range is
intact — provided the signer used MD5/SHA-1/SHA-256, which is what
certificates of the last decade use).

Honesty rules:

- if openssl is missing, certificate details are reported as unavailable —
  never invented;
- the chain check reports exactly what openssl said, including failures;
- the integrity check reports which algorithm matched, and says so plainly
  when nothing matched (modified file, or an unexpected algorithm).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_OPENSSL_TIMEOUT = 10.0

_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu/Arch
    "/etc/pki/tls/certs/ca-bundle.crt",    # Fedora/RHEL
    "/etc/ssl/cert.pem",                   # Alpine/macOS
    "/usr/local/share/ca-certificates/ca-certificates.crt",
)

_CERT_SPLIT_RE = re.compile(r"-----BEGIN CERTIFICATE-----[^-]+-----END CERTIFICATE-----")


def _openssl() -> Optional[str]:
    return shutil.which("openssl")


def _run_openssl(args: List[str], input_data: bytes) -> Tuple[int, bytes]:
    try:
        proc = subprocess.run(
            ["openssl", *args],
            input=input_data,
            capture_output=True,
            timeout=_OPENSSL_TIMEOUT,
            check=False,
        )
        return proc.returncode, proc.stdout + proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc).encode("utf-8", "ignore")


def _pem_blocks(text: bytes) -> List[bytes]:
    return [m.group(0).encode() for m in _CERT_SPLIT_RE.finditer(text.decode("utf-8", "ignore"))]


def _cert_details(pem: bytes) -> Dict[str, str]:
    info: Dict[str, str] = {}
    rc, out = _run_openssl(
        ["x509", "-noout", "-subject", "-issuer", "-serial", "-dates", "-fingerprint", "-sha256"],
        pem,
    )
    if rc != 0:
        return info
    for line in out.decode("utf-8", "ignore").splitlines():
        line = line.strip()
        if line.startswith("subject="):
            info["subject"] = line[len("subject="):].strip()
        elif line.startswith("issuer="):
            info["issuer"] = line[len("issuer="):].strip()
        elif line.startswith("serial="):
            info["serial"] = line[len("serial="):].strip()
        elif line.startswith("notBefore="):
            info["not_before"] = line[len("notBefore="):].strip()
        elif line.startswith("notAfter="):
            info["not_after"] = line[len("notAfter="):].strip()
        elif "Fingerprint=" in line:
            info["sha256"] = line.split("Fingerprint=", 1)[1].strip()
    return info


def _parse_pkcs7(pkcs7_der: bytes) -> Tuple[List[Dict[str, str]], List[bytes], str]:
    rc, out = _run_openssl(
        ["pkcs7", "-inform", "DER", "-print_certs"],
        pkcs7_der,
    )
    if rc != 0:
        return [], [], out.decode("utf-8", "ignore").strip()
    pem_blocks = _pem_blocks(out)
    # openssl prints the signing certificate first, then the chain.
    certs = [_cert_details(block) for block in pem_blocks]
    certs = [c for c in certs if c]
    return certs, pem_blocks, ""


def _verify_chain(certs_pem: List[bytes]) -> Tuple[bool, str]:
    if not certs_pem:
        return False, "no certificates in the signature"
    ca_bundle = next((p for p in _CA_BUNDLES if os.path.exists(p)), None)
    if ca_bundle is None:
        return False, "no CA bundle found (openssl could not build a chain)"
    leaf = certs_pem[0]
    intermediates = certs_pem[1:]
    with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as leaf_handle:
        leaf_handle.write(leaf)
        leaf_path = leaf_handle.name
    untrusted_path = None
    try:
        args = ["verify", "-CAfile", ca_bundle, "-no_check_time"]
        if intermediates:
            with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as handle:
                for block in intermediates:
                    handle.write(block)
                untrusted_path = handle.name
            args += ["-untrusted", untrusted_path]
        args.append(leaf_path)
        rc, out = _run_openssl(args, b"")
        detail = out.decode("utf-8", "ignore").strip().splitlines()
        return rc == 0, (detail[-1] if detail else "openssl verify produced no output")
    finally:
        for path in (leaf_path, untrusted_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _signed_range(
    data: bytes,
    *,
    checksum_offset: int,
    security_dir_offset: int,
    cert_offset: int,
) -> bytes:
    """The byte range an Authenticode digest covers.

    The file truncated at the certificate table, with the CheckSum field and
    the security data-directory entry zeroed. The certificate blob itself and
    anything appended after it are excluded entirely.
    """
    end = min(max(cert_offset, 0), len(data))
    buf = bytearray(data[:end])
    for start, width in ((checksum_offset, 4), (security_dir_offset, 8)):
        if 0 <= start and start + width <= len(buf):
            buf[start:start + width] = b"\x00" * width
    return bytes(buf)


def _digest_integrity(data: bytes, offsets: Dict[str, int]) -> Dict[str, Any]:
    signed = _signed_range(
        data,
        checksum_offset=offsets["checksum_offset"],
        security_dir_offset=offsets["security_dir_offset"],
        cert_offset=offsets["cert_offset"],
    )
    cert_blob = data[offsets["cert_offset"]: offsets["cert_offset"] + offsets["cert_table_size"]]
    digests = {
        "md5": hashlib.md5(signed).digest(),
        "sha1": hashlib.sha1(signed).digest(),
        "sha256": hashlib.sha256(signed).digest(),
        "sha512": hashlib.sha512(signed).digest(),
    }
    found = {algo: (digest in cert_blob) for algo, digest in digests.items()}
    matched = next((algo for algo in ("sha256", "sha1", "md5", "sha512") if found[algo]), "")
    return {
        "md5_found": found["md5"],
        "sha1_found": found["sha1"],
        "sha256_found": found["sha256"],
        "sha512_found": found["sha512"],
        "verified": bool(matched),
        "algorithm": matched,
        "method": (
            "digest of the signed byte range located inside the PKCS#7 signature blob; "
            "the certificate table itself is excluded from the digest by the Authenticode spec"
        ),
    }


def _pe_offsets(pe) -> Optional[Dict[str, int]]:
    try:
        e_lfanew = pe.DOS_HEADER.e_lfanew
        coff_start = e_lfanew + 4
        oh_start = coff_start + 20
        magic = pe.OPTIONAL_HEADER.Magic
        # PE32+ has 112 bytes of fixed optional-header fields before the data
        # directories; PE32 has 96. CheckSum sits at +64 in both.
        data_dirs_start = oh_start + (112 if magic == 0x20B else 96)
        return {
            "oh_start": int(oh_start),
            "checksum_offset": int(oh_start + 64),
            "security_dir_offset": int(data_dirs_start + 4 * 8),
        }
    except Exception as exc:
        logger.debug("[Authenticode] could not compute PE offsets: %s", exc)
        return None


def _win_certificates(blob: bytes) -> List[bytes]:
    out = []
    pos = 0
    while pos + 8 <= len(blob):
        length = int.from_bytes(blob[pos:pos + 4], "little")
        if length < 8 or pos + length > len(blob):
            break
        revision = int.from_bytes(blob[pos + 4:pos + 6], "little")
        cert_type = int.from_bytes(blob[pos + 6:pos + 8], "little")
        payload = blob[pos + 8:pos + length]
        if cert_type == 0x0002 and revision in (0x0100, 0x0200) and payload:
            out.append(payload)
        pos += length + ((8 - length % 8) % 8)  # certs are 8-byte aligned
    return out


def analyze(pe, path: Path) -> Dict[str, Any]:
    """Full Authenticode analysis. Never raises; reports what it could not do."""
    out: Dict[str, Any] = {"present": False}
    try:
        directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4]
        size = int(directory.Size or 0)
        offset = int(directory.VirtualAddress or 0)
        if not size or not offset:
            return out
        out["present"] = True
        out["cert_table_offset"] = offset
        out["cert_table_size"] = size
    except Exception as exc:
        logger.debug("[Authenticode] no security directory: %s", exc)
        return out

    try:
        data = path.read_bytes()
    except OSError as exc:
        out["error"] = f"could not read the file: {exc}"
        return out

    cert_blob = data[offset: offset + size]
    out["cert_table_bytes"] = len(cert_blob)

    signatures = _win_certificates(cert_blob)
    out["pkcs7_count"] = len(signatures)

    openssl_bin = _openssl()
    out["openssl"] = bool(openssl_bin)

    offsets = _pe_offsets(pe)
    if offsets is not None:
        out["integrity"] = _digest_integrity(
            data,
            {
                **offsets,
                "cert_offset": offset,
                "cert_table_size": size,
            },
        )

    if not signatures:
        out["error"] = "certificate table present but no WIN_CERTIFICATE with PKCS#7 content was found"
        return out

    pkcs7 = signatures[0]
    if openssl_bin:
        certs, pem_blocks, err = _parse_pkcs7(pkcs7)
        if certs:
            out["certificates"] = certs
            out["signer_subject"] = certs[0].get("subject", "")
            out["signer_fingerprint_sha256"] = certs[0].get("sha256", "")
            out["chain_verified"], detail = _verify_chain(pem_blocks)
            out["chain_detail"] = detail
        else:
            out["error"] = f"openssl could not parse the signature: {err or 'unknown error'}"
    else:
        out["certificates_note"] = "openssl not found — certificate details unavailable, digest integrity still reported"

    return out
