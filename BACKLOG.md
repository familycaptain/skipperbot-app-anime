# Backlog — anime

Decided work not yet done. Distinct from `specs-audit/findings-anime.md`, which is a survey of what
the code currently does.

---

## Make searching easier

**Operator request.** Finding something to watch is harder than it should be.

Worth looking at, from the audit:

- Search goes straight to the allanime catalog on every keystroke-ish query; there is no local
  history of what this household has searched or watched to bias results toward.
- The watchlist already exists (`anime_watchlist`) and `sort_order` is written but can never be set
  from any surface (`findings-anime.md` §15) — so "the things I meant to watch" cannot be ordered.
- `data.py::get_history_for_anime` and `delete_history_entry` exist with no callers
  (`findings-anime.md` §12), so history is write-mostly: you cannot search or prune what you have
  already started.
- `tools.py::anime_play` can store the opaque catalog id as the title when the model omits one
  (`findings-anime.md` §20), which then makes that entry unfindable by name afterwards.

Fixing §20 first is probably worth it regardless — a corrupted title is permanent for that person
(§21), so every day it stays unfixed makes the searchable history worse.

## Pull in cover art

**Operator request.** Show artwork for a title rather than text rows.

Note the schema is already half-built for this: **`cover_url` exists on both `anime_titles` and
`anime_watchlist`, is mapped into every row the data layer returns, and is written by nothing —
always `''` — and read by no UI** (`findings-anime.md` §14). So this is less "add a feature" than
"finish one that was scaffolded and abandoned": populate it at the point a title is first seen, and
render it in the browse/watchlist/history views.

Two things to decide when doing it:

- **Where the image comes from.** If it is hot-linked from the catalog, the household's browser
  fetches a third-party URL per card, which is a per-title tracking beacon and breaks with no
  internet. Caching through the images app is the alternative, at the cost of storage and a fetch
  path — and note the platform's images library serves files with **no authentication at all**
  (`skipperbot-platform/specs-audit/findings-images.md` A1), so caching there today would make the
  artwork public to anyone who can reach the host.
- **A URL from the catalog is untrusted input.** It ends up in an `src` attribute; keep it to
  http/https and never interpolate it into markup. The app is currently clean on that
  (`findings-anime.md`, "Checked and clean") and it is worth staying that way.
