import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Loader2, ExternalLink, ChevronLeft, ChevronRight, RefreshCw,
  Settings, AlertCircle, ListVideo, PlayCircle,
} from "lucide-react";
import Hls from "hls.js";

/**
 * Anime Player — embedded HLS player with quality + sub/dub picker,
 * resume position, and a "pop out" button that detaches the player into
 * a real new browser window.
 *
 * Props from registry: appId, userId, context = { animeId, episode, mode, title, episodeCount }
 */

const API = "/api/apps/anime";

function base64Url(s) {
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}


export default function AnimePlayerApp({ context = {}, onTitle, onOpenApp, userId }) {
  const { animeId, title: initialTitle = "Anime" } = context;
  const [episode, setEpisode] = useState(String(context.episode || "1"));
  const [mode, setMode] = useState(context.mode || "sub");
  const [title, setTitle] = useState(initialTitle);
  const [episodeCount, setEpisodeCount] = useState(context.episodeCount || 0);

  const [sources, setSources] = useState(null);   // { streams, selected_url, referer, subs_url }
  const [quality, setQuality] = useState("auto"); // "auto" | "1080" | "720" | ...
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [episodes, setEpisodes] = useState([]);

  const videoRef = useRef(null);
  const hlsRef = useRef(null);

  // Pop-out coordination — uses BroadcastChannel so we don't have to keep
  // a window.opener handle (and can keep noopener,noreferrer on window.open,
  // which avoids weird PWA navigation behavior).
  const [poppedOut, setPoppedOut] = useState(false);
  const channelRef = useRef(null);
  // Last known playback position — set when we pop out, updated by channel
  // messages from the pop-out. Consumed when the user clicks "Resume here".
  const popoutPositionRef = useRef(0);

  // Preserve the user's chosen quality across episode/mode switches. Read inside
  // resolveSources WITHOUT being one of its deps (a quality dep would re-resolve
  // sources on every quality change). null = no selection yet → first resolve
  // picks the default.
  const qualityRef = useRef(null);
  useEffect(() => { if (quality) qualityRef.current = quality; }, [quality]);

  // Preserve element-level media state across the per-episode <video> remount
  // (a fresh element otherwise resets volume/mute).
  const volumeRef = useRef(1);
  const mutedRef = useRef(false);

  // Channel name keys on this specific tab so multiple pop-outs of different
  // shows don't cross-talk.
  const channelName = useMemo(() => `anime-player::${animeId}::${episode}::${mode}`, [animeId, episode, mode]);

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel(channelName);
    channelRef.current = ch;
    ch.onmessage = (e) => {
      const d = e.data;
      if (!d) return;
      if (d.type === "position" && typeof d.currentTime === "number") {
        popoutPositionRef.current = Math.floor(d.currentTime);
      }
    };
    return () => {
      try { ch.close(); } catch {/* ignore */}
      if (channelRef.current === ch) channelRef.current = null;
    };
  }, [channelName]);

  useEffect(() => {
    if (onTitle) onTitle(`${title} — ep ${episode}`);
  }, [onTitle, title, episode]);

  // Resolve sources whenever episode/mode/anime changes
  const resolveSources = useCallback(async () => {
    if (!animeId) return;
    setLoading(true); setError(""); setSources(null);
    try {
      const res = await fetch(`${API}/sources/${encodeURIComponent(animeId)}/${encodeURIComponent(episode)}?mode=${mode}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Source resolution failed");
      setSources(data);
      // Preserve the user's current quality across the switch when episode B
      // offers it (or it's "auto"); otherwise fall back to B's highest. On the
      // very first resolve (no prior selection) default to the highest.
      const avail = (data.streams || []).map(s => s.quality);
      const heights = avail
        .filter(q => /^\d+$/.test(q))
        .sort((a, b) => Number(b) - Number(a));
      const want = qualityRef.current;
      const keep = want && (want === "auto" || avail.includes(want));
      setQuality(keep ? want : (heights[0] || "auto"));
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [animeId, episode, mode]);

  useEffect(() => { resolveSources(); }, [resolveSources]);

  // Load the episode list once so prev/next buttons work
  useEffect(() => {
    if (!animeId) return;
    fetch(`${API}/episodes/${encodeURIComponent(animeId)}?mode=${mode}`)
      .then(r => r.json())
      .then(d => {
        const list = d.episodes || [];
        setEpisodes(list);
        if (list.length) setEpisodeCount(list.length);
        if (d.title) setTitle(d.title);
      })
      .catch(() => setEpisodes([]));
  }, [animeId, mode]);

  // Pick the actual stream that matches the chosen quality
  const chosen = useMemo(() => {
    if (!sources?.streams?.length) return null;
    if (quality === "auto") return sources.streams.find(s => s.is_hls) || sources.streams[0];
    return sources.streams.find(s => s.quality === quality) || sources.streams[0];
  }, [sources, quality]);

  // Wire up the chosen stream into the <video> element. Skipped while popped
  // out — the <video> isn't even mounted then.
  useEffect(() => {
    if (poppedOut) return;
    if (!chosen || !videoRef.current) return;
    const video = videoRef.current;

    // Full teardown of any prior media before loading the new source. The
    // per-(anime,episode,mode) <video> remount (key on the element below) is the
    // primary stale-media guard — a fresh element can carry no prior episode's
    // MediaSource/buffer. This block fully resets the SAME element on a quality
    // switch (which keeps the element) and is otherwise purely defensive.
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
    if (video.getAttribute("src")) {
      try { video.removeAttribute("src"); video.load(); } catch {/* ignore */}
    }

    // Re-apply element-level state lost when the <video> remounts per episode.
    try { video.volume = volumeRef.current; } catch {/* ignore */}
    video.muted = mutedRef.current;
    const onVolume = () => { volumeRef.current = video.volume; mutedRef.current = video.muted; };
    video.addEventListener("volumechange", onVolume);

    // If we just resumed from a pop-out, seek to the last known position.
    const seekIfPending = () => {
      const t = popoutPositionRef.current;
      if (t > 0) {
        try { video.currentTime = t; } catch {/* ignore */}
        popoutPositionRef.current = 0;
        const playP = video.play();
        if (playP && typeof playP.catch === "function") {
          playP.catch(() => { video.muted = true; video.play().catch(() => {}); });
        }
      }
    };

    if (chosen.is_hls) {
      // HLS path: route through the playlist-rewriting proxy + hls.js
      const proxyMaster = `${API}/stream/${encodeURIComponent(animeId)}/${encodeURIComponent(episode)}/${encodeURIComponent(quality)}/master.m3u8?mode=${mode}`;
      if (Hls.isSupported()) {
        const hls = new Hls({ lowLatencyMode: false, enableWorker: true });
        hlsRef.current = hls;
        hls.loadSource(proxyMaster);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, seekIfPending);
        hls.on(Hls.Events.ERROR, (_e, data) => {
          if (data.fatal) {
            console.warn("[hls.js fatal]", data);
            setError(`Playback error: ${data.type} / ${data.details}`);
          }
        });
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = proxyMaster;
        video.addEventListener("loadedmetadata", seekIfPending, { once: true });
      } else {
        setError("This browser does not support HLS playback.");
      }
    } else {
      // Direct MP4 — go through the byte-stream proxy so Referer is set upstream
      const u = base64Url(chosen.url);
      const r = base64Url(chosen.referer || "");
      video.src = `${API}/stream/proxy?u=${u}&r=${r}`;
      video.addEventListener("loadedmetadata", seekIfPending, { once: true });
    }

    return () => {
      video.removeEventListener("volumechange", onVolume);
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [chosen, animeId, episode, mode, quality, poppedOut]);

  // Throttled position recording (every 15s while playing)
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !animeId) return;
    let lastSent = 0;
    const onTime = () => {
      const now = Date.now();
      if (now - lastSent < 15000) return;
      lastSent = now;
      const pos = Math.floor(video.currentTime || 0);
      const finished = video.duration && video.currentTime / video.duration > 0.92;
      fetch(`${API}/history/record`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          allanime_id: animeId,
          title,
          episode,
          mode,
          user_id: userId,
          position_s: pos,
          finished: !!finished,
        }),
      }).catch(() => {});
    };
    video.addEventListener("timeupdate", onTime);
    return () => video.removeEventListener("timeupdate", onTime);
  }, [animeId, episode, mode, title, userId]);

  // Episode navigation
  const epIdx = useMemo(() => episodes.indexOf(episode), [episodes, episode]);
  const prevEp = epIdx > 0 ? episodes[epIdx - 1] : null;
  const nextEp = epIdx >= 0 && epIdx + 1 < episodes.length ? episodes[epIdx + 1] : null;

  function popOut() {
    // Capture current position so the pop-out resumes where this player left off
    const t = Math.floor(videoRef.current?.currentTime || 0);
    popoutPositionRef.current = t;

    const url = `/api/apps/anime/player.html?animeId=${encodeURIComponent(animeId)}&episode=${encodeURIComponent(episode)}&mode=${mode}&quality=${encodeURIComponent(quality)}&title=${encodeURIComponent(title)}&userId=${encodeURIComponent(userId || "")}&t=${t}&ch=${encodeURIComponent(channelName)}`;
    // `popup` forces a real popup window in modern Chrome instead of a tab.
    // `noopener,noreferrer` keeps the parent in its own browsing context — the
    // pop-out talks back via BroadcastChannel, not window.opener.
    // NOTE: with `noopener`, window.open always returns null per spec, so we
    // can't use the return value to detect popup blocking. We trust the open
    // succeeded; if the user has popups blocked the in-app player still
    // transitions to the "Resume here" state and they can press it to recover.
    window.open(url, "_blank", "popup,noopener,noreferrer,width=1280,height=760");

    // Stop in-app playback so audio doesn't double up with the pop-out window
    if (videoRef.current) {
      try { videoRef.current.pause(); } catch {/* ignore */}
      videoRef.current.removeAttribute("src");
      try { videoRef.current.load(); } catch {/* ignore */}
    }
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    setPoppedOut(true);
  }

  function resumeHere() {
    // Tell the pop-out (if still open) to close itself via the channel.
    try { channelRef.current?.postMessage({ type: "close" }); } catch {/* ignore */}
    setPoppedOut(false);
    // The source-wiring effect will re-run because `poppedOut` is in its deps;
    // it picks up popoutPositionRef and seeks once the stream is ready.
  }

  const qualityOptions = useMemo(() => {
    if (!sources) return ["auto"];
    const numeric = (sources.streams || [])
      .map(s => s.quality)
      .filter(q => /^\d+$/.test(q));
    const unique = Array.from(new Set(numeric)).sort((a, b) => Number(b) - Number(a));
    return ["auto", ...unique];
  }, [sources]);

  return (
    <div className="flex flex-col h-full w-full bg-black text-zinc-100">
      {/* Top bar */}
      <div className="flex items-center justify-between gap-3 px-3 py-2 bg-zinc-950 border-b border-zinc-900">
        <div className="min-w-0">
          <div className="font-medium truncate">{title}</div>
          <div className="text-xs text-zinc-500">Episode {episode} • {mode}</div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => prevEp && setEpisode(prevEp)}
            disabled={!prevEp || poppedOut}
            className="inline-flex items-center gap-1 px-2 py-1 text-sm rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40"
          >
            <ChevronLeft className="w-4 h-4" /> Prev
          </button>
          <button
            onClick={() => nextEp && setEpisode(nextEp)}
            disabled={!nextEp || poppedOut}
            className="inline-flex items-center gap-1 px-2 py-1 text-sm rounded bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40"
          >
            Next <ChevronRight className="w-4 h-4" />
          </button>

          <select
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            disabled={poppedOut}
            className="text-sm bg-zinc-900 border border-zinc-800 rounded px-2 py-1 disabled:opacity-40"
            title={poppedOut ? "Resume here to change episode/mode" : "Sub / Dub"}
          >
            <option value="sub">Sub</option>
            <option value="dub">Dub</option>
          </select>

          <select
            value={quality}
            onChange={(e) => setQuality(e.target.value)}
            disabled={poppedOut}
            className="text-sm bg-zinc-900 border border-zinc-800 rounded px-2 py-1 disabled:opacity-40"
            title="Quality"
          >
            {qualityOptions.map(q => (
              <option key={q} value={q}>{q === "auto" ? "Auto" : `${q}p`}</option>
            ))}
          </select>

          <button
            onClick={() => onOpenApp?.("anime", {
              showEpisodesFor: { allanime_id: animeId, title, episode_count: episodeCount, mode },
            })}
            className="inline-flex items-center gap-1 px-2 py-1 text-sm rounded bg-zinc-900 hover:bg-zinc-800"
            title="Browse all episodes"
          >
            <ListVideo className="w-4 h-4" /> Episodes
          </button>
          <button
            onClick={resolveSources}
            className="inline-flex items-center gap-1 px-2 py-1 text-sm rounded bg-zinc-900 hover:bg-zinc-800"
            title="Re-resolve sources"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={popOut}
            disabled={poppedOut}
            className="inline-flex items-center gap-1 px-2 py-1 text-sm rounded bg-teal-700 hover:bg-teal-600 disabled:opacity-40 disabled:cursor-not-allowed"
            title={poppedOut ? "Already playing in a pop-out window" : "Open in a new desktop window"}
          >
            <ExternalLink className="w-4 h-4" /> Pop out
          </button>
        </div>
      </div>

      {/* Video area — or "Resume here" placeholder while popped out.
          Parent is `relative` so the absolutely-positioned <video> fills it.
          object-contain letterboxes the video content so aspect ratio is
          always preserved (no stretching, no cropping, regardless of how
          wide or tall the panel becomes). */}
      <div className="flex-1 bg-black relative overflow-hidden">
        {loading && !poppedOut && (
          <div className="absolute inset-0 flex items-center justify-center text-zinc-300 z-10 pointer-events-none">
            <Loader2 className="w-8 h-8 animate-spin" />
          </div>
        )}
        {error && !poppedOut && (
          <div className="absolute inset-x-0 top-0 mx-auto mt-3 max-w-xl p-3 rounded border border-red-900/60 bg-red-950/80 text-sm text-red-200 flex items-start gap-2 z-20">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div>{error}</div>
          </div>
        )}

        {!loading && !error && sources && !chosen && !poppedOut && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center text-zinc-400 z-10">
            <AlertCircle className="w-6 h-6" />
            <div className="text-sm">No playable source for episode {episode}{mode === "dub" ? " (dub)" : ""}.</div>
            <div className="text-xs text-zinc-500">Try switching Sub/Dub, quality, or another episode.</div>
          </div>
        )}

        {poppedOut ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
            <div className="text-zinc-300 text-sm">Playing in pop-out window</div>
            <button
              onClick={resumeHere}
              className="inline-flex items-center gap-2 px-5 py-3 rounded-lg bg-teal-600 hover:bg-teal-500 text-white font-medium"
            >
              <PlayCircle className="w-5 h-5" />
              Resume playback here
            </button>
            <div className="text-xs text-zinc-500 max-w-sm">
              Closes the pop-out window if it's still open and resumes from the
              same position. Works even if you already closed the pop-out manually.
            </div>
          </div>
        ) : (
          <video
            key={`${animeId}:${episode}:${mode}`}
            ref={videoRef}
            controls
            autoPlay
            playsInline
            className="absolute inset-0 w-full h-full object-contain bg-black"
            crossOrigin="anonymous"
          >
            {sources?.subs_url && (
              <track
                kind="subtitles"
                srcLang="en"
                label="English"
                src={`${API}/stream/proxy?u=${base64Url(sources.subs_url)}&r=${base64Url(sources.referer || "")}`}
                default
              />
            )}
          </video>
        )}
      </div>
    </div>
  );
}
