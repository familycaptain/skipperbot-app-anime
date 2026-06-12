import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Tv, Search, History, PlayCircle, Loader2, RefreshCw, ChevronLeft,
  Bookmark, BookmarkCheck, Trash2, ListVideo,
} from "lucide-react";

/**
 * Anime App — search the allanime catalog, pick an episode, hand off to
 * the AnimePlayerApp tab. Mirrors the meals/scriptures shell pattern.
 *
 * Props: appId, userId, context, onTitle, onOpenApp
 */

const API = "/api/apps/anime";

const btnPrimary = "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded bg-teal-600 hover:bg-teal-500 text-white disabled:opacity-50 disabled:hover:bg-teal-600";
const btnGhost = "inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 disabled:opacity-50";
const inp = "w-full text-sm bg-zinc-900 border border-zinc-700 rounded px-3 py-2 text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-teal-500";
const card = "p-3 rounded border border-zinc-800 bg-zinc-900/60 hover:border-zinc-700 transition";

const TABS = [
  { id: "browse",    label: "Browse",    Icon: Search },
  { id: "watchlist", label: "Watchlist", Icon: Bookmark },
  { id: "history",   label: "History",   Icon: History },
];

// Shared: in-memory cache of which allanime_ids are in the current user's
// watchlist. Lets BrowseTab and EpisodePicker render the right star state
// without a per-row API call. Refreshed by WatchlistTab and the SSE feed.
function useWatchlistSet(userId) {
  const [set, setSet] = useState(() => new Set());
  const reload = useCallback(async () => {
    if (!userId) return;
    try {
      const res = await fetch(`${API}/watchlist?user_id=${encodeURIComponent(userId)}`);
      const data = await res.json();
      setSet(new Set((data.watchlist || []).map(r => r.allanime_id)));
    } catch {/* ignore */}
  }, [userId]);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    const es = new EventSource(`${API}/events`);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === "watchlist_updated" && ev.user_id === userId) reload();
      } catch {/* ignore */}
    };
    return () => es.close();
  }, [reload, userId]);
  return [set, reload];
}

async function toggleWatchlist({ userId, anime, currentlyIn }) {
  if (currentlyIn) {
    await fetch(`${API}/watchlist/${encodeURIComponent(anime.allanime_id)}?user_id=${encodeURIComponent(userId)}`, { method: "DELETE" });
  } else {
    await fetch(`${API}/watchlist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        allanime_id: anime.allanime_id,
        title: anime.title,
        episode_count: anime.episode_count || 0,
      }),
    });
  }
}


export default function AnimeApp({ userId, context = {}, onTitle, onOpenApp }) {
  const [tab, setTab] = useState("browse");
  const [picked, setPicked] = useState(null);  // lifted out of BrowseTab so other tabs (and the Player) can jump straight to the episode picker
  const [watchlistSet, reloadWatchlist] = useWatchlistSet(userId);

  useEffect(() => { if (onTitle) onTitle("Anime"); }, [onTitle]);

  // External jump-to-episodes (e.g. from AnimePlayerApp's "Episodes" button,
  // which calls onOpenApp("anime", { showEpisodesFor: { allanime_id, title, episode_count, mode } }))
  useEffect(() => {
    if (context?.showEpisodesFor?.allanime_id) {
      setTab("browse");
      setPicked(context.showEpisodesFor);
    }
  }, [context?.showEpisodesFor]);

  // Internal jump-to-episodes used by Watchlist/History rows
  const showEpisodes = useCallback((anime) => {
    setPicked({
      allanime_id: anime.allanime_id,
      title: anime.title,
      episode_count: anime.episode_count || 0,
      mode: anime.mode || "sub",
    });
    setTab("browse");
  }, []);

  return (
    <div className="flex flex-col h-full w-full bg-zinc-950 text-zinc-100">
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-zinc-800 bg-zinc-900/40 shrink-0">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => { setTab(id); if (id !== "browse") setPicked(null); }}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded ${
              tab === id ? "bg-teal-600 text-white" : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        {tab === "browse"    && <BrowseTab    userId={userId} onOpenApp={onOpenApp} watchlistSet={watchlistSet} reloadWatchlist={reloadWatchlist} picked={picked} setPicked={setPicked} />}
        {tab === "watchlist" && <WatchlistTab userId={userId} onOpenApp={onOpenApp} reloadWatchlist={reloadWatchlist} onShowEpisodes={showEpisodes} />}
        {tab === "history"   && <HistoryTab   userId={userId} onOpenApp={onOpenApp} onShowEpisodes={showEpisodes} />}
      </div>
    </div>
  );
}


// ════════════════════════════════════════════════════════════════════════════
//  Browse: search → results → episode picker
// ════════════════════════════════════════════════════════════════════════════

function BrowseTab({ userId, onOpenApp, watchlistSet, reloadWatchlist, picked, setPicked }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState(picked?.mode || "sub");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");

  const onSearch = useCallback(async (e) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setError("");
    setResults([]);
    setPicked(null);
    try {
      const res = await fetch(`${API}/search?q=${encodeURIComponent(query.trim())}&mode=${mode}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Search failed");
      setResults(data.results || []);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSearching(false);
    }
  }, [query, mode]);

  if (picked) {
    return (
      <EpisodePicker
        anime={picked}
        mode={picked.mode || mode}
        userId={userId}
        onBack={() => setPicked(null)}
        onOpenApp={onOpenApp}
        watchlistSet={watchlistSet}
        reloadWatchlist={reloadWatchlist}
      />
    );
  }

  return (
    <div className="space-y-4 max-w-3xl h-full">
      <form onSubmit={onSearch} className="flex gap-2">
        <input
          type="text"
          autoFocus
          placeholder="Search anime…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className={inp}
        />
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          className="text-sm bg-zinc-900 border border-zinc-700 rounded px-2 text-zinc-100"
        >
          <option value="sub">Sub</option>
          <option value="dub">Dub</option>
        </select>
        <button type="submit" className={btnPrimary} disabled={searching || !query.trim()}>
          {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Search
        </button>
      </form>

      {error && (
        <div className="p-3 rounded border border-red-900/60 bg-red-950/40 text-sm text-red-200">
          {error}
        </div>
      )}

      {!error && !searching && results.length === 0 && query && (
        <div className="text-sm text-zinc-500">No results.</div>
      )}

      <div className="grid gap-2">
        {results.map((r) => {
          const inList = watchlistSet?.has(r.allanime_id);
          return (
            <div
              key={r.allanime_id}
              className={`${card} flex items-center justify-between gap-3`}
            >
              <button onClick={() => setPicked(r)} className="flex-1 min-w-0 text-left">
                <div className="font-medium text-zinc-100 truncate">{r.title}</div>
                <div className="text-xs text-zinc-500 mt-0.5">{mode}</div>
              </button>
              <span
                className="shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-teal-900/40 border border-teal-800/60 text-teal-200 tabular-nums"
                title={`${r.episode_count} episodes`}
              >
                {r.episode_count} ep{r.episode_count === 1 ? "" : "s"}
              </span>
              <button
                onClick={async () => {
                  await toggleWatchlist({ userId, anime: r, currentlyIn: inList });
                  reloadWatchlist?.();
                }}
                className={`shrink-0 p-1.5 rounded hover:bg-zinc-800 ${inList ? "text-amber-400" : "text-zinc-500"}`}
                title={inList ? "Remove from watchlist" : "Add to watchlist"}
              >
                {inList ? <BookmarkCheck className="w-5 h-5" /> : <Bookmark className="w-5 h-5" />}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}


const EPISODE_BATCH_SIZE = 100;

function EpisodePicker({ anime, mode, userId, onBack, onOpenApp, watchlistSet, reloadWatchlist }) {
  const inList = watchlistSet?.has(anime.allanime_id);
  const [episodes, setEpisodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [resolvingEp, setResolvingEp] = useState(null);
  const [activeMode, setActiveMode] = useState(mode);
  const [batchIdx, setBatchIdx] = useState(0);

  // Chunk the sorted episode list into batches of EPISODE_BATCH_SIZE.
  // Each batch is labeled by its first–last actual episode numbers (handles
  // fractional eps like "12.5" gracefully — the label uses real values).
  const batches = useMemo(() => {
    const out = [];
    for (let i = 0; i < episodes.length; i += EPISODE_BATCH_SIZE) {
      const slice = episodes.slice(i, i + EPISODE_BATCH_SIZE);
      out.push({
        eps: slice,
        label: slice.length === 1 ? slice[0] : `${slice[0]}–${slice[slice.length - 1]}`,
      });
    }
    return out;
  }, [episodes]);

  // Reset to first batch whenever the underlying episode list changes.
  useEffect(() => { setBatchIdx(0); }, [episodes]);

  const visibleEpisodes = batches[batchIdx]?.eps || [];

  const loadEpisodes = useCallback(async (m = activeMode) => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API}/episodes/${encodeURIComponent(anime.allanime_id)}?mode=${m}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Episode fetch failed");
      setEpisodes(data.episodes || []);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [anime.allanime_id, activeMode]);

  useEffect(() => { loadEpisodes(activeMode); }, [loadEpisodes, activeMode]);

  async function handlePlay(ep) {
    setResolvingEp(ep);
    try {
      // Pre-warm the source cache so the player loads instantly.
      const res = await fetch(`${API}/sources/${encodeURIComponent(anime.allanime_id)}/${encodeURIComponent(ep)}?mode=${activeMode}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Source resolution failed (${res.status})`);
      }
    } catch (err) {
      setResolvingEp(null);
      alert(`Could not resolve episode: ${err.message}`);
      return;
    }
    // Record history (best-effort, fire and forget)
    fetch(`${API}/history/record`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        allanime_id: anime.allanime_id,
        title: anime.title,
        episode: String(ep),
        mode: activeMode,
        user_id: userId,
        episode_count: anime.episode_count,
      }),
    }).catch(() => {});
    setResolvingEp(null);
    onOpenApp?.("anime-player", {
      animeId: anime.allanime_id,
      episode: String(ep),
      mode: activeMode,
      title: anime.title,
      episodeCount: anime.episode_count,
    });
  }

  return (
    <div className="space-y-4 max-w-4xl h-full">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className={btnGhost}>
          <ChevronLeft className="w-4 h-4" />
          Back
        </button>
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500">Mode:</span>
          <select
            value={activeMode}
            onChange={(e) => setActiveMode(e.target.value)}
            className="text-sm bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-100"
          >
            <option value="sub">Sub</option>
            <option value="dub">Dub</option>
          </select>
          <button onClick={() => loadEpisodes(activeMode)} className={btnGhost}>
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xl font-semibold text-zinc-100 truncate">{anime.title}</div>
          <div className="text-xs text-zinc-500 mt-0.5">
            allanime id: <code className="text-zinc-400">{anime.allanime_id}</code>
          </div>
        </div>
        <button
          onClick={async () => {
            await toggleWatchlist({ userId, anime, currentlyIn: inList });
            reloadWatchlist?.();
          }}
          className={`shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded ${
            inList
              ? "bg-amber-900/40 border border-amber-700/60 text-amber-300"
              : "bg-zinc-800 hover:bg-zinc-700 text-zinc-200"
          }`}
          title={inList ? "Remove from watchlist" : "Add to watchlist"}
        >
          {inList ? <BookmarkCheck className="w-4 h-4" /> : <Bookmark className="w-4 h-4" />}
          {inList ? "On watchlist" : "Add to watchlist"}
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-zinc-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading episodes…
        </div>
      )}

      {error && (
        <div className="p-3 rounded border border-red-900/60 bg-red-950/40 text-sm text-red-200">
          {error}
        </div>
      )}

      {!loading && !error && episodes.length === 0 && (
        <div className="text-sm text-zinc-500">No episodes available in this mode.</div>
      )}

      {episodes.length > 0 && (
        <>
          {batches.length > 1 && (
            <div className="flex items-center gap-2 text-sm">
              <label htmlFor="ep-batch" className="text-zinc-500">Episodes</label>
              <select
                id="ep-batch"
                value={batchIdx}
                onChange={(e) => setBatchIdx(Number(e.target.value))}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-100"
              >
                {batches.map((b, i) => (
                  <option key={i} value={i}>{b.label}</option>
                ))}
              </select>
              <span className="text-xs text-zinc-500">
                {visibleEpisodes.length} of {episodes.length}
              </span>
            </div>
          )}
          <div className="grid grid-cols-[repeat(auto-fill,minmax(64px,1fr))] gap-2">
            {visibleEpisodes.map((ep) => (
              <button
                key={ep}
                onClick={() => handlePlay(ep)}
                disabled={resolvingEp !== null}
                className={`relative ${card} flex items-center justify-center font-medium ${resolvingEp === ep ? "opacity-60" : ""}`}
                title={`Play episode ${ep}`}
              >
                {resolvingEp === ep ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  ep
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}


// ════════════════════════════════════════════════════════════════════════════
//  Watchlist tab
// ════════════════════════════════════════════════════════════════════════════

function WatchlistTab({ userId, onOpenApp, reloadWatchlist, onShowEpisodes }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/watchlist?user_id=${encodeURIComponent(userId)}`);
      const data = await res.json();
      setRows(data.watchlist || []);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { reload(); }, [reload]);

  // SSE — refresh on any watchlist or history change for this user
  useEffect(() => {
    const es = new EventSource(`${API}/events`);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if ((ev.type === "watchlist_updated" || ev.type === "history_updated")
            && (!ev.user_id || ev.user_id === userId)) {
          reload();
        }
      } catch {/* ignore */}
    };
    return () => es.close();
  }, [reload, userId]);

  async function play(row, advance = false) {
    let ep = row.last_episode || "1";
    if (advance && row.last_episode) {
      try {
        const res = await fetch(`${API}/episodes/${encodeURIComponent(row.allanime_id)}?mode=${row.mode || "sub"}`);
        const data = await res.json();
        const eps = data.episodes || [];
        const idx = eps.indexOf(row.last_episode);
        if (idx >= 0 && idx + 1 < eps.length) ep = eps[idx + 1];
      } catch {/* fall through */}
    }
    onOpenApp?.("anime-player", {
      animeId: row.allanime_id,
      episode: ep,
      mode: row.mode || "sub",
      title: row.title,
      episodeCount: row.episode_count,
    });
  }

  async function removeItem(row) {
    await fetch(`${API}/watchlist/${encodeURIComponent(row.allanime_id)}?user_id=${encodeURIComponent(userId)}`, { method: "DELETE" });
    reload();
    reloadWatchlist?.();
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-400 h-full">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!rows.length) {
    return (
      <div className="text-sm text-zinc-500 h-full">
        Your watchlist is empty. Use the bookmark icon on Browse results to save shows.
      </div>
    );
  }

  return (
    <div className="space-y-2 max-w-3xl h-full">
      {rows.map((r) => {
        const started = !!r.last_episode;
        const finished = r.finished;
        const progressLabel = !started
          ? "○ not started"
          : finished
            ? `✓ finished (last: ep ${r.last_episode})`
            : `▶ ep ${r.last_episode} of ${r.episode_count} (${r.mode})`;
        return (
          <div key={r.id} className="p-3 rounded border border-zinc-800 bg-zinc-900/60 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="font-medium truncate">{r.title}</div>
              <div className="text-xs text-zinc-500 mt-0.5">
                {progressLabel}
                {r.episode_count ? ` • ${r.episode_count} eps total` : ""}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => onShowEpisodes?.(r)}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200"
                title="Browse all episodes"
              >
                <ListVideo className="w-4 h-4" /> Episodes
              </button>
              {started && !finished && (
                <button onClick={() => play(r, false)} className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200" title="Replay current episode">
                  <PlayCircle className="w-4 h-4" /> Resume
                </button>
              )}
              <button
                onClick={() => play(r, started && finished)}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded bg-teal-600 hover:bg-teal-500 text-white"
                title={!started ? "Start episode 1" : finished ? "Play next episode" : "Replay episode"}
              >
                <PlayCircle className="w-4 h-4" />
                {!started ? "Start" : finished ? "Next ep" : "Replay"}
              </button>
              <button
                onClick={() => removeItem(r)}
                className="p-1.5 rounded hover:bg-red-900/40 text-zinc-500 hover:text-red-300"
                title="Remove from watchlist"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}


// ════════════════════════════════════════════════════════════════════════════
//  History tab
// ════════════════════════════════════════════════════════════════════════════

function HistoryTab({ userId, onOpenApp, onShowEpisodes }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const refreshKey = useRef(0);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/history?user_id=${encodeURIComponent(userId)}`);
      const data = await res.json();
      setRows(data.history || []);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { reload(); }, [reload]);

  // SSE — refresh on history changes
  useEffect(() => {
    const es = new EventSource(`${API}/events`);
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        if (event.type === "history_updated") reload();
      } catch {/* ignore */}
    };
    return () => es.close();
  }, [reload]);

  async function play(row, advance = false) {
    let ep = row.last_episode;
    if (advance) {
      try {
        const res = await fetch(`${API}/episodes/${encodeURIComponent(row.allanime_id)}?mode=${row.mode}`);
        const data = await res.json();
        const eps = data.episodes || [];
        const idx = eps.indexOf(row.last_episode);
        if (idx >= 0 && idx + 1 < eps.length) ep = eps[idx + 1];
      } catch {/* fall through to last_episode */}
    }
    onOpenApp?.("anime-player", {
      animeId: row.allanime_id,
      episode: ep,
      mode: row.mode,
      title: row.title,
    });
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-400 h-full">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!rows.length) {
    return <div className="text-sm text-zinc-500 h-full">No watch history yet.</div>;
  }

  return (
    <div className="space-y-2 max-w-3xl h-full">
      {rows.map((r) => (
        <div key={r.id} className={`${card} flex items-center justify-between gap-3`}>
          <div className="min-w-0">
            <div className="font-medium truncate">{r.title}</div>
            <div className="text-xs text-zinc-500 mt-0.5">
              ep {r.last_episode} • {r.mode} • {r.finished ? "✓ finished" : "▶ in progress"}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => onShowEpisodes?.(r)}
              className={btnGhost}
              title="Browse all episodes"
            >
              <ListVideo className="w-4 h-4" />
              Episodes
            </button>
            <button onClick={() => play(r, false)} className={btnGhost}>
              <PlayCircle className="w-4 h-4" />
              Replay
            </button>
            <button onClick={() => play(r, true)} className={btnPrimary}>
              <PlayCircle className="w-4 h-4" />
              Next ep
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
