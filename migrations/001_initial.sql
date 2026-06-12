-- Anime App — Initial migration
-- All tables in app_anime schema (created automatically by app loader).

-- ============================================================================
-- ANIME TITLES (catalog cache: only titles the user has interacted with)
-- ============================================================================
-- We don't try to mirror allanime.day. We only persist titles the user has
-- searched or played, so that history and resume work offline and the
-- "currently watching" list doesn't depend on an external lookup.

CREATE TABLE IF NOT EXISTS anime_titles (
    id              TEXT PRIMARY KEY,                -- "an-<slug>" — local app id
    allanime_id     TEXT NOT NULL UNIQUE,            -- _id from allanime.day
    title           TEXT NOT NULL,
    episode_count   INTEGER NOT NULL DEFAULT 0,
    cover_url       TEXT NOT NULL DEFAULT '',
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_anime_titles_allanime ON anime_titles (allanime_id);


-- ============================================================================
-- WATCH HISTORY (one row per (anime, user) — last episode watched)
-- ============================================================================

CREATE TABLE IF NOT EXISTS anime_watch_history (
    id              TEXT PRIMARY KEY,                -- "anwh-<uuid>"
    anime_id        TEXT NOT NULL,                   -- references anime_titles.id (soft, no FK per spec)
    allanime_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'user',
    mode            TEXT NOT NULL DEFAULT 'sub'      -- 'sub' or 'dub'
                    CHECK (mode IN ('sub', 'dub')),
    last_episode    TEXT NOT NULL,                   -- episode number (string — ani-cli uses strings like "12.5")
    last_position_s INTEGER NOT NULL DEFAULT 0,      -- playback position in seconds
    finished        BOOLEAN NOT NULL DEFAULT FALSE,  -- true once user marks the episode complete
    last_watched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_anime_watch_user_anime ON anime_watch_history (user_id, anime_id);
CREATE INDEX IF NOT EXISTS idx_anime_watch_recent ON anime_watch_history (user_id, last_watched_at DESC);


-- ============================================================================
-- SOURCE CACHE (resolved HLS URLs, short TTL — allanime tokens expire)
-- ============================================================================
-- Resolving a source requires GraphQL + AES decrypt + provider fetch.
-- We cache the resolved URL for ~10 minutes so re-clicking an episode
-- doesn't re-do the whole pipeline. The HLS proxy reads from here.

CREATE TABLE IF NOT EXISTS anime_source_cache (
    cache_key       TEXT PRIMARY KEY,                -- "<allanime_id>:<ep>:<mode>"
    streams_json    TEXT NOT NULL,                   -- JSON: [{quality, url, referer, subs?}]
    selected_url    TEXT NOT NULL DEFAULT '',        -- url chosen for "best"
    referer         TEXT NOT NULL DEFAULT '',        -- referer to attach to upstream requests
    subs_url        TEXT NOT NULL DEFAULT '',        -- english subtitle URL if soft-subbed
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_anime_source_expires ON anime_source_cache (expires_at);
