# Anime app

Search the allanime.day catalog, list episodes, resume what someone was last
watching, or play a specific episode in the in-app web player.

## Tools

- `anime_search(query, mode="sub")` — find shows by name. Returns ID + episode count.
- `anime_episodes(allanime_id, mode="sub")` — list episode numbers.
- `anime_play(allanime_id, episode, mode="sub", title="")` — resolve a stream
  and tell the agent to open the player tab.
- `anime_resume(user_id)` — continue the most recently watched show.
- `anime_history(user_id, limit=10)` — show recent watches (everything the user has played).
- `anime_watchlist(user_id)` — show the user's saved/favorited shows with progress.
- `anime_watchlist_add(user_id, allanime_id, title)` — save a show to the watchlist.
- `anime_watchlist_remove(user_id, allanime_id)` — remove a show from the watchlist.
- `cast_anime_episode(allanime_id, episode, device, mode, quality)` — resolve an
  episode AND play it on a Home Assistant media device (Chromecast, Roku, the
  TV's Chromecast input). Use this when the user says "play X on the tv" /
  "cast Y to the chromecast". `device` is the friendly alias from the
  automation app (e.g. "tv", "roku", "chromecast"); the LG webOS entity does
  NOT play arbitrary URLs — make sure the alias points at the TV's built-in
  Chromecast device, not the webostv entity.

### History vs Watchlist

- **History** is implicit — every show the user plays gets recorded automatically.
  Use `anime_history` for "what have I been watching".
- **Watchlist** is explicit — the user manually saves shows they want to watch.
  Use `anime_watchlist` for "my favorites", "my watchlist", "shows I saved".

## REQUIRED — pass the current chat user

All per-user tools (`anime_resume`, `anime_history`, `anime_watchlist*`)
require the user_id arg. Always pass the lowercase name of the person who is
currently chatting (it's already in your context — e.g. "alice", "bob",
"kid1"). Never hardcode a name and never default it to yourself.

Examples:
- Alice says "what anime was I watching?" → `anime_resume(user_id="alice")`
- Bob says "show my anime history" → `anime_history(user_id="bob")`
- Kid1 says "save one piece to my list" → `anime_search` to find the ID, then
  `anime_watchlist_add(user_id="kid1", allanime_id="<id>", title="One Piece")`
- Alice says "what's on my watchlist?" → `anime_watchlist(user_id="alice")`

`anime_play` does NOT take user_id — the watch event is recorded by the web
player itself once playback starts, using the logged-in browser user.

## After `anime_play`

The tool returns text instructing you to call `open_app` with the player.
Always call it. The player loads from cached sources resolved by the tool, so
playback starts immediately.
