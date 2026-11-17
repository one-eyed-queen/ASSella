<p align="center">
  <img src="exe_fingerprinter_logo.png" width="128" height="128" alt="EXE-fingerprinter Logo" />
</p>

<h1 align="center">EXE-fingerprinter</h1>

<p align="center">
  <strong>Fingerprint a Steam game. Download just the .exe files.</strong>
</p>

<p align="center">
  A small terminal app that finds the AppID you want and pulls only its Windows executables — nothing else. Built in Python. No Qt, no game launcher.
</p>

<p align="center">
  <a href="https://github.com/one-eyed-queen/ASSella/actions/workflows/build-appimage.yml"><img src="https://github.com/one-eyed-queen/ASSella/actions/workflows/build-appimage.yml/badge.svg" alt="Build Executable Downloader"></a>
  <a href="https://github.com/one-eyed-queen/ASSella/releases"><img src="https://img.shields.io/github/v/release/one-eyed-queen/ASSella?color=orange" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue.svg" alt="License"></a>
</p>

---

### What it does

1. Search by AppID (`620`) or a few letters of a name (`portal`) — merged from Steam Store, SteamDB, Hubcap and your private API, exact AppID first.
2. Fingerprint the base game's Windows depots via the steamcmd API / Steam PICS, list every `.exe` with a DepotDownloader `-manifest-only` dump.
3. Pick the files you want. Only those are downloaded, by `-filelist`, into `%APPDATA%\exe-pawprint\<appid>`.

The interface is a paws-style TUI. Sixel graphics are used where the terminal supports them; everywhere else it draws a clean text view. Colour-blind mode is built in. SFW assets only.

**Never adds or removes games.** This is the download side of a depot workflow, not a library manager.

### Quick start

```bash
git clone https://github.com/one-eyed-queen/ASSella.git
cd ASSella
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 src/main.py
```

Or, non-interactively:

```bash
python3 src/main.py --api --appid 620            # fingerprint: prints the .exe list
python3 src/main.py --api --appid 620 --download # also download the executables
```

### Settings

Open **Settings → Private API & Appearance** in the TUI:

| Setting | What it's for |
| --- | --- |
| Hubcap API key | Your Hubcap account key (used to fetch the manifest bundle for the fingerprint). |
| API base URL | Your EXE-fingerprinter API (`https://exe-fingerprinter.vercel.app`). |
| API username / password | Sign in once; the account HWID-locks to this device, with an expiry date if set. |

Depot-key sources, best to best: local key database → your private API → Hubcap. The Hubcap key stays server-side; the API serves keys to you.

### Download location

`%APPDATA%\exe-pawprint\<appid>` on Windows, `~/.local/share/exe-pawprint/<appid>` on Linux, `~/Library/Application Support/exe-pawprint/<appid>` on macOS. Override with `--dest`.

### License

GPL-3.0. Unofficial, not affiliated with Valve Corporation or Steam.
