# AGENTS.md — EXE-fingerprinter agent rules

EXE-fingerprinter is a **terminal-only, .exe-only** Steam downloader. Everything that used to be a library manager (Qt GUI, SLSsteam/ACCELA integration, Goldberg, Steamless, SLScheevo, plugins, library views, update checker) was deleted. If you are about to add any of that back, stop.

## Core rules

- **No GUI, no Qt.** The only interface is the TUI (`src/tui/`). Nothing in `src/` may import PyQt6/PySide6.
- **.exe only.** The tool downloads Windows executables for the base game (DLC skipped). Do not add "install the game" flows, library views, update checkers or repair/verify flows.
- **Depot keys, best to best:** local key database (`core/depot_key_manager.py`) → the user's private API (`core/exe_fingerprinter_api.py`) → Hubcap (`core/morrenus_api.py`). Manifest GIDs always come from the steamcmd public API (PICS fallback).
- **Hubcap key stays server-side.** The app never calls your private database directly; the API serves keys. The app ships with no hardcoded Hubcap key.
- **Settings** live in a JSON store (`~/.config/EXE-fingerprinter/settings.json`); legacy QSettings INI is migrated once at startup. Never store secrets in the repo.
- **Downloads** go to `<data root>/exe-pawprint/<appid>` — the app **never** writes Steam's ACF/manifest/`config.yaml` files and never triggers downloads for whole games. It only fetches `.exe` files by file list.
- **SFW assets only.** No game options, no theme marketplaces, no image downloads beyond the app logo.
- **Search** merges Steam Store, SteamDB, Hubcap and the private API; exact AppID matches must surface first.
- **Performance:** keep startup under a second. Network goes on worker threads; the TUI is raw-terminal (nothing logs to stdout — logs go to a file).

## Release & versioning

Releases are built by `.github/workflows/build-appimage.yml`:

1. Tag `vX.Y.Z` (stable), `vX.Y.Z-beta.N` (beta), `vX.Y.Z-canary.N` (canary).
2. The workflow builds `ExeFingerprinter.AppImage` with a bundled Python runtime, `.zsync` for delta updates and checksums, and a companion source archive. It stamps `src/version.py` (`APP_VERSION`, `BUILD_DATE`, `BUILD_TIMESTAMP`).
3. CI (`ci.yml`) runs ruff, compileall, the smoke import gate and `scripts/prerelease_check.py`.

## Safety

- `scripts/prerelease_check.py` is hermetic (no network); keep it that way.
- `scripts/smoke_import_check.py` must pass — it is the guard against deleted-module imports.
- Never commit secrets (Hubcap keys, API credentials). The panel token and the Hubcap key live in Vercel env vars only.
- The app must run on a clean Linux desktop with a stock Python 3 and the dependencies in `requirements.txt` — that is what CI validates.
