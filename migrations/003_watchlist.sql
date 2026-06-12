-- Anime App — Watchlist (favorites) per user.
-- Unlike anime_watch_history (which is implicit, populated when you play
-- something), the watchlist is explicit: the user adds shows by hand. The UI
-- joins watchlist rows against watch_history to show "ep X" progress per item.

CREATE TABLE IF NOT EXISTS anime_watchlist (
    id              TEXT PRIMARY KEY,                -- "anwl-<uuid>"
    user_id         TEXT NOT NULL,
    allanime_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    episode_count   INTEGER NOT NULL DEFAULT 0,
    cover_url       TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 0,      -- user-controlled ordering (lowest first)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_anime_watchlist_user_anime
    ON anime_watchlist (user_id, allanime_id);

CREATE INDEX IF NOT EXISTS idx_anime_watchlist_user_recent
    ON anime_watchlist (user_id, sort_order, created_at DESC);
