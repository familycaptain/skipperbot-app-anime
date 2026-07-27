# Findings — anime

Survey only; nothing fixed. Corpus 3 → 62 records. Items marked **VERIFIED** confirmed by the PM.

## Security

**1. VERIFIED — `routes.py::anime_proxy_segment` (`GET /stream/proxy?u=<b64>`) is an unrestricted
server-side fetcher (SSRF).** Confirmed at line 289: `upstream_url = _b64url_decode(u)` inside a `try`
that catches only malformed base64, then the URL is fetched with `follow_redirects=True` and the response
body streamed back. **No allowlist, no scheme check, no host check, and no verification that the URL came
from a cached source bundle.** Any authenticated member — including a child account — can make the server
GET arbitrary URLs and read the result: LAN devices, `http://127.0.0.1:8000/…`, Home Assistant,
link-local metadata endpoints. Expected: constrain `u` to hosts present in the current
`anime_source_cache` bundle, or sign the proxy URL when the playlist is rewritten — the app mints every
legitimate one itself in `_rewrite_playlist`. Compounding: `principal_from_request` accepts the
`sb_session` cookie on GET, so this is also reachable by CSRF (the attacker cannot read the body but can
drive internal GETs). Same class as the timeline SSRF in the main platform.

**2. The MCP tools bypass the IDOR guard the REST layer enforces.** `routes.py` consistently calls
`scope_user(...)`, so `GET /history?user_id=alice` from bob is a 403. But `tools.py::anime_history`,
`anime_resume`, `anime_watchlist`, `anime_watchlist_add`, `anime_watchlist_remove` pass `user_id` straight
to `data.py` with **no principal check at all**. Cross-person access in chat is prevented only by prose in
`guide.md` ("never guess or hardcode"). A child asking "show me alice's anime history" is one model
decision from succeeding, and the watchlist tools can *write* to another person's list.

**3. `static/anime-player.html` loads `hls.js` from `cdn.jsdelivr.net`.** A self-hosted family assistant
pop-out pulls a third-party script with full DOM access onto a page carrying `userId` in its query string,
and the pop-out stops working with no internet. The React player imports `hls.js` as a bundled dependency;
the standalone page should too.

**4. SSE broadcasts are not scoped to a person.** `_broadcast` pushes `history_updated` (with `id`,
`allanime_id`) and `watchlist_updated` (with `user_id`, `allanime_id`) to *every* connected client, and
`HistoryTab` reloads on any `history_updated` regardless of whose it was — so any listener learns which
show another member just saved or started.

## Broken / never-worked behaviour

**5. Casting almost certainly cannot play — the URL it hands the TV is auth-gated.**
`tools.py::cast_anime_episode` sends the device
`{base_url}/api/apps/anime/stream/{id}/{ep}/{q}/master.m3u8`. In the platform's
`agent.py::_is_public_path`, `/api/…` is not public, and `auth_gate` rejects a request with no bearer
header and no `sb_session` cookie — a Chromecast/Roku sends neither. Home Assistant returns 200 because it
only forwards the request, and the tool's own success text hedges with "If playback doesn't start, the cast
device couldn't reach the URL", consistent with this never having worked. *Moderate confidence — not
exercised against a live device.* One unauthenticated `curl` against a cast URL would confirm. If so,
casting needs a signed, short-lived, unauthenticated stream URL.

**6. The pop-out window never records watch progress.** `static/anime-player.html` is standalone: its
`fetch` is not the SPA's `installAuthFetch`-patched one, so it sends no `Authorization` header. Its GETs
survive on the `sb_session` cookie, but `principal_from_request` deliberately ignores the cookie for
mutating methods (the CSRF guard), so its `POST /history/record` should 401 every time. **Watching an
entire episode in the pop-out leaves history and resume position untouched**, and the `BroadcastChannel`
handoff still works, so the failure is invisible until you come back a day later. *Moderate confidence.*

**7. The player's "re-resolve sources" button does not re-resolve.** `routes.py::api_sources` supports
`?refresh=true`, but **no caller anywhere passes it** — not `resolveSources`, not
`static/anime-player.html::loadSources`, not `EpisodePicker`. The refresh control re-reads the same cached
bundle for up to 10 minutes — exactly the window in which a person hits an expired stream and presses it.

**8. `digest_record` is never called** — zero occurrences in the repo, while `CLAUDE.md` and
`specs/APP_PACKAGES.md` list memory digestion as non-negotiable for every mutation and `help.md` tells
users their watch history is "pulled into Skipper's memory — so you can just ask 'what have I been
watching?'". Either the data layer should digest, or `help.md` is wrong.

**9. `manifest.yaml` declares `emits: [anime.episode_played, anime.episode_finished]` and nothing emits
either.**

**10. `data.py::purge_expired_cache` is defined and never called** — no schedule, no job, no route.
`anime_source_cache` grows one row per (show, episode, mode) forever, and `idx_anime_source_expires` exists
solely for a sweep that never runs.

## Dead code and dead columns

11. `routes.py::api_resume` has no callers; its docstring says "with the next episode pre-resolved" and the
    body resolves nothing (`return {"resume": top}` under a comment about suggesting the next episode).
12. `data.py::delete_history_entry` and `get_history_for_anime` — defined, never called. **There is no way
    for a person to delete a history entry.**
13. `anime_source_cache.selected_url` is written and returned and read by no consumer — both players pick
    from `streams` themselves.
14. `cover_url` exists on `anime_titles` and `anime_watchlist`, is mapped into every returned row, is
    **never written by anything** (always `''`) and never read by the UI.
15. `anime_watchlist.sort_order` is documented in the migration as "user-controlled ordering" and is the
    second sort key in `get_watchlist`, but no route, tool or UI control can set it — permanently 0.
16. `_rewrite_playlist` takes a `request` parameter it never uses.
17. `ui/AnimeApp.jsx::HistoryTab` declares `const refreshKey = useRef(0)` and never reads or writes it.

## Correctness

18. **Changing quality restarts the episode from zero.** The `<video>` `key` is
    `${animeId}:${episode}:${mode}` — quality is excluded, so the element survives — but the wiring effect
    tears down hls.js and calls `loadSource` afresh, and `seekIfPending` only fires when a pop-out position
    is pending. Someone 20 minutes in who drops from 1080p to 720p is returned to the start. Same in
    `static/anime-player.html::attach`.
19. **`finished` is not sticky.** Both players recompute `finished = currentTime/duration > 0.92` on every
    save, so seeking back after the credits flips a finished episode to unfinished — which then changes
    what "next episode" does.
20. **Playing from chat can store the allanime id as the show's title.** `tools.py::anime_play` does
    `upsert_title(allanime_id, title or allanime_id, 0)` with `title` defaulting to `""`, so the opaque
    catalog id becomes the persisted title in history and the watchlist — and `upsert_title` then
    overwrites a good title with it on any later call.
21. `data.py::record_watch` never refreshes `title` on an existing row, so a title corrected upstream — or
    corrupted per §20 — is permanent for that person.
22. The subtitle track URL is built with the *bundle-level* `sources.referer` rather than the chosen
    stream's own `referer`; where the subtitle host differs, the wrong referer is sent.
23. `api_record_watch` calls `upsert_title` and returns HTTP 500 ("Failed to upsert title") on an empty
    result — a database hiccup surfaces to the player as a 500 during a routine 15-second position save.

## Contract / house-style drift

24. **Both UI files use raw Tailwind colour scales throughout** — `bg-zinc-950`, `text-zinc-100`,
    `bg-teal-600`, `border-red-900/60`. `CLAUDE.md` and `APP_PACKAGES.md` say raw colour scales **fail the
    build** and only semantic classes are permitted, and that the app must work in both themes. This app is
    hardcoded dark. Either it is exempt as an out-of-tree package (in which case the contract copy shipped
    here is misleading), or it does not pass the platform's lint.
25. `CLAUDE.md` says tests run as `python -m pytest tests/`, but the only test is a Playwright e2e needing a
    live dockerised install, a real login and the third-party catalog to be up. There are **no offline unit
    tests for `allanime.py`'s decode pipeline** — the most fragile and most frequently broken code here.
26. `guide.md` claims "`anime_play` does NOT take user_id — the watch event is recorded by the web player
    itself once playback starts." With §6, a chat-initiated play watched in a pop-out is recorded by nobody.

## Checked and clean

**The raw-HTML rendering pattern is NOT present here.** Catalog titles reach the React UIs as JSX text
children (`{r.title}`), and the standalone pop-out sets `$title.textContent` / `$meta.textContent` /
`document.title` — never `innerHTML`. The only `innerHTML` write in the repo is `$qualSel.innerHTML = ""`
(clearing). Third-party stream URLs are never interpolated into markup; they are base64-encoded into a
query parameter and set via the `src` property. That is what
`anime.upstream.catalogue-text-is-never-interpreted` records, and it is worth preserving deliberately —
the same data reaching a future `dangerouslySetInnerHTML` would be directly exploitable, since the catalog
controls both the titles and the URLs.
