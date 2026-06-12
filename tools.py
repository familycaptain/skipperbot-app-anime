"""Anime App MCP tools.

These tools are invoked by the chat agent. The pattern (per existing apps
like locator and auto): when an action should open a new app tab, the tool
returns text instructing the agent to call the platform's `open_app`
meta-tool with the app id and context. The frontend's WebSocket handler
routes that to onOpenApp(...).
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from apps.anime import allanime, data as _dl

logger = logging.getLogger(__name__)


def _run(coro):
    """Run an async function from sync MCP-tool context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If we're already inside a loop (FastAPI thread), spin up a worker thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Search / browse
# ---------------------------------------------------------------------------

def anime_search(query: str, mode: str = "sub") -> str:
    """Search the allanime.day catalog for anime by name.

    Use this when the user says things like "find one piece anime",
    "search for attack on titan", "any anime with the word demon".

    Args:
        query: The anime name or partial title to search for.
        mode: "sub" for subtitled, "dub" for English dubbed. Defaults to "sub".

    Returns:
        Numbered list of matching anime with their IDs and episode counts.

    Ack: Searching anime catalog for "{query}"...
    """
    if not query.strip():
        return "Error: query is required."
    if mode not in ("sub", "dub"):
        mode = "sub"
    try:
        results = _run(allanime.search(query.strip(), mode=mode))
    except Exception as exc:
        return f"Error searching anime: {exc}"

    if not results:
        return f"No anime found for '{query}' ({mode})."

    lines = [f"Found {len(results)} anime ({mode}):\n"]
    for r in results[:25]:
        lines.append(f"• **{r.title}** — {r.episode_count} episodes — id: `{r.allanime_id}`")
    if len(results) > 25:
        lines.append(f"\n…{len(results) - 25} more not shown.")
    lines.append(f"\nTo play: anime_play(allanime_id, episode, mode=\"{mode}\")")
    return "\n".join(lines)


def anime_episodes(allanime_id: str, mode: str = "sub") -> str:
    """List the available episodes for an anime.

    Args:
        allanime_id: The allanime.day show ID (from anime_search results).
        mode: "sub" or "dub". Defaults to "sub".

    Returns:
        Comma-separated episode numbers, or first/last if there are many.

    Ack: Loading episodes...
    """
    if mode not in ("sub", "dub"):
        mode = "sub"
    try:
        eps = _run(allanime.episodes(allanime_id, mode=mode))
    except Exception as exc:
        return f"Error fetching episodes: {exc}"

    if not eps:
        return f"No {mode} episodes found for {allanime_id}."

    if len(eps) <= 30:
        ep_str = ", ".join(eps)
    else:
        ep_str = ", ".join(eps[:10]) + f", … , {eps[-5]}, {eps[-4]}, {eps[-3]}, {eps[-2]}, {eps[-1]}"

    return f"{len(eps)} {mode} episode(s) available for `{allanime_id}`:\n{ep_str}"


# ---------------------------------------------------------------------------
# Play / resume
# ---------------------------------------------------------------------------

def anime_play(
    allanime_id: str,
    episode: str = "1",
    mode: str = "sub",
    title: str = "",
) -> str:
    """Resolve a playable stream for an episode and open it in the in-app player.

    Use this when the user says things like "play attack on titan episode 5",
    "start watching jujutsu kaisen", "play episode 3 of one piece dub".

    The stream is resolved synchronously (so we return early on errors) and
    cached for ~10 minutes; the player loads the cached URL via the HLS proxy.

    Args:
        allanime_id: The allanime.day show ID.
        episode: Episode number to play (e.g. "1", "12.5"). Defaults to "1".
        mode: "sub" or "dub". Defaults to "sub".
        title: Display title for the player tab. Optional — falls back to the show id.

    Returns:
        Confirmation, then an instruction for the agent to call open_app.

    Ack: Resolving streams for episode {episode}...
    """
    if mode not in ("sub", "dub"):
        mode = "sub"
    episode = str(episode).strip() or "1"

    try:
        streams = _run(allanime.sources(allanime_id, episode, mode=mode))
    except Exception as exc:
        return f"Error resolving sources: {exc}"

    if not streams:
        return (
            f"Could not find a playable source for {allanime_id} ep {episode} ({mode}). "
            "The episode may not be released yet, or all providers failed."
        )

    best = allanime.pick_quality(streams, "best")
    stream_dicts = [s.to_dict() for s in streams]

    # Cache resolved sources so the player can fetch them without re-decrypting.
    try:
        _dl.store_sources(
            allanime_id=allanime_id,
            episode=episode,
            mode=mode,
            streams=stream_dicts,
            selected_url=best.url if best else "",
            referer=best.referer if best else "",
            subs_url=best.subs_url if best else "",
        )
    except Exception as exc:
        logger.warning("anime_play: source cache write failed: %s", exc)

    # Touch title cache so resume works even if user navigates away
    try:
        _dl.upsert_title(allanime_id, title or allanime_id, 0)
    except Exception as exc:
        logger.warning("anime_play: title upsert failed: %s", exc)

    qualities = sorted(
        {s.quality for s in streams if s.quality.isdigit()},
        key=lambda q: int(q),
        reverse=True,
    )
    quality_str = ", ".join(qualities) if qualities else "auto"
    display_title = title or allanime_id

    return (
        f"Resolved **{display_title}** episode {episode} ({mode}) — qualities: {quality_str}.\n\n"
        f"Now call open_app(app_type=\"anime-player\", "
        f"animeId=\"{allanime_id}\", episode=\"{episode}\", "
        f"mode=\"{mode}\", title=\"{display_title}\") to start playback."
    )


def anime_resume(user_id: str) -> str:
    """Resume the most recently watched anime for a specific user.

    Use when the user says "what was I watching", "continue my anime",
    "resume watching", or "play next episode".

    Args:
        user_id: REQUIRED. The lowercase name of the person who is currently
            chatting (e.g. "alice", "bob", "kid1"). Watch history is
            per-user; never guess or hardcode — pass the active chat user.

    Returns:
        Resolves the next episode (or replays the unfinished one) and
        instructs the agent to call open_app for the player.

    Ack: Looking up your last anime...
    """
    if not user_id or not user_id.strip():
        return "Error: user_id is required (the active chat user's name)."
    try:
        history = _dl.get_history(user_id, limit=5)
    except Exception as exc:
        return f"Error reading watch history: {exc}"
    if not history:
        return "No anime watch history yet. Use anime_search to find something to watch."

    pending = next((h for h in history if not h["finished"]), history[0])
    next_ep = pending["last_episode"]
    if pending["finished"]:
        # Try to advance to the next episode
        try:
            eps = _run(allanime.episodes(pending["allanime_id"], mode=pending["mode"]))
            if pending["last_episode"] in eps:
                idx = eps.index(pending["last_episode"])
                if idx + 1 < len(eps):
                    next_ep = eps[idx + 1]
        except Exception as exc:
            logger.warning("anime_resume: could not advance episode: %s", exc)

    return anime_play(
        allanime_id=pending["allanime_id"],
        episode=next_ep,
        mode=pending["mode"],
        title=pending["title"],
    )


def anime_watchlist(user_id: str) -> str:
    """Show a user's anime watchlist — the shows they manually saved as favorites.

    The watchlist is per-user; each entry shows progress (current episode and
    sub/dub mode) when the user has watched at least one episode.

    Args:
        user_id: REQUIRED. The lowercase name of the person currently chatting.

    Returns:
        Bulleted list of saved shows with their current episode progress.

    Ack: Loading watchlist...
    """
    if not user_id or not user_id.strip():
        return "Error: user_id is required (the active chat user's name)."
    try:
        rows = _dl.get_watchlist(user_id)
    except Exception as exc:
        return f"Error reading watchlist: {exc}"
    if not rows:
        return f"{user_id}'s watchlist is empty. Use anime_watchlist_add() to save a show."

    lines = [f"{user_id}'s watchlist ({len(rows)}):\n"]
    for r in rows:
        if r["last_episode"]:
            status = "✓ finished" if r["finished"] else f"▶ ep {r['last_episode']} ({r['mode']})"
        else:
            status = "○ not started"
        lines.append(f"• **{r['title']}** — {r['episode_count']} eps — {status} — `{r['allanime_id']}`")
    return "\n".join(lines)


def anime_watchlist_add(user_id: str, allanime_id: str, title: str = "") -> str:
    """Add an anime to the user's watchlist (favorites).

    Use when the user says "add X to my watchlist", "save X for later",
    "favorite X anime", or similar.

    Args:
        user_id: REQUIRED. The lowercase name of the person currently chatting.
        allanime_id: The allanime.day show ID (from anime_search results).
        title: Display title to store with the watchlist entry.

    Returns:
        Confirmation.

    Ack: Adding to watchlist...
    """
    if not user_id or not user_id.strip():
        return "Error: user_id is required (the active chat user's name)."
    if not allanime_id.strip():
        return "Error: allanime_id is required."
    try:
        entry = _dl.add_to_watchlist(
            user_id=user_id, allanime_id=allanime_id,
            title=title or allanime_id, episode_count=0,
        )
    except Exception as exc:
        return f"Error adding to watchlist: {exc}"
    return f"Added **{entry['title']}** to {user_id}'s watchlist."


def anime_watchlist_remove(user_id: str, allanime_id: str) -> str:
    """Remove an anime from the user's watchlist.

    Args:
        user_id: REQUIRED. The lowercase name of the person currently chatting.
        allanime_id: The allanime.day show ID to remove.

    Returns:
        Confirmation.

    Ack: Removing from watchlist...
    """
    if not user_id or not user_id.strip():
        return "Error: user_id is required (the active chat user's name)."
    try:
        ok = _dl.remove_from_watchlist(user_id, allanime_id)
    except Exception as exc:
        return f"Error removing from watchlist: {exc}"
    if not ok:
        return f"`{allanime_id}` was not in {user_id}'s watchlist."
    return f"Removed `{allanime_id}` from {user_id}'s watchlist."


def anime_history(user_id: str, limit: int = 10) -> str:
    """Show recent anime watch history for a specific user.

    Args:
        user_id: REQUIRED. The lowercase name of the person currently chatting
            (e.g. "alice", "bob", "kid1"). Watch history is per-user;
            never guess or hardcode — pass the active chat user.
        limit: How many entries to return. Defaults to 10.

    Returns:
        Bulleted list of recent watches.

    Ack: Loading watch history...
    """
    if not user_id or not user_id.strip():
        return "Error: user_id is required (the active chat user's name)."
    try:
        rows = _dl.get_history(user_id, limit=limit)
    except Exception as exc:
        return f"Error reading history: {exc}"
    if not rows:
        return "No watch history yet."

    lines = [f"Recent anime ({len(rows)}):\n"]
    for r in rows:
        status = "✓ finished" if r["finished"] else "▶ in progress"
        lines.append(
            f"• **{r['title']}** — ep {r['last_episode']} ({r['mode']}) — "
            f"{status} — `{r['allanime_id']}`"
        )
    lines.append("\nUse anime_resume() to continue, or anime_play(id, episode) to pick a specific one.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cast to a Home Assistant media device (Chromecast, Roku, LG TV's cast input)
# ---------------------------------------------------------------------------

def _ha_call(path: str, method: str = "POST", payload: dict | None = None) -> tuple[int, str]:
    """Minimal HA REST call for the cast tool. Returns (status_code, body)."""
    base = (os.getenv("HOME_ASSISTANT_URL") or os.getenv("HA_URL") or "").strip().rstrip("/")
    token = (os.getenv("HOME_ASSISTANT_TOKEN") or os.getenv("HA_TOKEN") or "").strip()
    if not base or not token:
        raise RuntimeError("HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN must be set in .env")
    if base.endswith("/api"):
        base = base[:-4]
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def cast_anime_episode(
    allanime_id: str,
    episode: str,
    device: str = "chromecast",
    mode: str = "sub",
    quality: str = "best",
) -> str:
    """Resolve an anime episode and play it on a Home Assistant media device.

    Single-hop convenience for "play one piece episode 5" (defaults to the
    chromecast) or "play attack on titan ep 3 on the roku" (explicit device).
    Resolves the playable HLS URL via Skipper's anime proxy, looks up the
    device by alias in apps/automation (so "tv", "chromecast", "roku" work),
    and calls Home Assistant's media_player.play_media service.

    Device requirements:
      - Chromecast: works out of the box (DEFAULT — used when the user
        doesn't name a device)
      - Roku: requires the "Roku Media Player" channel to be installed
      - LG webOS TV: use its built-in Chromecast input (the OLED C2/C3 expose
        a separate Cast device in HA), NOT the webostv entity, which can't
        play arbitrary URLs

    Args:
        allanime_id: The allanime.day show ID (from anime_search results).
        episode: Episode number (e.g. "5", "12.5"). Defaults to "1".
        device: Friendly name/alias of the HA media device. Defaults to
            "chromecast" — only override when the user explicitly says
            "on the roku", "on the tv", etc.
        mode: "sub" or "dub". Defaults to "sub".
        quality: "best" / "worst" / "1080" / "720" / "480" / "auto". Defaults
            to "best".

    Returns:
        Confirmation, or an actionable error if the device or stream couldn't
        be resolved.

    Ack: Casting episode {episode} to {device}...
    """
    if mode not in ("sub", "dub"):
        mode = "sub"
    episode = str(episode).strip() or "1"
    device = (device or "").strip()
    if not device:
        return "Error: device alias is required (e.g. 'tv', 'chromecast', 'roku')."

    # Cast targets are on the LAN — prefer the LAN URL so streaming doesn't
    # have to hairpin through ngrok / a public CDN. SKIPPER_PUBLIC_URL stays
    # the fallback for setups without a separate LAN endpoint.
    from app_platform import settings as _settings
    base_url = (
        (_settings.get("lan_url", scope="platform", default="")
         or _settings.get("public_url", scope="platform", default="")
         or "")
        .strip()
        .rstrip("/")
    )
    if not base_url:
        return (
            "Error: no LAN/Public URL is set (Settings → System, or SKIPPER_LAN_URL / "
            "SKIPPER_PUBLIC_URL in .env). The cast device needs an absolute, "
            "network-reachable URL to fetch the stream."
        )

    # 1. Resolve sources to confirm the episode plays at all + warm the proxy cache
    try:
        streams = _run(allanime.sources(allanime_id, episode, mode=mode))
    except Exception as exc:
        return f"Error resolving anime sources: {exc}"
    if not streams:
        return f"No playable source for {allanime_id} ep {episode} ({mode})."

    best = allanime.pick_quality(streams, quality)
    if not best:
        return f"Could not pick a stream matching quality '{quality}'."

    # Cache so the master.m3u8 proxy can serve immediately
    try:
        _dl.store_sources(
            allanime_id=allanime_id, episode=episode, mode=mode,
            streams=[s.to_dict() for s in streams],
            selected_url=best.url, referer=best.referer, subs_url=best.subs_url,
        )
    except Exception as exc:
        logger.warning("cast_anime_episode: source cache write failed: %s", exc)

    # 2. Find the HA device + a media_player entity on it. Casting reaches into
    #    the OPTIONAL Automation app — guard the import so Anime works standalone.
    try:
        from apps.automation import devices as _ha_dev
    except ImportError:
        return ("Casting needs the optional Automation (Home Assistant) app, which "
                "isn't installed. Install skipperbot-app-automation to enable casting.")
    try:
        _ha_dev.warm_entities_cache_if_empty()
        match = _ha_dev.find_device(device)
    except Exception as exc:
        return f"Error looking up device alias: {exc}"
    if not match:
        return (
            f"No Home Assistant device matches alias '{device}'. "
            "Use find_home_device to see what's known, or add an alias to "
            "apps/automation/devices.json."
        )
    device_id, device_meta = match
    entities = _ha_dev.get_entities_for_device(device_id)
    media_entities = [e for e in entities if e.get("domain") == "media_player"]
    if not media_entities:
        domains = sorted({e.get("domain") for e in entities if e.get("domain")})
        return (
            f"Device '{device_meta['name']}' has no media_player entity "
            f"(found domains: {', '.join(domains) or 'none'}). "
            "If this is an LG TV, the webostv entity can't play arbitrary URLs — "
            "use the TV's built-in Chromecast input instead (it should appear as a "
            "separate Cast device in HA)."
        )
    target_entity = media_entities[0]["entity_id"]

    # 3. Build the absolute proxy URL the cast device will fetch
    quality_segment = quality if quality and quality != "auto" else "best"
    stream_url = (
        f"{base_url}/api/apps/anime/stream/"
        f"{urllib.parse.quote(allanime_id, safe='')}/"
        f"{urllib.parse.quote(episode, safe='')}/"
        f"{urllib.parse.quote(quality_segment, safe='')}/"
        f"master.m3u8?mode={mode}"
    )

    # 4. Call media_player.play_media on HA
    payload = {
        "entity_id": target_entity,
        "media_content_id": stream_url,
        "media_content_type": "application/x-mpegURL",
    }
    logger.info(
        "ANIME CAST: entity=%s url=%s base_source=%s",
        target_entity, stream_url,
        "SKIPPER_LAN_URL" if os.getenv("SKIPPER_LAN_URL") else "SKIPPER_PUBLIC_URL",
    )
    try:
        status, body = _ha_call("/api/services/media_player/play_media", "POST", payload)
    except Exception as exc:
        logger.error("ANIME CAST: HA call exception: %s", exc)
        return f"Error calling Home Assistant: {exc}"
    logger.info("ANIME CAST: HA responded status=%s body=%s", status, body[:300])
    if status >= 400:
        return (
            f"Home Assistant rejected the cast call (HTTP {status}).\n"
            f"  device: {device_meta['name']} ({target_entity})\n"
            f"  url:    {stream_url}\n"
            f"  body:   {body[:400]}"
        )

    return (
        f"OK — casting **{device_meta['name']}** episode {episode} ({mode}, {quality}) to {target_entity}.\n"
        f"  Stream URL: {stream_url}\n"
        f"  HA accepted the request (HTTP {status}). If playback doesn't start, "
        f"the cast device couldn't reach the URL or rejected the content type — "
        f"check Home Assistant's own logs for cast-side errors."
    )
