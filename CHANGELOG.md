# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed
- **The tool is now a TUI-only, .exe-only downloader.** The Qt desktop window and everything that served it was removed: library views, game details, plugins (SpaceBunny/Styx/Lobster), SLSsteam/ACCELA integration, Goldberg/GSE, Steamless, SLScheevo, achievements, the Workshop, DLC-only mode, the update checker, `config.yaml` management, MRC, tickets, and all PyQt6 dialogs.

### Added
- Pawws-style TUI (`--tui`, or just `python3 src/main.py`) with sixel graphics where the terminal supports them; rice/minimal auto-switch; colour-blind mode; SFW assets only.
- Non-interactive API mode: `python3 src/main.py --api --appid 620 [--download]` fingerprints a game and (optionally) downloads only its `.exe`s. Interactive API mode never adds/removes games; two choices: download, or the existing flow.
- **Private API** (`exe-fingerprinter.vercel.app`): username + password sign-in, HWID lock on first device, expiry dates, admin account creation via the Touka Kirishima panel. The public Hubcap remains supported; the Hubcap key never leaves the server side.
- Downloads go to `%APPDATA%/exe-pawprint/<appid>` (Linux/macOS equivalents under `~/.local/share/exe-pawprint` / `~/Library/Application Support/exe-pawprint`).

### Changed
- Depot-key resolution order: local key database → your private API (served from your depot-key repo, see `EXEFP_KEYS_URL`) → Hubcap. Manifest GIDs come from the steamcmd public API.
- Settings storage moved from QSettings INI (`~/.config/Tachibana Labs/ACCELA.conf`) to JSON (`%APPDATA%/EXE-fingerprinter/settings.json` on Windows, `~/.config/EXE-fingerprinter/settings.json` on Linux); a one-time migration preserves the old values.
- Search merged across Steam Store, SteamDB, Hubcap and the API, exact AppID match first.
- App data root is now `~/.local/share/EXE-fingerprinter` (was `ACCELA`); the folder is renamed in place on first run.


### Changed
- **Project renamed to Executable Downloader.** Every user-facing name, the window title, desktop entry, AppImage, installer and the AppImage self-updater now identify as "Executable Downloader" (`ExecutableDownloader.AppImage`, `exe_downloader.desktop`, `X-Executable Downloader-Token`). Historical changelog entries are intentionally left untouched. Internal code identifiers moved with the product name (`exe_downloader`, `get_exe_downloader_proxy_token()`); GitHub/Pages URLs to the original ASSella repo/docs were deliberately kept so existing links keep working. The Hubcap Wirecutter protocol constants (`ASSella_Hubcap_Bypass_v261`, `ASSella_Worker_Token_Salt_v261`) remain unchanged, as do `ACCELA` data paths, `accela_fetch_*` manifest bundles, the `X-Assella-Token` header and the `ACCELA.AppImage` compatibility symlink — all are protocol/compat identifiers, not branding.
- AppImage build workflow now derives the embedded `gh-releases-zsync` update string from `github.repository_owner` / `github.event.repository.name` instead of a hard-coded upstream name.
- `extract_semver`, the installer `_detect_branch_of_tag` channel detection and the dated-build-stamp regex now tolerate spaces in the codename (e.g. `20261109+Executable Downloader-3.1.0`).

### Added
- **Executable Downloader mode** (`src/core/exe_scan.py`, `src/ui/dialogs/exe_scan_runner.py`, `src/ui/dialogs/executable_chooser.py`): selecting a game with its `.lua` / depot keys / manifest bundle now scans the checked depots for Windows executables (`.exe` only, from `-manifest-only` dumps of each depot) and lets the user confirm exactly which executables to ship. Multiple executables across depots are shown as a grouped, filterable checklist with every entry pre-checked; per-depot `-filelist` hand-off to DepotDownloader limits the install to only the chosen executables. Wired into the multi-depot selector, single-depot selector, and the modern depot selector — every selected `.exe` is pre-checked by default and the install is skipped for depots with no selected executables.
- SteamDB App Parser (`src/steamdb_app_parser.py`, Python port of the GreasyFork 543010-steamdb-app-parser userscript): parses DLC and related/linked app tables from SteamDB app pages into SLSsteam-compatible `DlcData` / `AdditionalApps` YAML.
- SteamDB scraper (`src/core/steamdb_scraper.py`) additionally exposes `get_latest_build`, `get_base_game_depot`, `get_folder_structure`, `get_game_image`, `search_by_appid`, `search_by_name` and `parse_steamdb_dlc` for AppID/name-driven lookups.

## [3.1.0] - 2026-11-09

### Added
- **Christmas(beta) Seasonal Theme**:
  - Multi-layer 60 FPS `SnowEngine` particle physics with sinusoidal drift, bokeh depth-of-field blur on foreground flakes, and automatic sleep on minimize.
  - Knitted Nordic sweater pattern wallpaper with subtle gradient transparency and vignette.
  - Vector candy-cane switches, festive checkboxes, and radio buttons.
  - Animated download progress bar featuring Santa Claus, flying reindeer, stardust trail, and snowy village chimneys.
  - Organic dripping snow contour along top edge of main and dialog titlebars.
  - Dynamic synchronization of titlebar version label color with the active accent color and control icons.
  - Seasonal auto-activation window (December 20–26, once per year).
- **ZSync2 Delta-Update Fallback**:
  - Bundled `zsync2` under `AppDir/bin/zsync2-root/` with dynamic RUNPATH and bundled OpenSSL 1.1/gcrypt runtime, resolving TLS-dead Ubuntu stock zsync failures.
  - Added `-u <download_url>` override in `app_update_manager.py` to bypass legacy `gh-releases-zsync` descriptors in control files.
  - Hardened launcher wrapper with `LD_LIBRARY_PATH` prepending to guarantee bundled libraries load first.
  - Titlebar pulse indicator for available updates triggering fast delta ZSync updates.
- **Katamari Plugin Architecture & Dedicated Tab**:
  - Dedicated **Plugins** tab in Settings (active when AT0-M is enabled) with real-time two-way sync for `~/.config/SLSsteam/katamari.conf.lua`.
  - Bundled full Katamari suite: SpaceBunny, Lobster, Styx, Bakeneko, Pineapple, and Lettuce (enabled by default).
  - Centralized foreign plugin collision detection with automatic backup (`.lua.bak`) and prompt to resolve conflicts.
- **Companions Management (Uran & Epsilon)**:
  - Settings -> Companions tab for monitoring and managing the Uran Decky plugin and Epsilon systemd user service.
  - Inode-preserving atomic config editing via `utils/companions.py`.
- **UI Modernization & Game Details Overhaul**:
  - Content-grouped Depot Selector for standard installs with dynamic height scaling.
  - Hero banner layout in Game Details with side metadata, SteamDB lockup logo, and ProtonDB badge parity.
  - Floating loading overlay during download and handoff actions.
  - Translucent frosted glass card styling for the Downloads -> Cached tab.
  - Native ACCELA-style desktop shortcut creation.
  - Custom launcher bypass binary picker and config injection.
- **Storage & Auto-Tuning**:
  - Real-world wire-speed adaptive auto-tuning with hill climbing.
  - Install library selection memory for both Executable Downloader and AT0-M modes.

### Changed
- Promoted seasonal holiday theme to `Christmas(beta)` and cleaned up audio playback toggles.
- Retired legacy Hubcap interceptor in favor of modern Lettuce plugin.
- Decoupled Wirecutter proxy token (`X-Assella-Token`) from Katamari worker credentials.
- Streamlined Platform Mismatch dialog to clearly describe Steam Play compatibility requirements.
- Standardized external Hubcap API status caching to 5 minutes across Visor and Search Game tabs.

### Fixed
- **DLC Mode Persistence & Invariants**:
  - Symmetrical DLC mode memory: Executable Downloader records `dlc_mode_source/{appid}` to distinguish between native Steam Play and AT0-M DLC configurations.
  - Strictly guarded base/parent AppIDs from entering `AdditionalApps` or `AdditionalDepots` in DLC-only mode.
  - Scrubbed stale base AppIDs automatically during library scans.
  - Resolved `DepotSelectionDialog` kwarg crashes and false native routing.
  - Filtered unreleased DLCs from depot selection to prevent broken installs.
- **Install & Verify Protections**:
  - Strictly bypassed remote verify checks for pinned builds to protect against upstream manifest clobbering.
  - Decoupled stale bundle freshness from rollback flag to prevent build ID and update cache poisoning.
  - Strictly enforced no-ACF invariant during handoff: Executable Downloader delegates manifest creation natively to Steam and SLSsteam.
- **Performance & Stalls**:
  - Cached smoothly scaled wallpaper pixmap on resize (`_cached_scaled_bg`), completely eliminating the 4.95s GUI thread stall.
  - Lazy-loaded Settings tabs (`addLazyTab`) and asynchronous Game Details artwork/depots loading.
- **Code Quality & Error Handling**:
  - Comprehensive silent exception gate (`scripts/test_silent_exception_gate.py`): eliminated silent `except Exception: pass` swallows across all modules.
  - Fixed `DialogRaiser` eventFilter return value on PyQt6.

## [3.0.6] - 2026-10-28

### Added
- Native PassThrough high-DPI scaling policy for docked Steam Deck and Wayland displays.
- Modernized CLI: replaced legacy Urwid menus with Python Rich for depot/DLC selection, colorized status tables, and multi-column progress tracking.

### Fixed
- Fixed appimageupdatetool delta updates with -O in-place overwrite and explicit target paths.
- Fixed QByteArray decode error in image_fetcher for Last-Modified header validation.
- Dual-routed CLI messages in assfixer to persistent loggers while preserving terminal output.
- Manifest-driven companion plugin filtering and 0-byte corrupt stub protection.
- Guarded QThread lifecycle assertions in TaskRunner and single depot dialog on Qt 6.11.

### Changed
- Upgraded Python standalone AppImage runtime to 3.13.18.
- Upgraded PyQt6 to 6.11.0 / Qt 6.11.2 with fractional scaling protocol support.
- Updated dependencies: cryptography 50.0.2, psutil 7.2.2, wsproto 1.3.2, beautifulsoup4 4.15.0, capstone 5.0.9.

## [3.0.5] - 2026-10-27

### Added
- Dynamic titlebar theming with clean header for normal installs
- Async header fallback and disk color cache
- Dynamic theming with PS5 ambient drift effect
- Game thumbnail vibrant color extraction for ambient radial gradient background
- Redesigned header with click-to-copy AppID, symmetric pills, and smart single-depot routing
- Modernized edition catalog using IStoreBrowseService GetItems API
- DLC mode memory persistence and AT0-M DLC selection dialog
- Custom Byparr solver configuration and Extras plugins support
- AT0-M vs ACCELA mode selection with 64+ DlcData quality-of-life improvements for Steam games
- Missing/pending AT0-M depots displayed as tagged unclickable entries in game details
- Split config buttons for Lobster and Hubcap, AT0-M key sync, single-depot DLC check
- Plugin version display in UI
- Greyscaling of owned DLCs and resized AT0-M setup modal
- Refactored platform mismatch modal UX with eliminated duplicate DLC badges
- Manifest ground truth comparison and self-healing depot recovery
- Human-readable build dates and strict no-ACF invariant enforcement
- Selective DLC entitlement persistence with AdditionalDepots guarded against parent AppIDs
- Direct TLS fast-path for SteamDB racing with dual patchnotes parser and Byparr fallback
- Official SteamDB RSS feed parsing for rich patchnotes and build history
- RenderAppSection fast-path supporting up to 50 builds with Byparr background follow-up
- Bundled zsync fallback support
- Config templates for AT0-M migration
- Online multiplayer and proxy guide with screenshots

### Changed
- Standardized on prepacked spacebunny.lua (v1.8.2) instead of spacebunny1.5 across AT0-M settings and plugin manager, automatically archiving redundant spacebunny1.5.lua to .bak
- Default plugin switched to spacebunny; Lobster autodeployed with AT0-M; smallcrab disabled
- Added steamrun/opensteamtool MRC endpoints

### Fixed
- Safely exclude unkeyed content depots from AdditionalDepots during AT0-M migration with user warnings to prevent DepotWithoutKey failures
- Manifest fix choice modal for atom scope; scoped DLC AppTokens
- Seasonal accent mismatch in tests; aligned DLC mode badge test suites
- Byparr close exception logging and AppToken injection in AT0-M mode
- Pinned vdf git URL to @v4.0 to resolve pip dependency conflict with steam
- Audit findings across AT0-M migration, config permissions, key sync timestamps, and installer
- Lua config string escaping, Hubcap key validation, and plugin .bak preservation on undeploy
- Used get_plugin_status in morrenus tab; added alias in plugin manager
- Test button layout in downloads tab and sanitized wirecutter URL scheme
- Preserved Update All button styling during prep; omitted unconditional -validate
- Refined DLC-only edition selection, polished bottom bar, added ambient radial gradient
- Compact depot selection dialog dimensions; handled blacklisted bundle depots
- Unlinked destination symlink before mv to prevent ELOOP on older coreutils
- Guarded BASH_SOURCE check when executing via piped stdin
- Hardened desktop shortcuts, temp file tracking, download retries, and uninstaller parity
- Repaired broken ACFs in-place instead of deleting
- Removed redundant pre-flight gate from single-game migration engine
- StateFlags set to 6 in ACF during transfer; fixed install instead of deleting

### Build
- Bumped curl-cffi to 0.17.0 for modern browser TLS impersonation

### CI
- Added CI regression gate, guarded cleanup paths, and set StartupWMClass for installer

### Docs
- Added online multiplayer and proxy guide with screenshots

### Reverted
- Reverted UI animation and restored user accent color for buttons and selection

### Other
- AT0-M migration hardening with bundled zsync fallback and config templates (v3.0.4)
