# Anime

Search a large anime catalog, stream episodes in a built-in player, keep a
watchlist, and pick up right where you left off — per person.

## Overview

Anime lets each member of the household find shows, watch them in an in-app
player, and have their progress remembered so "resume" always works. It tracks
two separate things: your **history** (everything you've played, recorded
automatically) and your **watchlist** (shows you deliberately saved for later).
Sub and dub are both supported, at up to 1080p.

It's built on a public catalog (allanime.day), so what's available depends on
that upstream source.

## Screens

- **Search.** Type a show name to search the catalog; results show the title and
  episode count.
- **Episode list.** Pick a show to see its episodes; choose **sub** or **dub**.
- **Player.** Selecting an episode opens the in-app player and starts playback;
  your position is saved as you watch, so you can leave and resume.
- **Watchlist.** Your saved/favorited shows with progress.
- **History.** Recently watched shows (filled in automatically as you play).

## Example workflows

**Find and watch a show**
- *In the app:* search the title → open it → pick an episode → it plays.
- *Through chat:* "find One Piece" then "play episode 3 of One Piece".

**Resume what you were watching**
- *In the app:* open Anime — your history/watchlist show progress; reopen the show.
- *Through chat:* "what anime was I watching?" or "resume my anime".

**Save a show for later**
- *In the app:* add it to your watchlist from the show page.
- *Through chat:* "add One Piece to my watchlist", "what's on my watchlist?"

**Watch on the TV**
- *Through chat:* "play One Piece episode 3 on the TV" / "cast it to the
  Chromecast" — Skipper resolves the stream and starts it on that device (needs
  the Automation app + a Chromecast-capable target).

## Tips

- "History" is automatic (what you played); "watchlist" is what you explicitly
  saved — ask for either by name.
- Casting uses the Automation app's device names (e.g. "tv", "roku").

## Your data

Your **watch history, watchlist, and resume positions are saved** as records
(per person) **and pulled into Skipper's memory** — so you can just ask "what
have I been watching?" and Skipper knows. It's per-user and stays in your
household; the video itself streams live from the public catalog and isn't stored.
