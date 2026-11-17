---
layout: home
hero:
  name: EXE-fingerprinter
  text: Fingerprint a game. Download just the .exes.
  tagline: A tiny terminal app that finds the Steam appid you want and pulls only its Windows executables — nothing else.
  actions:
    - theme: brand
      text: Install
      link: /install
    - theme: alt
      text: How it works
      link: /downloading
features:
  - title: .exe-only downloads
    details: Uses Steam PICS / steamcmd metadata to find the base game's Windows depots and DepotDownloader's manifest-only mode to list files. You pick the .exes; only those are downloaded.
  - title: Search by AppID or short name
    details: Type an appid or a few letters of a name — results are merged from Steam Store, SteamDB, Hubcap and your private API, exact appid first.
  - title: Private API
    details: Your API (HWID-bound, expiry dates, username + password) serves depot keys and manifest GIDs. The public Hubcap works too. Your Hubcap key never leaves the server side.
  - title: Terminal, as it should be
    details: A paws-style TUI with sixel graphics where the terminal supports them and a clean text view where it does not. SFW assets only.
---
