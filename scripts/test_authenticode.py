#!/usr/bin/env python3
"""Test the Authenticode analysis against a synthetic signed binary.

No real signed executable is available on this machine, so the test builds
one: it takes DepotDownloader.dll, sets its security directory entry, signs
the signed byte range with a throwaway CA created on the spot (openssl cms),
and wraps the result in a WIN_CERTIFICATE blob exactly the way a linker
would. The whole analysis then runs against that file — no network, no
system trust store needed (the negative chain check uses the real store; the
positive one patches the CA bundle to the throwaway CA).

    python3 scripts/test_authenticode.py
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

OPENSSL = shutil.which("openssl")
FIXTURE = SRC_DIR / "deps" / "DepotDownloader.dll"

failures = []
checks = 0


def check(condition, name):
    global checks
    checks += 1
    print(("  [ok] " if condition else "  [FAIL] ") + name)
    if not condition:
        failures.append(name)


def _run(args):
    subprocess.run(args, check=True, capture_output=True)


def make_certs(work: Path):
    _run([
        OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(work / "ca.key"), "-out", str(work / "ca.pem"),
        "-days", "3650", "-subj", "/CN=EXE-fingerprinter Test CA",
    ])
    _run([
        OPENSSL, "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(work / "leaf.key"), "-out", str(work / "leaf.csr"),
        "-subj", "/CN=EXE-fingerprinter Test Signer",
    ])
    _run([
        OPENSSL, "x509", "-req", "-in", str(work / "leaf.csr"),
        "-CA", str(work / "ca.pem"), "-CAkey", str(work / "ca.key"),
        "-CAcreateserial", "-out", str(work / "leaf.pem"), "-days", "3650",
    ])


def main() -> int:
    if OPENSSL is None:
        print("SKIP: openssl not found — no authenticode checks possible")
        return 0
    if not FIXTURE.exists():
        print(f"SKIP: fixture missing: {FIXTURE}")
        return 0

    import pefile

    from core import authenticode

    work = Path(tempfile.mkdtemp(prefix="exefp-sign-test-"))
    try:
        data = FIXTURE.read_bytes()
        pe = pefile.PE(str(FIXTURE), fast_load=True)
        e_lfanew = pe.DOS_HEADER.e_lfanew
        oh_start = e_lfanew + 24
        magic = pe.OPTIONAL_HEADER.Magic
        dirs_start = oh_start + (112 if magic == 0x20B else 96)
        security_dir = dirs_start + 4 * 8  # IMAGE_DIRECTORY_ENTRY_SECURITY = 4
        checksum_offset = oh_start + 64
        cert_offset = (len(data) + 7) & ~7

        # pass 1: signed-range digest input — security entry pointing at EOF, size irrelevant
        modified = bytearray(data + b"\x00" * (cert_offset - len(data)))
        struct.pack_into("<I", modified, security_dir, cert_offset)
        struct.pack_into("<I", modified, security_dir + 4, 0)
        signed_range = authenticode._signed_range(
            bytes(modified),
            checksum_offset=checksum_offset,
            security_dir_offset=security_dir,
            cert_offset=cert_offset,
        )
        range_path = work / "range.bin"
        range_path.write_bytes(signed_range)

        make_certs(work)
        cms_der = work / "cms.der"
        _run([
            OPENSSL, "cms", "-sign", "-binary", "-in", str(range_path),
            "-outform", "DER", "-out", str(cms_der),
            "-signer", str(work / "leaf.pem"), "-inkey", str(work / "leaf.key"),
            "-certfile", str(work / "ca.pem"),
        ])

        # pass 2: the final file — set the real certificate table size
        cms_bytes = cms_der.read_bytes()
        blob = struct.pack("<IHH", 8 + len(cms_bytes), 0x0200, 0x0002) + cms_bytes
        blob = blob + b"\x00" * ((8 - len(blob) % 8) % 8)
        final = bytes(modified) + blob
        # patch only the Size field of the security directory entry (VA must stay cert_offset)
        final = final[:security_dir + 4] + struct.pack("<I", len(blob)) + final[security_dir + 8:]
        synth = work / "synth.dll"
        synth.write_bytes(final)

        pe_final = pefile.PE(str(synth), fast_load=True)
        result = authenticode.analyze(pe_final, synth)

        check(result.get("present") is True, "certificate table detected")
        check(result.get("pkcs7_count") == 1, "one PKCS#7 signature extracted")
        integrity = result.get("integrity") or {}
        check(integrity.get("sha256_found") is True, "SHA-256 digest of the signed range is intact")
        check(integrity.get("sha1_found") is False, "SHA-1 not (falsely) found")
        check(integrity.get("algorithm") == "sha256", "algorithm reported as sha256")
        check(integrity.get("verified") is True, "integrity verified")
        certificates = result.get("certificates") or []
        subjects = [c.get("subject", "") for c in certificates]
        check(any("EXE-fingerprinter Test Signer" in s for s in subjects), "signer certificate extracted via openssl")
        check(any("EXE-fingerprinter Test CA" in c.get("issuer", "") or "EXE-fingerprinter Test CA" in c.get("subject", "") for c in certificates),
              "CA certificate extracted too")
        check(result.get("chain_verified") is False, "chain against the system store fails (self-signed test CA)")

        original_bundles = authenticode._CA_BUNDLES
        try:
            authenticode._CA_BUNDLES = (str(work / "ca.pem"),)
            result2 = authenticode.analyze(pefile.PE(str(synth), fast_load=True), synth)
            check(result2.get("chain_verified") is True, "chain against the issuing CA verifies")
        finally:
            authenticode._CA_BUNDLES = original_bundles

        # a single flipped byte inside the signed range must be caught
        corrupted = final[:10] + bytes([final[10] ^ 0x01]) + final[11:]
        corrupt_path = work / "corrupt.dll"
        corrupt_path.write_bytes(corrupted)
        result3 = authenticode.analyze(pefile.PE(str(corrupt_path), fast_load=True), corrupt_path)
        check((result3.get("integrity") or {}).get("verified") is False, "a single flipped byte is detected")

        from core.fingerprint import fingerprint_file, fingerprint_to_toml

        report = fingerprint_file(str(synth))
        toml = fingerprint_to_toml(report)
        try:
            import tomllib

            parsed = tomllib.loads(toml)
            check(True, "TOML parses")
            check("certificates" in (parsed.get("signature") or {}), "certificates in TOML")
            check((parsed.get("signature", {}).get("integrity") or {}).get("sha256_found") is True,
                  "integrity in TOML")
        except Exception as exc:
            check(False, f"TOML parse — {exc}")
            check(False, "TOML round-trip")

        print()
        if failures:
            print(f"FAILED ({len(failures)} of {checks}):")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print(f"ALL AUTHENTICODE TESTS PASSED ({checks} checks)")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
