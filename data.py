"""Anime App — Schema-aware data layer.

All tables live in app_anime schema. No cross-schema foreign keys; user
references stored as plain TEXT.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app_platform.db import (
    fetch_one_in_schema,
    fetch_all_in_schema,
    execute_in_schema,
    execute_returning_in_schema,
)

logger = logging.getLogger(__name__)

SCHEMA = "app_anime"

# Source-cache TTL — allanime tokens & CDN URLs typically last ~10–20 min.
SOURCE_CACHE_TTL_SECONDS = 600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Title cache
# ---------------------------------------------------------------------------

def _title_row(row: dict | None) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "allanime_id": row["allanime_id"],
        "title": row.get("title") or "",
        "episode_count": row.get("episode_count", 0),
        "cover_url": row.get("cover_url") or "",
        "last_seen_at": row["last_seen_at"].isoformat() if row.get("last_seen_at") else "",
    }


def upsert_title(allanime_id: str, title: str, episode_count: int = 0) -> dict:
    """Insert-or-touch an anime title. Returns the local id."""
    existing = fetch_one_in_schema(
        SCHEMA,
        "SELECT * FROM anime_titles WHERE allanime_id = %s",
        (allanime_id,),
    )
    if existing:
        execute_in_schema(
            SCHEMA,
            "UPDATE anime_titles SET title = %s, episode_count = GREATEST(episode_count, %s), "
            "last_seen_at = now() WHERE allanime_id = %s",
            (title, episode_count, allanime_id),
        )
        existing["title"] = title
        existing["episode_count"] = max(existing.get("episode_count", 0), episode_count)
        return _title_row(existing)

    new_id = _gen_id("an")
    row = execute_returning_in_schema(
        SCHEMA,
        "INSERT INTO anime_titles (id, allanime_id, title, episode_count) "
        "VALUES (%s, %s, %s, %s) RETURNING *",
        (new_id, allanime_id, title, episode_count),
    )
    return _title_row(row)


def get_title_by_allanime_id(allanime_id: str) -> dict:
    row = fetch_one_in_schema(
        SCHEMA,
        "SELECT * FROM anime_titles WHERE allanime_id = %s",
        (allanime_id,),
    )
    return _title_row(row)


def get_title(anime_id: str) -> dict:
    row = fetch_one_in_schema(
        SCHEMA, "SELECT * FROM anime_titles WHERE id = %s", (anime_id,)
    )
    return _title_row(row)


# ---------------------------------------------------------------------------
# Watch history
# ---------------------------------------------------------------------------

def _history_row(row: dict | None) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "anime_id": row["anime_id"],
        "allanime_id": row["allanime_id"],
        "title": row.get("title") or "",
        "user_id": row.get("user_id") or "",
        "mode": row.get("mode") or "sub",
        "last_episode": row.get("last_episode") or "",
        "last_position_s": row.get("last_position_s", 0),
        "finished": bool(row.get("finished", False)),
        "last_watched_at": row["last_watched_at"].isoformat() if row.get("last_watched_at") else "",
    }


def record_watch(
    *,
    anime_id: str,
    allanime_id: str,
    title: str,
    episode: str,
    user_id: str,
    mode: str = "sub",
    position_s: int = 0,
    finished: bool = False,
) -> dict:
    """Upsert one watch history row per (user, anime). Updates the last episode + position."""
    existing = fetch_one_in_schema(
        SCHEMA,
        "SELECT * FROM anime_watch_history WHERE user_id = %s AND anime_id = %s",
        (user_id, anime_id),
    )
    if existing:
        row = execute_returning_in_schema(
            SCHEMA,
            "UPDATE anime_watch_history SET last_episode = %s, mode = %s, "
            "last_position_s = %s, finished = %s, last_watched_at = now() "
            "WHERE id = %s RETURNING *",
            (episode, mode, position_s, finished, existing["id"]),
        )
        return _history_row(row)

    new_id = _gen_id("anwh")
    row = execute_returning_in_schema(
        SCHEMA,
        "INSERT INTO anime_watch_history "
        "(id, anime_id, allanime_id, title, user_id, mode, last_episode, last_position_s, finished) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (new_id, anime_id, allanime_id, title, user_id, mode, episode, position_s, finished),
    )
    return _history_row(row)


def get_history(user_id: str, limit: int = 25) -> list[dict]:
    rows = fetch_all_in_schema(
        SCHEMA,
        "SELECT * FROM anime_watch_history WHERE user_id = %s "
        "ORDER BY last_watched_at DESC LIMIT %s",
        (user_id, limit),
    )
    return [_history_row(r) for r in rows]


def get_history_for_anime(allanime_id: str, user_id: str) -> dict:
    row = fetch_one_in_schema(
        SCHEMA,
        "SELECT * FROM anime_watch_history WHERE user_id = %s AND allanime_id = %s",
        (user_id, allanime_id),
    )
    return _history_row(row)


def delete_history_entry(history_id: str, user_id: str) -> bool:
    n = execute_in_schema(
        SCHEMA,
        "DELETE FROM anime_watch_history WHERE id = %s AND user_id = %s",
        (history_id, user_id),
    )
    return n > 0


# ---------------------------------------------------------------------------
# Source cache
# ---------------------------------------------------------------------------

def _cache_key(allanime_id: str, episode: str, mode: str) -> str:
    return f"{allanime_id}:{episode}:{mode}"


def get_cached_sources(allanime_id: str, episode: str, mode: str) -> dict | None:
    """Return cached source bundle if not expired, else None."""
    row = fetch_one_in_schema(
        SCHEMA,
        "SELECT * FROM anime_source_cache WHERE cache_key = %s AND expires_at > now()",
        (_cache_key(allanime_id, episode, mode),),
    )
    if not row:
        return None
    try:
        streams = json.loads(row["streams_json"])
    except (json.JSONDecodeError, TypeError):
        streams = []
    return {
        "streams": streams,
        "selected_url": row.get("selected_url") or "",
        "referer": row.get("referer") or "",
        "subs_url": row.get("subs_url") or "",
    }


def store_sources(
    *,
    allanime_id: str,
    episode: str,
    mode: str,
    streams: list[dict],
    selected_url: str = "",
    referer: str = "",
    subs_url: str = "",
) -> None:
    """Cache resolved sources with a short TTL."""
    key = _cache_key(allanime_id, episode, mode)
    expires = _now() + timedelta(seconds=SOURCE_CACHE_TTL_SECONDS)
    execute_in_schema(
        SCHEMA,
        "INSERT INTO anime_source_cache "
        "(cache_key, streams_json, selected_url, referer, subs_url, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (cache_key) DO UPDATE SET "
        "streams_json = EXCLUDED.streams_json, selected_url = EXCLUDED.selected_url, "
        "referer = EXCLUDED.referer, subs_url = EXCLUDED.subs_url, "
        "expires_at = EXCLUDED.expires_at",
        (key, json.dumps(streams), selected_url, referer, subs_url, expires),
    )


def purge_expired_cache() -> int:
    return execute_in_schema(
        SCHEMA, "DELETE FROM anime_source_cache WHERE expires_at < now()", ()
    )


# ---------------------------------------------------------------------------
# Watchlist (per-user favorites, with progress join)
# ---------------------------------------------------------------------------

def _watchlist_row(row: dict | None) -> dict:
    if not row:
        return {}
    return {
        "id": row["id"],
        "user_id": row.get("user_id") or "",
        "allanime_id": row["allanime_id"],
        "title": row.get("title") or "",
        "episode_count": row.get("episode_count", 0),
        "cover_url": row.get("cover_url") or "",
        "sort_order": row.get("sort_order", 0),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else "",
        # Progress fields come from the LEFT JOIN against anime_watch_history
        "last_episode": row.get("last_episode") or "",
        "last_position_s": row.get("last_position_s", 0),
        "finished": bool(row.get("finished", False)),
        "mode": row.get("mode") or "sub",
        "last_watched_at": row["last_watched_at"].isoformat() if row.get("last_watched_at") else "",
    }


def add_to_watchlist(*, user_id: str, allanime_id: str, title: str, episode_count: int = 0) -> dict:
    """Add a show to the user's watchlist. No-op if already present."""
    existing = fetch_one_in_schema(
        SCHEMA,
        "SELECT * FROM anime_watchlist WHERE user_id = %s AND allanime_id = %s",
        (user_id, allanime_id),
    )
    if existing:
        # Update the title/episode_count in case they've changed since add
        if existing.get("title") != title or (episode_count and episode_count > existing.get("episode_count", 0)):
            execute_in_schema(
                SCHEMA,
                "UPDATE anime_watchlist SET title = %s, "
                "episode_count = GREATEST(episode_count, %s) "
                "WHERE id = %s",
                (title, episode_count, existing["id"]),
            )
            existing["title"] = title
            existing["episode_count"] = max(existing.get("episode_count", 0), episode_count)
        return _watchlist_row(existing)

    new_id = _gen_id("anwl")
    row = execute_returning_in_schema(
        SCHEMA,
        "INSERT INTO anime_watchlist (id, user_id, allanime_id, title, episode_count) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (new_id, user_id, allanime_id, title, episode_count),
    )
    return _watchlist_row(row)


def remove_from_watchlist(user_id: str, allanime_id: str) -> bool:
    n = execute_in_schema(
        SCHEMA,
        "DELETE FROM anime_watchlist WHERE user_id = %s AND allanime_id = %s",
        (user_id, allanime_id),
    )
    return n > 0


def is_in_watchlist(user_id: str, allanime_id: str) -> bool:
    row = fetch_one_in_schema(
        SCHEMA,
        "SELECT 1 FROM anime_watchlist WHERE user_id = %s AND allanime_id = %s",
        (user_id, allanime_id),
    )
    return row is not None


def get_watchlist(user_id: str) -> list[dict]:
    """Return the user's watchlist with progress fields joined from history.

    Sorted by:
      1. unfinished items first (in-progress on top)
      2. then by sort_order (manual)
      3. then by most-recently added
    """
    rows = fetch_all_in_schema(
        SCHEMA,
        """
        SELECT
            wl.*,
            wh.last_episode,
            wh.last_position_s,
            wh.finished,
            wh.mode,
            wh.last_watched_at
        FROM anime_watchlist wl
        LEFT JOIN anime_watch_history wh
            ON wh.user_id = wl.user_id
           AND wh.allanime_id = wl.allanime_id
        WHERE wl.user_id = %s
        ORDER BY
            CASE WHEN wh.finished IS NOT TRUE AND wh.last_episode IS NOT NULL THEN 0 ELSE 1 END,
            wl.sort_order,
            wl.created_at DESC
        """,
        (user_id,),
    )
    return [_watchlist_row(r) for r in rows]
