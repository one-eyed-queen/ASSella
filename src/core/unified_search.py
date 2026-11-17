"""
Unified game search.

Combines every source the app can reach into one ranked result list:

- the local EXE-fingerprinter API (if signed in),
- Hubcap's free ``/search`` endpoint,
- Steam's Store search (``/api/storesearch``),
- SteamDB's search page,
- direct AppID resolution via the Store ``appdetails`` endpoint.

Results are merged by AppID and ranked so an exact AppID match is always first,
then exact-name matches, then prefix matches, then the rest.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

STORE_SEARCH = "https://store.steampowered.com/api/storesearch/"
STORE_APPDETAILS = "https://store.steampowered.com/api/appdetails"
STEAMDB_SEARCH = "https://steamdb.info/search/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ── individual sources ─────────────────────────────────────────────────────

def _hubcap_results(query: str, limit: int) -> List[Dict[str, Any]]:
    try:
        from core.morrenus_api import search_games

        data = search_games(query, limit=limit)
        if isinstance(data, dict) and "error" not in data:
            out = []
            for entry in data.get("results", []):
                appid = entry.get("appid") or entry.get("app_id")
                if appid:
                    out.append(
                        {
                            "appid": int(appid),
                            "name": entry.get("name", ""),
                            "header_url": entry.get("header_url", "") or entry.get("header_image", ""),
                            "sources": ["hubcap"],
                        }
                    )
            return out
    except Exception as exc:
        logger.debug("[Search] Hubcap source failed: %s", exc)
    return []


def _api_results(query: str, limit: int) -> List[Dict[str, Any]]:
    try:
        from core import exe_fingerprinter_api as api

        if not api.is_configured():
            return []
        result = api.search(query)
        if not result.ok or not isinstance(result.data, dict):
            return []
        out = []
        for entry in result.data.get("results", [])[:limit]:
            appid = entry.get("appid")
            if appid:
                out.append(
                    {
                        "appid": int(appid),
                        "name": entry.get("name", ""),
                        "header_url": entry.get("header_url", ""),
                        "sources": entry.get("sources") or ["api"],
                    }
                )
        return out
    except Exception as exc:
        logger.debug("[Search] API source failed: %s", exc)
    return []


def _steam_store_results(query: str, limit: int) -> List[Dict[str, Any]]:
    return store_search_full(query, limit)


def _steamdb_results(query: str, limit: int) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            STEAMDB_SEARCH, params={"q": query}, headers=_HEADERS, timeout=15
        )
        if response.status_code != 200:
            return []
        import re

        out: List[Dict[str, Any]] = []
        seen: set[int] = set()
        for appid, raw_name in re.findall(
            r'<a[^>]+href="/app/(\d+)/"[^>]*>(.*?)</a>', response.text, re.DOTALL
        ):
            app_id = int(appid)
            if app_id in seen:
                continue
            seen.add(app_id)
            name = re.sub(r"<[^>]+>", "", raw_name).strip()
            if name:
                out.append(
                    {
                        "appid": app_id,
                        "name": name,
                        "header_url": "",
                        "sources": ["steamdb"],
                    }
                )
            if len(out) >= limit:
                break
        return out
    except Exception as exc:
        logger.debug("[Search] SteamDB source failed: %s", exc)
    return []


def _direct_appid_result(query: str) -> List[Dict[str, Any]]:
    stripped = query.strip()
    if not stripped.isdigit():
        return []
    appid = int(stripped)
    try:
        response = requests.get(
            STORE_APPDETAILS,
            params={"appids": appid, "l": "english"},
            headers=_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        entry = (response.json() or {}).get(str(appid)) or {}
        if not entry.get("success"):
            return []
        data = entry.get("data") or {}
        return [
            {
                "appid": appid,
                "name": data.get("name", f"App {appid}"),
                "header_url": data.get("header_image", ""),
                "sources": ["steam-store", "appid"],
            }
        ]
    except Exception as exc:
        logger.debug("[Search] Direct AppID lookup failed: %s", exc)
    return []


# ── ranking ────────────────────────────────────────────────────────────────

def _rank_key(entry: Dict[str, Any], query: str):
    name = (entry.get("name") or "").lower()
    q = query.strip().lower()
    is_numeric = q.isdigit()

    if is_numeric and str(entry.get("appid")) == q:
        bucket = 0
    elif name == q:
        bucket = 1
    elif name.startswith(q):
        bucket = 2
    elif q in name:
        bucket = 3
    else:
        bucket = 4

    # Prefer entries corroborated by more sources
    source_bonus = -len(entry.get("sources") or [])
    return (bucket, source_bonus, name)


def merge_results(query: str, groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: Dict[int, Dict[str, Any]] = {}
    for group in groups:
        for entry in group:
            appid = entry.get("appid")
            if not appid:
                continue
            appid = int(appid)
            existing = merged.get(appid)
            header = entry.get("header_image") or entry.get("header_url", "")
            if existing is None:
                merged[appid] = {
                    "appid": appid,
                    "name": entry.get("name", ""),
                    "header_url": header,
                    "price": entry.get("price", ""),
                    "kind": entry.get("kind", ""),
                    "sources": list(entry.get("sources") or []),
                }
            else:
                if not existing["name"] and entry.get("name"):
                    existing["name"] = entry["name"]
                if not existing["header_url"] and header:
                    existing["header_url"] = header
                if not existing["price"] and entry.get("price"):
                    existing["price"] = entry["price"]
                if not existing["kind"] and entry.get("kind"):
                    existing["kind"] = entry["kind"]
                for source in entry.get("sources") or []:
                    if source not in existing["sources"]:
                        existing["sources"].append(source)

    return sorted(merged.values(), key=lambda e: _rank_key(e, query))


def search(query: str, limit: int = 30, include_steamdb: bool = True) -> List[Dict[str, Any]]:
    """Search every source and return one ranked list."""
    groups = [
        _direct_appid_result(query),
        _api_results(query, limit),
        _hubcap_results(query, limit),
        _steam_store_results(query, limit),
    ]
    if include_steamdb:
        groups.append(_steamdb_results(query, limit))
    return merge_results(query, groups)[:limit]

# ── rich store results (art, price, description) ─────────────────────────

STORE_HEADER_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

# "artbook", "soundtrack" etc, like paws' classification
_KIND_WORDS = (
    ("artbook", ("art book", "artbook", "digital art")),
    ("soundtrack", ("soundtrack", "ost")),
    ("graphic novel", ("graphic novel",)),
)


def _search_price(price: Any) -> str:
    if not isinstance(price, dict):
        return str(price or "")
    final, currency = price.get("final"), price.get("currency", "")
    if not isinstance(final, (int, float)):
        return ""
    return f"${final / 100:.2f}" if currency == "USD" else f"{final / 100:.2f} {currency}".strip()


def _kind_label(name: str, store_type: str = "") -> str:
    name_l = (name or "").lower()
    if store_type.lower() != "game":
        for label, words in _KIND_WORDS:
            if any(word in name_l for word in words):
                return label
    return {"dlc": "dlc", "music": "soundtrack", "video": "video", "mod": "mod", "demo": "demo"}.get(
        store_type.lower(), ""
    )


def store_search_full(query: str, limit: int = 30, cc: str = "us", lang: str = "en") -> List[Dict[str, Any]]:
    """Store search with prices, header art and kind labels (paws-style)."""
    try:
        response = requests.get(
            STORE_SEARCH,
            params={"term": query, "cc": cc, "l": lang},
            headers=_HEADERS,
            timeout=15,
        )
        items = (response.json() or {}).get("items") or []
    except Exception as exc:
        logger.debug("[Search] store search failed: %s", exc)
        return []

    out = []
    for item in items:
        appid = item.get("id")
        if not appid:
            continue
        name = item.get("name", "") or f"App {appid}"
        out.append(
            {
                "appid": int(appid),
                "name": name,
                "price": _search_price(item.get("price") or {}),
                "kind": _kind_label(name, item.get("type", "")),
                "header_image": item.get("tiny_image") or STORE_HEADER_CDN.format(appid=appid),
                "short_description": "",
                "developers": [],
                "sources": ["steam-store"],
            }
        )
        if len(out) >= limit:
            break
    return out


def app_details(appid: int, cc: str = "us", lang: str = "en") -> Dict[str, Any]:
    """Full details for one app: image, price, developer, short description."""
    try:
        response = requests.get(
            STORE_APPDETAILS,
            params={"appids": str(int(appid)), "cc": cc, "l": lang},
            headers=_HEADERS,
            timeout=15,
        )
        entry = (response.json() or {}).get(str(int(appid))) or {}
        if not entry.get("success"):
            return {}
        info = entry.get("data") or {}
        price = info.get("price_overview") or {}
        return {
            "appid": int(appid),
            "name": info.get("name", ""),
            "price": price.get("final_formatted", ""),
            "header_image": info.get("header_image") or STORE_HEADER_CDN.format(appid=int(appid)),
            "short_description": __import__("html").unescape(info.get("short_description", "") or "").strip(),
            "developers": info.get("developers") or info.get("publishers") or [],
            "genres": [g.get("description", "") for g in info.get("genres", [])],
            "release_date": (info.get("release_date") or {}).get("date", ""),
            "free": bool(info.get("is_free")),
        }
    except Exception as exc:
        logger.debug("[Search] appdetails failed for %s: %s", appid, exc)
        return {}

