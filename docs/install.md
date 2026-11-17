# Installing EXE-fingerprinter

## From source

```bash
git clone https://github.com/one-eyed-queen/paws-fingerprinter.git
cd paws-fingerprinter
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 src/main.py
```

The app is a TUI — it needs a terminal. Sixel graphics are used where the terminal supports them; everywhere else you get the text interface.

### First run

* Search: type `620` or `portal` in the search box.
* Add your Hubcap key under **Settings → Private API & Appearance** (`morrenus_api_key` in settings) if you have one, or sign in to your private API.
* Downloads land in `%APPDATA%\exe-pawprint` (see [How it works](/downloading)).

## AppImage

Grab the AppImage from Releases and make it executable:

```bash
chmod +x EXE-fingerprinter-*.AppImage
./EXE-fingerprinter-*.AppImage
```

The AppImage bundles its own Python runtime and DepotDownloader. It needs a terminal to render the interface.

## On SteamOS / Linux

Works the same from Konsole or a terminal in Desktop Mode. No Qt, no desktop integration, no game library changes — this tool never touches your Steam installation.
