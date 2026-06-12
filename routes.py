"""Anime App API Routes
=======================
FastAPI router mounted at /api/apps/anime/ by the app loader.

Endpoints:
  GET  /search?q=...&mode=sub
  GET  /episodes/{allanime_id}?mode=sub
  GET  /sources/{allanime_id}/{episode}?mode=sub
  GET  /stream/{allanime_id}/{episode}/{quality}/master.m3u8?mode=sub
  GET  /stream/proxy?u=<base64-url>&r=<base64-referer>      (HLS segments)
  GET  /history
  POST /history/record
  GET  /resume
  GET  /events                                              (SSE)

The /stream endpoints are the player's data plane: they proxy upstream HLS
playlists and segments, attaching the Referer header that the browser cannot
set itself, and rewriting playlist segment URLs so segments also flow through
this proxy.
"""

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from app_platform.auth import scope_user
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from apps.anime import allanime, data as _dl

logger = logging.getLogger(__name__)
router = APIRouter()

# Standalone HLS pop-out player page (opened by AnimePlayerApp's "pop out"). It is
# app-owned and served at /api/apps/anime/player.html — its internal calls already
# target /api/apps/anime, so it needs no platform route.
_PLAYER_HTML = Path(__file__).resolve().parent / "static" / "anime-player.html"


@router.get("/player.html")
async def serve_player_page():
    """Serve the standalone pop-out video player page."""
    if _PLAYER_HTML.is_file():
        return FileResponse(_PLAYER_HTML, media_type="text/html")
    raise HTTPException(status_code=404, detail="Anime player page not found")


# ---------------------------------------------------------------------------
# SSE broadcast (so AnimeApp / AnimePlayerApp can refresh on history changes)
# ---------------------------------------------------------------------------

_sse_clients: set[asyncio.Queue] = set()


def _broadcast(event_type: str, **fields) -> None:
    payload = json.dumps({"type": event_type, **fields})
    for q in list(_sse_clients):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


@router.get("/events")
async def anime_events():
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    _sse_clients.add(queue)

    async def stream():
        try:
            yield "data: {\"type\":\"connected\"}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_clients.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Search / episodes / sources
# ---------------------------------------------------------------------------

@router.get("/search")
async def api_search(q: str = "", mode: str = "sub"):
    if not q.strip():
        return {"results": [], "count": 0}
    try:
        results = await allanime.search(q.strip(), mode=mode)
    except Exception as exc:
        logger.warning("anime search failed for '%s': %s", q, exc)
        raise HTTPException(502, f"Upstream search failed: {exc}")
    return {
        "results": [r.to_dict() for r in results],
        "count": len(results),
        "mode": mode,
    }


@router.get("/episodes/{allanime_id}")
async def api_episodes(allanime_id: str, mode: str = "sub"):
    try:
        eps = await allanime.episodes(allanime_id, mode=mode)
    except Exception as exc:
        logger.warning("anime episodes failed for %s: %s", allanime_id, exc)
        raise HTTPException(502, f"Upstream episode fetch failed: {exc}")
    title_row = await asyncio.to_thread(_dl.get_title_by_allanime_id, allanime_id)
    return {
        "allanime_id": allanime_id,
        "mode": mode,
        "episodes": eps,
        "count": len(eps),
        "title": title_row.get("title", "") if title_row else "",
    }


@router.get("/sources/{allanime_id}/{episode}")
async def api_sources(allanime_id: str, episode: str, mode: str = "sub", refresh: bool = False):
    """Resolve playable streams. Cached for ~10 min unless ?refresh=true."""
    if not refresh:
        cached = await asyncio.to_thread(_dl.get_cached_sources, allanime_id, episode, mode)
        if cached and cached["streams"]:
            return {"cached": True, **cached}

    try:
        streams = await allanime.sources(allanime_id, episode, mode=mode)
    except Exception as exc:
        logger.warning("anime sources failed for %s ep %s: %s", allanime_id, episode, exc)
        raise HTTPException(502, f"Upstream source resolution failed: {exc}")

    if not streams:
        raise HTTPException(404, "No playable sources found for this episode")

    stream_dicts = [s.to_dict() for s in streams]
    best = allanime.pick_quality(streams, "best")
    selected_url = best.url if best else ""
    referer = best.referer if best else ""
    subs_url = best.subs_url if best else ""

    await asyncio.to_thread(
        _dl.store_sources,
        allanime_id=allanime_id,
        episode=episode,
        mode=mode,
        streams=stream_dicts,
        selected_url=selected_url,
        referer=referer,
        subs_url=subs_url,
    )
    return {
        "cached": False,
        "streams": stream_dicts,
        "selected_url": selected_url,
        "referer": referer,
        "subs_url": subs_url,
    }


# ---------------------------------------------------------------------------
# HLS proxy
#
# Browsers can't set the Referer header that allanime's CDN requires. We solve
# that with a thin proxy:
#   1. /stream/{id}/{ep}/{quality}/master.m3u8 → look up the cached upstream
#      URL, fetch it with the right Referer, rewrite segment URLs to also go
#      through this proxy, and return the rewritten playlist.
#   2. /stream/proxy → for any segment or sub-playlist, fetch with the
#      Referer and pipe the bytes back.
# ---------------------------------------------------------------------------

def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> str:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad).decode("utf-8")


def _rewrite_playlist(playlist_text: str, base_url: str, referer: str, request: Request) -> str:
    """Replace every segment / sub-playlist URI with a proxy URL pinned to `referer`."""
    proxy_base = str(request.url_for("anime_proxy_segment"))
    out_lines: list[str] = []
    for line in playlist_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            # Rewrite URIs inside tag attributes like #EXT-X-MEDIA:...,URI="..."
            def _sub_uri(m: re.Match) -> str:
                inner = m.group(1)
                abs_inner = inner if inner.startswith("http") else urljoin(base_url, inner)
                proxied = f'{proxy_base}?u={_b64url(abs_inner)}&r={_b64url(referer)}'
                return f'URI="{proxied}"'
            out_lines.append(re.sub(r'URI="([^"]+)"', _sub_uri, line))
            continue
        abs_url = stripped if stripped.startswith("http") else urljoin(base_url, stripped)
        out_lines.append(f"{proxy_base}?u={_b64url(abs_url)}&r={_b64url(referer)}")
    return "\n".join(out_lines) + "\n"


@router.get("/stream/{allanime_id}/{episode}/{quality}/master.m3u8")
async def anime_master_playlist(
    allanime_id: str, episode: str, quality: str, request: Request, mode: str = "sub",
):
    """Return the upstream HLS playlist, with all segment URIs rewritten through our proxy."""
    cached = await asyncio.to_thread(_dl.get_cached_sources, allanime_id, episode, mode)
    if not cached or not cached["streams"]:
        # Re-resolve on cache miss
        try:
            streams = await allanime.sources(allanime_id, episode, mode=mode)
        except Exception as exc:
            raise HTTPException(502, f"Could not resolve sources: {exc}")
        if not streams:
            raise HTTPException(404, "No playable sources for episode")
        stream_dicts = [s.to_dict() for s in streams]
        best = allanime.pick_quality(streams, "best")
        await asyncio.to_thread(
            _dl.store_sources,
            allanime_id=allanime_id, episode=episode, mode=mode,
            streams=stream_dicts,
            selected_url=best.url if best else "",
            referer=best.referer if best else "",
            subs_url=best.subs_url if best else "",
        )
        cached = {"streams": stream_dicts, "referer": best.referer if best else "",
                  "subs_url": best.subs_url if best else ""}

    chosen_url = ""
    chosen_referer = cached.get("referer") or ""
    for s in cached["streams"]:
        if quality == "best":
            if s.get("is_hls", True):
                chosen_url = s["url"]; chosen_referer = s.get("referer") or chosen_referer
                break
        elif quality == s.get("quality"):
            chosen_url = s["url"]; chosen_referer = s.get("referer") or chosen_referer
            break
    if not chosen_url:
        chosen_url = cached["streams"][0]["url"]
        chosen_referer = cached["streams"][0].get("referer") or chosen_referer

    headers = {"User-Agent": allanime.USER_AGENT, "Referer": chosen_referer or allanime.ALLANIME_REFR}
    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as c:
        r = await c.get(chosen_url)
        if r.status_code != 200:
            raise HTTPException(502, f"Upstream returned {r.status_code}")
        body = r.text

    if "EXTM3U" in body:
        rewritten = _rewrite_playlist(body, base_url=chosen_url, referer=chosen_referer, request=request)
        return Response(rewritten, media_type="application/vnd.apple.mpegurl")

    # Not an m3u8 — pass through (e.g., direct mp4 redirect)
    return Response(content=r.content, media_type=r.headers.get("content-type", "application/octet-stream"))


@router.get("/stream/proxy", name="anime_proxy_segment")
async def anime_proxy_segment(u: str, request: Request, r: str = ""):
    """Stream a single segment / sub-playlist / MP4 through, with the right Referer.

    Critical for MP4 seeking: forwards the client's `Range` header upstream and
    relays the upstream's 206 Partial Content + Content-Range/Length headers
    back. Without this, the browser can play sequentially but cannot scrub
    past the buffered portion.
    """
    try:
        upstream_url = _b64url_decode(u)
    except Exception:
        raise HTTPException(400, "Bad URL parameter")
    referer = ""
    if r:
        try:
            referer = _b64url_decode(r)
        except Exception:
            referer = ""

    headers = {
        "User-Agent": allanime.USER_AGENT,
        "Referer": referer or allanime.ALLANIME_REFR,
    }
    # Forward Range so MP4 seeking works
    client_range = request.headers.get("range")
    if client_range:
        headers["Range"] = client_range

    # Sniff content type from URL extension; sub-playlists need rewriting (text),
    # everything else is byte-streamed with full Range pass-through.
    if upstream_url.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
    elif upstream_url.endswith(".ts"):
        media_type = "video/mp2t"
    elif upstream_url.endswith(".mp4"):
        media_type = "video/mp4"
    elif upstream_url.endswith(".vtt"):
        media_type = "text/vtt"
    else:
        media_type = "application/octet-stream"

    # Sub-playlist: fetch as text, rewrite inner URIs, return non-streaming.
    if media_type == "application/vnd.apple.mpegurl":
        # Strip Range — playlists are tiny and Range on a text rewrite is meaningless
        text_headers = {k: v for k, v in headers.items() if k.lower() != "range"}
        async with httpx.AsyncClient(headers=text_headers, timeout=20.0, follow_redirects=True) as c:
            r2 = await c.get(upstream_url)
            if r2.status_code != 200:
                raise HTTPException(502, f"Upstream returned {r2.status_code}")
            base = upstream_url
            lines: list[str] = []
            for line in r2.text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    def _sub_uri(m: re.Match) -> str:
                        inner = m.group(1)
                        abs_inner = inner if inner.startswith("http") else urljoin(base, inner)
                        return f'URI="/api/apps/anime/stream/proxy?u={_b64url(abs_inner)}&r={_b64url(referer)}"'
                    lines.append(re.sub(r'URI="([^"]+)"', _sub_uri, line))
                    continue
                abs_url = stripped if stripped.startswith("http") else urljoin(base, stripped)
                lines.append(f"/api/apps/anime/stream/proxy?u={_b64url(abs_url)}&r={_b64url(referer)}")
            return Response("\n".join(lines) + "\n", media_type=media_type)

    # Binary stream (mp4 / ts / vtt / unknown). Open the upstream connection,
    # peek at its status + headers, then stream the body through.
    client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60.0), follow_redirects=True)
    try:
        upstream = await client.send(client.build_request("GET", upstream_url), stream=True)
    except Exception as exc:
        await client.aclose()
        raise HTTPException(502, f"Upstream connection failed: {exc}")

    if upstream.status_code >= 400:
        body_preview = (await upstream.aread())[:300]
        await client.aclose()
        raise HTTPException(502, f"Upstream returned {upstream.status_code}: {body_preview!r}")

    # Pass through critical headers for seeking and progress
    response_headers: dict[str, str] = {"Accept-Ranges": "bytes"}
    for h in ("content-length", "content-range", "last-modified", "etag"):
        v = upstream.headers.get(h)
        if v:
            response_headers[h.title()] = v

    async def _gen():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _gen(),
        status_code=upstream.status_code,  # 200 full or 206 partial
        media_type=media_type,
        headers=response_headers,
    )


# ---------------------------------------------------------------------------
# History / resume
# ---------------------------------------------------------------------------

class RecordWatchRequest(BaseModel):
    allanime_id: str
    title: str
    episode: str
    user_id: str       # required — must be the logged-in user, never hardcoded
    mode: str = "sub"
    position_s: int = 0
    finished: bool = False
    episode_count: int = 0


@router.get("/history")
async def api_history(user_id: str, request: Request, limit: int = 25):
    """Watch history is per-user. user_id is required."""
    user_id = scope_user(request, user_id)
    if not user_id.strip():
        raise HTTPException(400, "user_id is required")
    rows = await asyncio.to_thread(_dl.get_history, user_id, limit)
    return {"history": rows, "count": len(rows)}


@router.post("/history/record")
async def api_record_watch(req: RecordWatchRequest, request: Request):
    req.user_id = scope_user(request, req.user_id)
    title_row = await asyncio.to_thread(
        _dl.upsert_title, req.allanime_id, req.title, req.episode_count
    )
    if not title_row:
        raise HTTPException(500, "Failed to upsert title")
    entry = await asyncio.to_thread(
        _dl.record_watch,
        anime_id=title_row["id"],
        allanime_id=req.allanime_id,
        title=req.title,
        episode=req.episode,
        mode=req.mode,
        user_id=req.user_id,
        position_s=req.position_s,
        finished=req.finished,
    )
    _broadcast("history_updated", id=entry["id"], allanime_id=req.allanime_id)
    return entry


@router.get("/resume")
async def api_resume(user_id: str, request: Request):
    """Most-recent unfinished watch, with the next episode pre-resolved."""
    user_id = scope_user(request, user_id)
    if not user_id.strip():
        raise HTTPException(400, "user_id is required")
    rows = await asyncio.to_thread(_dl.get_history, user_id, 5)
    pending = [r for r in rows if not r.get("finished")]
    if not pending:
        return {"resume": None}
    top = pending[0]
    # Suggest next episode if the last one was finished, else replay current.
    return {"resume": top}


# ---------------------------------------------------------------------------
# Watchlist (per-user favorites)
# ---------------------------------------------------------------------------

class WatchlistAddRequest(BaseModel):
    user_id: str       # required
    allanime_id: str
    title: str
    episode_count: int = 0


@router.get("/watchlist")
async def api_get_watchlist(user_id: str, request: Request):
    user_id = scope_user(request, user_id)
    if not user_id.strip():
        raise HTTPException(400, "user_id is required")
    rows = await asyncio.to_thread(_dl.get_watchlist, user_id)
    return {"watchlist": rows, "count": len(rows)}


@router.get("/watchlist/check/{allanime_id}")
async def api_check_watchlist(allanime_id: str, user_id: str, request: Request):
    """Used by the search/episode-picker UI to render the right star state."""
    user_id = scope_user(request, user_id)
    if not user_id.strip():
        raise HTTPException(400, "user_id is required")
    in_list = await asyncio.to_thread(_dl.is_in_watchlist, user_id, allanime_id)
    return {"in_watchlist": in_list}


@router.post("/watchlist")
async def api_add_watchlist(req: WatchlistAddRequest, request: Request):
    req.user_id = scope_user(request, req.user_id)
    if not req.user_id.strip():
        raise HTTPException(400, "user_id is required")
    entry = await asyncio.to_thread(
        _dl.add_to_watchlist,
        user_id=req.user_id,
        allanime_id=req.allanime_id,
        title=req.title,
        episode_count=req.episode_count,
    )
    _broadcast("watchlist_updated", user_id=req.user_id, allanime_id=req.allanime_id, action="add")
    return entry


@router.delete("/watchlist/{allanime_id}")
async def api_remove_watchlist(allanime_id: str, user_id: str, request: Request):
    user_id = scope_user(request, user_id)
    if not user_id.strip():
        raise HTTPException(400, "user_id is required")
    ok = await asyncio.to_thread(_dl.remove_from_watchlist, user_id, allanime_id)
    if not ok:
        raise HTTPException(404, "Not in watchlist")
    _broadcast("watchlist_updated", user_id=user_id, allanime_id=allanime_id, action="remove")
    return {"removed": allanime_id}
