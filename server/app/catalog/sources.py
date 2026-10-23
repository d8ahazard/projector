"""Search adapters for free/open media sources. Each returns CatalogItem field dicts."""

from __future__ import annotations

import html
import re
from typing import Awaitable, Callable, Optional

import httpx

SEED_QUERIES: dict[str, list[str]] = {
    "halloween": [
        "halloween",
        "ghost",
        "haunted house",
        "skeleton dance",
        "witch",
        "zombie",
        "jack o lantern",
        "bats flying",
        "spooky",
        "phantom",
        "vampire",
        "creepy fog",
        "pumpkin",
        "spider web",
        "silhouette monster",
        "horror film public domain",
    ],
    "christmas": [
        "christmas",
        "santa claus",
        "snowfall",
        "falling snow",
        "reindeer",
        "christmas lights",
        "elves",
        "snowman",
        "winter night",
        "christmas cartoon",
        "jingle bells",
        "gingerbread",
    ],
}

AUDIO_SEEDS: dict[str, list[str]] = {
    "halloween": ["halloween ambience", "creepy laugh", "ghost moan", "thunder storm", "witch cackle", "haunted"],
    "christmas": ["sleigh bells", "christmas music", "ho ho ho", "fireplace crackle", "winter wind"],
}

POSITIVE = {
    "black background": 0.35,
    "on black": 0.3,
    "isolated": 0.15,
    "overlay": 0.2,
    "projection": 0.25,
    "hologram": 0.2,
    "window": 0.1,
    "silhouette": 0.15,
    "ghost": 0.1,
    "loop": 0.1,
    "seamless": 0.1,
    "night": 0.05,
    "dark": 0.05,
    "animation": 0.05,
    "cartoon": 0.05,
    "snowfall": 0.1,
    "falling snow": 0.15,
}
NEGATIVE = {"green screen": -0.15, "greenscreen": -0.15, "chroma": -0.1, "interview": -0.3, "lecture": -0.3, "news": -0.2, "trailer": -0.1, "podcast": -0.3}

UA = {"User-Agent": "ProjectorApp/0.1 (home projection; https://github.com/)"}

Search = Callable[[httpx.AsyncClient, str, str, str, dict], Awaitable[list[dict]]]


def strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def heuristic_score(text: str, kind: str, duration_ms: int) -> float:
    t = text.lower()
    score = 0.35
    for k, v in POSITIVE.items():
        if k in t:
            score += v
    for k, v in NEGATIVE.items():
        if k in t:
            score += v
    if kind == "video" and duration_ms:
        if duration_ms < 4000:
            score -= 0.15
        elif duration_ms <= 5 * 60_000:
            score += 0.1
        elif duration_ms > 45 * 60_000:
            score -= 0.1
    return round(max(0.0, min(1.0, score)), 3)


def license_name(url: str) -> str:
    u = (url or "").lower()
    if "publicdomain/zero" in u:
        return "CC0"
    if "publicdomain" in u:
        return "Public Domain"
    m = re.search(r"licenses/([a-z\-]+)/([\d.]+)", u)
    if m:
        return f"CC {m.group(1).upper()} {m.group(2)}"
    return url or ""


def _item(source: str, source_id: str, kind: str, title: str, season: str, query: str, **kw) -> dict:
    tags = sorted({w for w in re.split(r"[,\s]+", query.lower()) if len(w) > 2} | set(kw.pop("tags", [])))
    text = f"{title} {kw.get('description', '')} {' '.join(tags)}"
    return {
        "source": source,
        "source_id": str(source_id),
        "kind": kind,
        "title": (title or "")[:300],
        "season": season,
        "tags": tags[:20],
        "suitability": heuristic_score(text, kind, kw.get("duration_ms", 0)),
        **kw,
    }


# ------------------------------------------------------------ archive.org


async def internet_archive(client: httpx.AsyncClient, query: str, season: str, kind: str, keys: dict) -> list[dict]:
    mediatype = "audio" if kind == "audio" else "movies"
    q = f"({query}) AND mediatype:{mediatype} AND (licenseurl:*creativecommons* OR licenseurl:*publicdomain*)"
    r = await client.get(
        "https://archive.org/advancedsearch.php",
        params={"q": q, "fl[]": ["identifier", "title", "description", "licenseurl", "creator", "subject"], "rows": 60, "output": "json"},
    )
    r.raise_for_status()
    out = []
    for d in r.json().get("response", {}).get("docs", []):
        ident = d.get("identifier")
        if not ident:
            continue
        desc = d.get("description") or ""
        if isinstance(desc, list):
            desc = " ".join(desc)
        subj = d.get("subject") or []
        if isinstance(subj, str):
            subj = [subj]
        creator = d.get("creator") or ""
        if isinstance(creator, list):
            creator = ", ".join(creator)
        lic = d.get("licenseurl") or ""
        out.append(
            _item(
                "archive",
                ident,
                kind,
                d.get("title") or ident,
                season,
                query,
                description=strip_html(desc)[:1000],
                page_url=f"https://archive.org/details/{ident}",
                thumb_url=f"https://archive.org/services/img/{ident}",
                license=license_name(lic),
                license_url=lic,
                author=creator,
                tags=[s.lower() for s in subj[:8] if isinstance(s, str)],
            )
        )
    return out


VIDEO_FORMATS = ["h.264", "h.264 ia", "mpeg4", "512kb mpeg4", "ogg video", "webm"]
AUDIO_FORMATS = ["vbr mp3", "mp3", "128kbps mp3", "ogg vorbis", "flac"]


async def resolve_archive(client: httpx.AsyncClient, ident: str, kind: str, max_bytes: int) -> Optional[str]:
    """Picks the best playable derivative file for an archive.org item."""
    r = await client.get(f"https://archive.org/metadata/{ident}")
    r.raise_for_status()
    files = r.json().get("files", [])
    prefs = AUDIO_FORMATS if kind == "audio" else VIDEO_FORMATS
    best = None
    best_rank = len(prefs) + 1
    for f in files:
        fmt = (f.get("format") or "").lower()
        size = int(f.get("size") or 0)
        if size and size > max_bytes:
            continue
        rank = prefs.index(fmt) if fmt in prefs else None
        if rank is None:
            ext = (f.get("name") or "").lower().rsplit(".", 1)[-1]
            if kind != "audio" and ext in ("mp4", "m4v", "webm", "ogv", "mov"):
                rank = len(prefs)
            elif kind == "audio" and ext in ("mp3", "ogg", "wav", "flac"):
                rank = len(prefs)
        if rank is not None and rank < best_rank:
            best, best_rank = f, rank
    if not best:
        return None
    return f"https://archive.org/download/{ident}/{best['name']}"


# ------------------------------------------------------- wikimedia commons


async def wikimedia(client: httpx.AsyncClient, query: str, season: str, kind: str, keys: dict) -> list[dict]:
    ftype = "audio" if kind == "audio" else "video"
    r = await client.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:{ftype} {query}",
            "gsrnamespace": 6,
            "gsrlimit": 40,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mediatype",
            "iiurlwidth": 320,
        },
    )
    r.raise_for_status()
    out = []
    for page in (r.json().get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})

        def m(k: str) -> str:
            return strip_html(meta.get(k, {}).get("value", ""))

        title = page.get("title", "").removeprefix("File:")
        title = re.sub(r"\.(webm|ogv|ogg|mp4|mp3|wav|flac|oga)$", "", title, flags=re.I)
        out.append(
            _item(
                "wikimedia",
                page.get("pageid"),
                kind,
                title,
                season,
                query,
                description=m("ImageDescription")[:1000],
                page_url=info.get("descriptionurl", ""),
                media_url=info.get("url", ""),
                thumb_url=info.get("thumburl", ""),
                license=m("LicenseShortName"),
                license_url=m("LicenseUrl"),
                author=m("Artist")[:200],
                duration_ms=int(float(info.get("duration") or 0) * 1000),
                width=int(info.get("width") or 0),
                height=int(info.get("height") or 0),
            )
        )
    return out


# ------------------------------------------------------------------ pexels


async def pexels(client: httpx.AsyncClient, query: str, season: str, kind: str, keys: dict) -> list[dict]:
    key = keys.get("pexels_api_key")
    if not key or kind == "audio":
        return []
    r = await client.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": 40, "orientation": "landscape"},
        headers={"Authorization": key},
    )
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("height")]
        if not files:
            continue
        f = min(files, key=lambda f: abs(int(f["height"]) - 720) + (0 if int(f["height"]) >= 720 else 200))
        slug = (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        title = re.sub(r"-\d+$", "", slug).replace("-", " ").strip().capitalize() or f"Pexels {v.get('id')}"
        out.append(
            _item(
                "pexels",
                v.get("id"),
                "video",
                title,
                season,
                query,
                page_url=v.get("url", ""),
                media_url=f.get("link", ""),
                thumb_url=v.get("image", ""),
                license="Pexels License",
                license_url="https://www.pexels.com/license/",
                author=(v.get("user") or {}).get("name", ""),
                duration_ms=int(v.get("duration") or 0) * 1000,
                width=int(f.get("width") or 0),
                height=int(f.get("height") or 0),
            )
        )
    return out


# ----------------------------------------------------------------- pixabay


async def pixabay(client: httpx.AsyncClient, query: str, season: str, kind: str, keys: dict) -> list[dict]:
    key = keys.get("pixabay_api_key")
    if not key or kind == "audio":
        return []
    r = await client.get("https://pixabay.com/api/videos/", params={"key": key, "q": query[:100], "per_page": 50, "safesearch": "true"})
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        vids = h.get("videos", {})
        f = vids.get("medium") or vids.get("large") or vids.get("small") or {}
        if not f.get("url"):
            continue
        tags = [t.strip() for t in (h.get("tags") or "").split(",") if t.strip()]
        out.append(
            _item(
                "pixabay",
                h.get("id"),
                "video",
                ", ".join(tags[:3]).title() or f"Pixabay {h.get('id')}",
                season,
                query,
                page_url=h.get("pageURL", ""),
                media_url=f.get("url", ""),
                thumb_url=f.get("thumbnail", ""),
                license="Pixabay Content License",
                license_url="https://pixabay.com/service/license-summary/",
                author=h.get("user", ""),
                duration_ms=int(h.get("duration") or 0) * 1000,
                width=int(f.get("width") or 0),
                height=int(f.get("height") or 0),
                tags=tags,
            )
        )
    return out


# --------------------------------------------------------------- freesound


async def freesound(client: httpx.AsyncClient, query: str, season: str, kind: str, keys: dict) -> list[dict]:
    key = keys.get("freesound_api_key")
    if not key or kind != "audio":
        return []
    r = await client.get(
        "https://freesound.org/apiv2/search/text/",
        params={
            "query": query,
            "token": key,
            "page_size": 50,
            "fields": "id,name,url,license,username,duration,previews,images,tags,description",
        },
    )
    r.raise_for_status()
    out = []
    for s in r.json().get("results", []):
        lic = s.get("license", "")
        out.append(
            _item(
                "freesound",
                s.get("id"),
                "audio",
                s.get("name", ""),
                season,
                query,
                description=strip_html(s.get("description", ""))[:1000],
                page_url=s.get("url", ""),
                media_url=(s.get("previews") or {}).get("preview-hq-mp3", ""),
                thumb_url=(s.get("images") or {}).get("waveform_m", ""),
                license=license_name(lic),
                license_url=lic,
                author=s.get("username", ""),
                duration_ms=int(float(s.get("duration") or 0) * 1000),
                tags=[t.lower() for t in (s.get("tags") or [])[:10]],
            )
        )
    return out


SOURCES: dict[str, tuple[Search, set[str], Optional[str]]] = {
    # name: (search fn, kinds, required settings key)
    "archive": (internet_archive, {"video", "audio"}, None),
    "wikimedia": (wikimedia, {"video", "audio"}, None),
    "pexels": (pexels, {"video"}, "pexels_api_key"),
    "pixabay": (pixabay, {"video"}, "pixabay_api_key"),
    "freesound": (freesound, {"audio"}, "freesound_api_key"),
}
