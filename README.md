<p align="center">
  <img src="exe_fingerprinter_logo.png" width="128" height="128" alt="EXE-fingerprinter logo" />
</p>

<h1 align="center">paws-fingerprinter</h1>

<p align="center">
  <strong>Fingerprint any Windows .exe — from a game, or a running process.</strong><br/>
  A terminal-only tool for Steam games, depot keys and forensic reports. No Qt, no launcher.
</p>

<p align="center">
  <a href="https://github.com/one-eyed-queen/paws-fingerprinter/actions"><img alt="Build" src="https://github.com/one-eyed-queen/paws-fingerprinter/actions/workflows/build-appimage.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-GPL--3.0-blue.svg"></a>
</p>

---

### What it does

1. **Search** an AppID (`620`) or a few letters of a name — merged from Steam Store, SteamDB, Hubcap and your private API. Exact AppID first.
2. **Fingerprint a game**: a manifest-only scan of the base game's Windows depots (this downloads *no* game content) lists every `.exe` with its size and depot — you **choose** which ones to analyze. Only those are downloaded to a temp folder and unpacked into a full forensic report.
3. **Download .exe files** (nothing else) into `%APPDATA%/exe-pawprint/<appid>`, with per-file selection.
4. **Fingerprint any file or running process**: hashes, PE layout, imports, version info, CodeView GUID, strings, disassembly — plus process name, PID, PPID, start time, RSS, argv (**credentials redacted**) and mapped modules when a process is live.

The interface is a paws-style terminal UI (sixel graphics where the terminal supports it, clean text everywhere else). SFW only. **It never adds or removes games.**

### Fingerprint contents

Every report (TOML or screen) carries, when the binary contains it:

- `file` — name, size, path, **MD5 / SHA-1 / SHA-256 / SHA-512 / BLAKE2b-256**, magic
- `fs` — modification/creation/change/access times, owner, mode, inode, hard links
- `process` — pid, ppid, name, state, start time, user, RSS/VSZ, threads, utime/stime, argv (redacted), mapped modules (when fingerprinting a running process)
- `pe` — arch, subsystem, entry point (RVA/VA), image base, sections with entropy, **checksum verification** (`absent`/`valid`/`mismatch`), timedatestamp sanity notes, file/DLL characteristic names, **overall entropy**, **resource inventory** (icons, version info, manifest — with type, entry count and languages), imported DLLs and function counts, exports
- `version_info` — FileVersion, CompanyName, ProductName, LegalCopyright, …
- `debug` — CodeView **PDB GUID**, PDB path, age
- `signature` — **Authenticode**: certificate(s) extracted via OpenSSL (subject, issuer, serial, validity, SHA-256 fingerprint), **chain verification** against the system CA bundle, and a **signed byte-range digest integrity check** — a single flipped byte anywhere in the signed range is detected
- `disasm` — disassembly at the entry point (x86/x64)
- `strings` — interesting strings (URLs, emails, registry paths, file paths, environment variables)

TOML output is valid TOML and parses with `tomllib` / `toml`. Copy it to the clipboard or export to a file from the report screen.

---

## Install

### From source (Linux/macOS)

One command installs the app and puts `fingerprint` (plus `fp`) on your PATH:

```bash
git clone https://github.com/one-eyed-queen/paws-fingerprinter.git
cd paws-fingerprinter
./install.sh
```

It creates a private venv in `~/.local/share/paws-fingerprinter/venv`, links `~/.local/bin/fingerprint`, adds an app-menu entry ("EXE Fingerprinter", search `fingerprint`) and icons. Requirements: **Python 3.10+**, **openssl** for certificate chain checks. `./uninstall.sh` removes it. Running `./install.sh` again updates.

Windows:

```powershell
git clone https://github.com/one-eyed-queen/paws-fingerprinter.git
cd paws-fingerprinter
powershell -ExecutionPolicy Bypass -File install.ps1
```

Same idea: venv in `%LOCALAPPDATA%\paws-fingerprinter`, `fingerprint` shim on your user PATH, **EXE Fingerprinter** in the Start Menu (search `fingerprint`). `install.ps1 -Desktop` adds a desktop shortcut; `install.ps1 -Uninstall` removes everything. Best in Windows Terminal; cmd and the old console work too.

### Manual install

```bash
git clone https://github.com/one-eyed-queen/paws-fingerprinter.git
cd paws-fingerprinter
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 src/main.py
```

### .NET for DepotDownloader

The .exe download/fingerprint pipeline uses a bundled **DepotDownloaderMod** (`net8.0`). Install the **.NET 8 Desktop Runtime** (or newer) from <https://dotnet.microsoft.com/download> — or point the app at a working dotnet via the `DOTNET` environment variable.

### Optional pickers and graphics

- **File/folder pickers** on Linux use `zenity` (or `kdialog`); on Windows/macOS a native dialog is used. Install `zenity` or `kdialog` if file picking does nothing.
- **Sixel graphics** need kitty, Ghostty or WezTerm (the app falls back to a text interface elsewhere). A paws-style background underlay renders only on those terminals.

### Non-interactive use

```bash
python3 src/main.py --api --appid 620             # print the .exe list
python3 src/main.py --api --appid 620 --download  # also download the executables
```

---

## Hubcap — how to get a key

The app **never ships depot keys**. To fetch `.exe` lists or download them you need Steam depot access, which comes from one of:

1. **Your own Hubcap account** (third-party manifest service):
   - Create an account at **https://hubcapmanifest.com**
   - Copy your API key from your account page
   - In the app: **Settings → Hubcap API key → paste → save**
   - Hubcap is rate-limited and metered — your usage comes out of your own Hubcap quota. Keys stay on your machine; they are never committed to this repository.
2. **Your private EXE-fingerprinter API** (see below) — it serves depot keys/GIDs to authorized accounts; if you have one, you don't need Hubcap.

If neither is configured, search and the report viewer still work; only depot-key-backed features fail — they will say exactly which source is missing and why.

---

## Private API — friends only

<https://exe-fingerprinter.vercel.app> is the author's **private** service:

- username + password, **HWID-locked to the first device** on sign-in, expiry dates on accounts,
- serves depot keys and manifest GIDs so you never touch Hubcap,
- accounts are issued in person to friends of the author — there is no public sign-up.

If you have an account: **Settings → EXE-fingerprinter API base / username / password** and sign in once. If you don't, use your own Hubcap key. Sign-in is optional; everything except depot-key delivery behaves the same.

---

## Crowdsourced fingerprints — the `fingerprints` bank

<https://github.com/one-eyed-queen/fingerprints> is a community bank of fingerprint TOMLs, organized as `<binary-name>/<sha256>.toml` (+ per-directory `index.json`).

Direct fetch if you know the file's SHA-256:

```bash
curl -L https://raw.githubusercontent.com/one-eyed-queen/fingerprints/main/<name>/<sha256>.toml
```

Contribute: run the fingerprint in the app → **Export TOML** → open a PR against the bank with your file at `<name>/<sha256>.toml` and update `index.json`. **Strip absolute paths (`path`, `cwd`) and any personal data first** — TOMLs may be public. The bank stores metadata only, never binaries.

---

## Steam, legality, disclaimers

- Use this with games you own. The tool does not crack, bypass or circumvent DRM; it automates a depot workflow using keys you are entitled to (your own Hubcap account or your own API account).
- Never commit keys, tokens or account data to this repo (it is public).
- Not affiliated with Valve Corporation or Steam™. "Steam" and "SteamDB" belong to their respective owners.

## Credits

- Terminal UI: visual language adapted from **paws** (same author, used with permission) — SFW assets only.
- Fork lineage: **ASSella** → stripped to a .exe-only downloader/fingerprinter.
- [DepotDownloaderMod](https://github.com/SteamRE/DepotDownloader) does the actual depot work.

## License

GPL-3.0 — see [LICENSE](LICENSE). Copyright (C) the paws-fingerprinter contributors.
