# How it works

1. **Search** — type an AppID (e.g. `620`) or the first few letters of a name (e.g. `portal`). The search merges Steam Store, SteamDB, Hubcap and the private API, and puts an exact AppID match first.
2. **Fingerprint** — the app asks Steam PICS / the steamcmd API which depots the base game ships for Windows (DLC depots are skipped), fetches a `-manifest-only` dump via DepotDownloader, and lists every `.exe` in it.
3. **Choose** — pick the executables you want. Only those files are downloaded, by file list, into `%APPDATA%\exe-pawprint\<appid>` (or the equivalent on Linux/macOS).

## API mode

`python3 src/main.py --api --appid 620` fingerprints without an interactive session and prints the `.exe` list; `--download` also fetches them. In the interactive TUI, **API mode** fingerprints the game first and never adds or removes games — two choices: download the executables, or use the desktop flow.

## Where the data comes from

* **Vault/PICS** — depot manifests and GIDs (via steamcmd's public API and Steam's PICS as fallback).
* **Hubcap** — a public bundle with depot keys + manifests, used for the fingerprint dump (`/api/v1/manifest/{appid}`).
* **Private API** — your own Vercel service (see the app's *Private API* settings): HWID-bound accounts, depot keys served from your database, Hubcap as a server-side fallback. The app never talks to your database directly.

## Download location

`%APPDATA%\exe-pawprint\<appid>` on Windows, `~/.local/share/exe-pawprint/<appid>` on Linux, `~/Library/Application Support/exe-pawprint/<appid>` on macOS. Override with `--dest PATH`.
