"""allanime.day client — pure Python port of ani-cli's scraping pipeline.

Implements:
  - search(query, mode)           : GraphQL show search
  - episodes(allanime_id, mode)   : list of episode numbers
  - sources(id, ep, mode)         : resolves playable streams (the hard part)

The source resolution mirrors ani-cli/get_episode_url:
  1. POST GraphQL `episode_embed_gql` to api.allanime.day
  2. If response carries `tobeparsed`, AES-256-CTR decrypt with key=sha256("Xot36i3lK3:v1")
  3. Each provider line is "<name> :<encoded_path>"; decode hex pairs via the
     provider lookup table to recover an `/apivtwo/clock?id=...` path
  4. GET that path (suffixed with `.json`) — response has either direct
     {link, resolutionStr} entries or a master m3u8 URL with a Referer
  5. For m3u8: fetch the master, parse #EXT-X-STREAM-INF to expose qualities

This module has zero side effects on the rest of the app; it can be exercised
from a REPL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger("apps.anime.allanime")

# ---------------------------------------------------------------------------
# Constants (match ani-cli line-for-line)
# ---------------------------------------------------------------------------

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
ALLANIME_REFR = "https://allmanga.to"
ALLANIME_BASE = "allanime.day"
ALLANIME_API = f"https://api.{ALLANIME_BASE}/api"
ALLANIME_KEY = hashlib.sha256(b"Xot36i3lK3:v1").digest()  # 32 bytes
PERSISTED_QUERY_HASH = "d405d0edd690624b66baba3068e0edc3ac90f1597d898a1ec8db4e5c43c00fec"

SEARCH_GQL = (
    "query( $search: SearchInput $limit: Int $page: Int "
    "$translationType: VaildTranslationTypeEnumType "
    "$countryOrigin: VaildCountryOriginEnumType ) "
    "{ shows( search: $search limit: $limit page: $page "
    "translationType: $translationType countryOrigin: $countryOrigin ) "
    "{ edges { _id name availableEpisodes __typename } }}"
)

EPISODES_GQL = (
    "query ($showId: String!) "
    "{ show( _id: $showId ) { _id availableEpisodesDetail }}"
)

EMBED_GQL = (
    "query ($showId: String!, $translationType: VaildTranslationTypeEnumType!, "
    "$episodeString: String!) { episode( showId: $showId "
    "translationType: $translationType episodeString: $episodeString ) "
    "{ episodeString sourceUrls }}"
)

# Provider lookup — exact substitution table from ani-cli line 175.
# Key: 2-hex-char source byte; Value: target ASCII char.
_PROVIDER_HEX_MAP = {
    "79": "A", "7a": "B", "7b": "C", "7c": "D", "7d": "E", "7e": "F", "7f": "G",
    "70": "H", "71": "I", "72": "J", "73": "K", "74": "L", "75": "M", "76": "N", "77": "O",
    "68": "P", "69": "Q", "6a": "R", "6b": "S", "6c": "T", "6d": "U", "6e": "V", "6f": "W",
    "60": "X", "61": "Y", "62": "Z",
    "59": "a", "5a": "b", "5b": "c", "5c": "d", "5d": "e", "5e": "f", "5f": "g",
    "50": "h", "51": "i", "52": "j", "53": "k", "54": "l", "55": "m", "56": "n", "57": "o",
    "48": "p", "49": "q", "4a": "r", "4b": "s", "4c": "t", "4d": "u", "4e": "v", "4f": "w",
    "40": "x", "41": "y", "42": "z",
    "08": "0", "09": "1", "0a": "2", "0b": "3", "0c": "4",
    "0d": "5", "0e": "6", "0f": "7", "00": "8", "01": "9",
    "15": "-", "16": ".", "67": "_", "46": "~", "02": ":", "17": "/",
    "07": "?", "1b": "#", "63": "[", "65": "]", "78": "@", "19": "!",
    "1c": "$", "1e": "&", "10": "(", "11": ")", "12": "*", "13": "+",
    "14": ",", "03": ";", "05": "=", "1d": "%",
}

# Provider name → regex applied to the decrypted line list.
# These map to ani-cli's provider_init() second arguments.
_PROVIDER_PATTERNS = [
    ("hianime",    re.compile(r"^Luf-Mp4 :(.+)$")),    # m3u8 multi (default)
    ("wixmp",      re.compile(r"^Default :(.+)$")),    # m3u8 -> mp4 multi
    ("sharepoint", re.compile(r"^S-mp4 :(.+)$")),      # mp4 single
    ("youtube",    re.compile(r"^Yt-mp4 :(.+)$")),     # mp4 single
    # filemoon ("Fm-mp4") uses a different decryption pipeline; skipping
    # it in v1 keeps this file simple. hianime + wixmp cover the vast
    # majority of titles.
]

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    allanime_id: str
    title: str
    episode_count: int

    def to_dict(self) -> dict:
        return {
            "allanime_id": self.allanime_id,
            "title": self.title,
            "episode_count": self.episode_count,
        }


@dataclass
class Stream:
    quality: str           # "1080", "720", etc., or "auto" if master m3u8
    url: str               # absolute URL to playlist or media
    referer: str = ""      # required Referer header for upstream requests
    subs_url: str = ""     # optional soft-subtitle URL (vtt)
    is_hls: bool = True

    def to_dict(self) -> dict:
        return {
            "quality": self.quality,
            "url": self.url,
            "referer": self.referer,
            "subs_url": self.subs_url,
            "is_hls": self.is_hls,
        }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _client(referer: str = ALLANIME_REFR) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Referer": referer},
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
    )


async def _post_gql(client: httpx.AsyncClient, variables: dict, query: str) -> dict:
    body = {"variables": variables, "query": query}
    r = await client.post(
        ALLANIME_API,
        content=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search(query: str, mode: str = "sub", limit: int = 40) -> list[SearchResult]:
    """Search the allanime catalog. Returns shows that have at least 1 episode in `mode`."""
    if mode not in ("sub", "dub"):
        mode = "sub"
    variables = {
        "search": {"allowAdult": False, "allowUnknown": False, "query": query},
        "limit": limit,
        "page": 1,
        "translationType": mode,
        "countryOrigin": "ALL",
    }
    async with _client() as c:
        data = await _post_gql(c, variables, SEARCH_GQL)

    edges = (((data.get("data") or {}).get("shows") or {}).get("edges")) or []
    results: list[SearchResult] = []
    for edge in edges:
        ae = edge.get("availableEpisodes") or {}
        ep_count = int(ae.get(mode) or 0)
        if ep_count <= 0:
            continue
        results.append(SearchResult(
            allanime_id=edge["_id"],
            title=edge.get("name") or "",
            episode_count=ep_count,
        ))
    results.sort(key=lambda r: (-r.episode_count, r.title.lower()))
    return results


# ---------------------------------------------------------------------------
# Episode list
# ---------------------------------------------------------------------------

async def episodes(allanime_id: str, mode: str = "sub") -> list[str]:
    """Return episode numbers (as strings, since allanime uses fractional eps like "12.5")."""
    if mode not in ("sub", "dub"):
        mode = "sub"
    variables = {"showId": allanime_id}
    async with _client() as c:
        data = await _post_gql(c, variables, EPISODES_GQL)

    show = ((data.get("data") or {}).get("show")) or {}
    detail = show.get("availableEpisodesDetail") or {}
    eps = detail.get(mode) or []
    # Sort numerically (handles "1", "2", ... "12.5", ...)
    def _key(ep: str) -> float:
        try:
            return float(ep)
        except (TypeError, ValueError):
            return float("inf")
    return sorted([str(e) for e in eps], key=_key)


# ---------------------------------------------------------------------------
# Source resolution — the AES + provider pipeline
# ---------------------------------------------------------------------------

def _decode_provider_path(encoded: str) -> str:
    """Apply ani-cli's hex-pair lookup table to recover the API path."""
    encoded = encoded.strip()
    out = []
    # Walk two hex chars at a time. Anything not matching the table
    # is dropped (matches `sed` behavior on unmatched inputs).
    for i in range(0, len(encoded) - 1, 2):
        pair = encoded[i:i + 2].lower()
        ch = _PROVIDER_HEX_MAP.get(pair)
        if ch is not None:
            out.append(ch)
    decoded = "".join(out)
    # ani-cli appends `.json` to /clock paths
    return decoded.replace("/clock", "/clock.json")


def _decrypt_tobeparsed(blob: str) -> str:
    """AES-256-CTR decrypt of the `tobeparsed` blob.

    Layout (matches decode_tobeparsed in ani-cli):
      byte 0       : skip (length marker)
      bytes 1..13  : IV (12 bytes)
      bytes 13..-16: ciphertext (CTR has no padding/tag, but ani-cli skips
                     the trailing 16 bytes and uses -nopad)
    Counter = IV (12 bytes) || 0x00000002 (4 bytes), big-endian.
    """
    raw = base64.b64decode(blob)
    if len(raw) < 30:
        return ""
    iv = raw[1:13]
    ct = raw[13:-16]
    counter_initial = iv + bytes([0, 0, 0, 2])
    cipher = Cipher(algorithms.AES(ALLANIME_KEY), modes.CTR(counter_initial))
    decryptor = cipher.decryptor()
    plain = decryptor.update(ct) + decryptor.finalize()
    return plain.decode("utf-8", errors="replace")


_LINE_RE = re.compile(r'"sourceUrl":"([^"]+)".*?"sourceName":"([^"]+)"')


def _extract_provider_lines(payload_text: str) -> list[str]:
    """Convert the decrypted/raw text into ani-cli-style "<name> :<payload>" lines.

    Payload is either:
      - "--<hex>"     : obfuscated path that needs the hex-pair lookup decode
      - "@<url>"      : pre-resolved direct URL (e.g. Yt-mp4 on fast4speed.rsvp)
    External iframe embeds (ok.ru / streamlare / streamsb / mp4upload / filemoon
    on opaque hosts) are emitted with the same shape but no provider in
    _PROVIDER_PATTERNS matches their names, so they're silently dropped in
    sources(). That's intentional — we don't scrape iframe players.
    """
    text = payload_text.replace("\\u002F", "/").replace("\\/", "/")
    text = text.replace("},{", "}\n{")
    lines = []
    for chunk in text.split("\n"):
        m = _LINE_RE.search(chunk)
        if not m:
            continue
        raw_url = m.group(1)
        name = m.group(2)
        if raw_url.startswith("--"):
            lines.append(f"{name} :{raw_url[2:]}")
        else:
            # Direct URL — prefix with `@` so sources() can tell it apart from
            # an obfuscated path without doing string heuristics later.
            lines.append(f"{name} :@{raw_url}")
    return lines


async def _fetch_provider_streams(client: httpx.AsyncClient, provider_name: str, path: str) -> list[Stream]:
    """Fetch one provider's resolved URL list. Path looks like '/apivtwo/clock.json?id=...'.

    Retries transient failures (network errors and 5xx upstream responses) up to
    twice with short backoff — allanime.day is flaky enough that a single blip
    used to bubble all the way up as a 404 to the user.
    """
    url = f"https://{ALLANIME_BASE}{path}"
    body = ""
    last_err: str = ""
    for attempt in range(3):
        try:
            r = await client.get(url)
            if r.status_code == 200 and r.text:
                body = r.text
                break
            if r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
            else:
                # 4xx or empty 200 — not retryable, give up cleanly
                return []
        except httpx.HTTPError as exc:
            last_err = f"{type(exc).__name__}: {exc!r}"
        if attempt < 2:
            await asyncio.sleep(0.25 * (attempt + 1))

    if not body:
        logger.warning("provider %s fetch failed after retries: %s", provider_name, last_err)
        return []

    streams: list[Stream] = []

    # Direct {link, resolutionStr} variants (wixmp pre-resolved)
    for m in re.finditer(r'"link":"([^"]+)".*?"resolutionStr":"([^"]+)"', body):
        link = m.group(1).replace("\\/", "/")
        quality = re.sub(r"\D", "", m.group(2)) or "auto"

        # wixmp multi-bitrate packager URL: expand into per-quality MP4s.
        # Pattern: https://repackager.wixmp.com/video.wixstatic.com/video/<id>/,480p,720p,/mp4/file.mp4.urlset/master.m3u8
        wixmp = re.match(
            r"https?://repackager\.wixmp\.com/(.+?)/,([^/]+),(/mp4/[^.]+\.mp4)\.urlset.*",
            link,
        )
        if wixmp:
            host_path = wixmp.group(1)
            qualities = [q for q in wixmp.group(2).split(",") if q]
            tail = wixmp.group(3)
            for q in qualities:
                mp4_url = f"https://{host_path}/{q}{tail}"
                q_height = re.sub(r"\D", "", q) or "auto"
                streams.append(Stream(
                    quality=q_height, url=mp4_url, referer=ALLANIME_REFR, is_hls=False,
                ))
            # Also keep the original master URL as a fallback
            streams.append(Stream(quality="auto", url=link, referer=ALLANIME_REFR, is_hls=True))
        else:
            streams.append(Stream(quality=quality, url=link, referer=ALLANIME_REFR))

    # Master m3u8 form: {"hls":..., "url":"...master.m3u8", "Referer":"..."}
    m3u8_match = re.search(r'"url":"([^"]+master\.m3u8[^"]*)"', body)
    if m3u8_match:
        master_url = m3u8_match.group(1).replace("\\/", "/")
        ref_match = re.search(r'"Referer":"([^"]+)"', body)
        upstream_referer = ref_match.group(1).replace("\\/", "/") if ref_match else ALLANIME_REFR
        # Optional subtitle
        subs = ""
        subs_match = re.search(
            r'"subtitles":\[\{"lang":"en","label":"English","default":"default","src":"([^"]+)"',
            body,
        )
        if subs_match:
            subs = subs_match.group(1).replace("\\/", "/")

        # Always offer the master playlist as "auto" (player handles ABR).
        # Then try to enumerate its variants for explicit quality picks.
        streams.append(Stream(
            quality="auto", url=master_url,
            referer=upstream_referer, subs_url=subs, is_hls=True,
        ))
        try:
            vr = await client.get(master_url, headers={"Referer": upstream_referer})
            if vr.status_code == 200 and "EXTM3U" in vr.text:
                base = master_url.rsplit("/", 1)[0] + "/"
                stream_inf_re = re.compile(r"#EXT-X-STREAM-INF:[^\n]*RESOLUTION=\d+x(\d+)[^\n]*\n([^\n]+)")
                for vm in stream_inf_re.finditer(vr.text):
                    height = vm.group(1)
                    rel = vm.group(2).strip()
                    abs_url = rel if rel.startswith("http") else base + rel
                    streams.append(Stream(
                        quality=height, url=abs_url,
                        referer=upstream_referer, subs_url=subs, is_hls=True,
                    ))
        except httpx.HTTPError as exc:
            logger.debug("master playlist fetch failed: %s", exc)

    return streams


async def sources(allanime_id: str, episode: str, mode: str = "sub") -> list[Stream]:
    """Resolve all playable streams for an episode, sorted best-first.

    Mirrors ani-cli/get_episode_url end-to-end. Returns a deduplicated list of
    Stream objects ordered by quality (highest first; "auto" master last).
    """
    if mode not in ("sub", "dub"):
        mode = "sub"

    variables = {
        "showId": allanime_id,
        "translationType": mode,
        "episodeString": str(episode),
    }

    # Try persisted-query GET first (matches ani-cli's primary path), then fall back to POST.
    api_resp_text = ""
    async with httpx.AsyncClient(
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://youtu-chan.com",
            "Origin": "https://youtu-chan.com",
        },
        timeout=20.0,
    ) as c1:
        try:
            ext = {"persistedQuery": {"version": 1, "sha256Hash": PERSISTED_QUERY_HASH}}
            r = await c1.get(
                ALLANIME_API,
                params={"variables": json.dumps(variables), "extensions": json.dumps(ext)},
            )
            if r.status_code == 200 and "tobeparsed" in r.text:
                api_resp_text = r.text
        except httpx.HTTPError:
            pass

    if not api_resp_text:
        async with _client() as c2:
            data = await _post_gql(c2, variables, EMBED_GQL)
            api_resp_text = json.dumps(data)

    # Extract provider lines
    if '"tobeparsed"' in api_resp_text:
        m = re.search(r'"tobeparsed":"([^"]+)"', api_resp_text)
        if not m:
            return []
        decrypted = _decrypt_tobeparsed(m.group(1))
        provider_lines = _extract_provider_lines(decrypted)
    else:
        provider_lines = _extract_provider_lines(api_resp_text)

    if not provider_lines:
        logger.warning("anime sources: no provider lines for %s ep %s", allanime_id, episode)
        return []

    # For each known provider, decode its target. Two shapes:
    #   - "/apivtwo/clock.json?id=..."  → relative API path; fetch + parse
    #   - "https://tools.fast4speed.rsvp/..."  → direct mp4 URL (Yt provider)
    streams: list[Stream] = []
    tasks: list[asyncio.Task] = []
    async with _client() as c:
        for name, pattern in _PROVIDER_PATTERNS:
            for line in provider_lines:
                m = pattern.match(line)
                if not m:
                    continue
                raw = m.group(1)
                if raw.startswith("@"):
                    target = raw[1:]  # pre-resolved direct URL
                else:
                    target = _decode_provider_path(raw)
                if target.startswith("https://"):
                    # Direct media URL — no further fetching needed.
                    # Per ani-cli, these need the allanime referer.
                    is_hls = target.endswith(".m3u8") or "m3u8" in target
                    streams.append(Stream(
                        quality="auto" if is_hls else "720",
                        url=target,
                        referer=ALLANIME_REFR,
                        is_hls=is_hls,
                    ))
                elif target.startswith("/"):
                    tasks.append(asyncio.create_task(_fetch_provider_streams(c, name, target)))
                # else: malformed decoding — drop
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.debug("provider task failed: %s", res)
                    continue
                streams.extend(res)

    # Dedupe by URL, sort: numeric quality desc, then "auto" last
    seen: set[str] = set()
    deduped: list[Stream] = []
    for s in streams:
        if s.url in seen:
            continue
        seen.add(s.url)
        deduped.append(s)

    def _sort_key(s: Stream) -> tuple[int, int]:
        try:
            return (0, -int(s.quality))
        except ValueError:
            return (1, 0)
    deduped.sort(key=_sort_key)
    return deduped


def pick_quality(streams: list[Stream], preference: str = "best") -> Optional[Stream]:
    """Pick a stream from a resolved list. preference='best'|'worst'|'1080'|...|'auto'."""
    if not streams:
        return None
    pref = (preference or "best").strip().lower()

    numeric = [s for s in streams if s.quality.isdigit()]
    if pref == "best":
        return numeric[0] if numeric else streams[0]
    if pref == "worst":
        return numeric[-1] if numeric else streams[-1]
    if pref == "auto":
        for s in streams:
            if s.quality == "auto":
                return s
        return streams[0]
    # Specific height: exact match, then closest-not-exceeding
    pref_height = re.sub(r"\D", "", pref)
    if pref_height:
        target = int(pref_height)
        exact = [s for s in numeric if s.quality == pref_height]
        if exact:
            return exact[0]
        not_exceeding = [s for s in numeric if int(s.quality) <= target]
        if not_exceeding:
            return not_exceeding[0]
    return streams[0]
