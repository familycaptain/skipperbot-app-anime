# skipperbot-app-anime

A Skipperbot **app package** — search, stream, and track anime, with optional
cast-to-TV. Built on the public **allanime.day** catalog.

> **Shipped separately on purpose.** Streaming third-party video isn't something
> every household wants bundled into their assistant, so this is an opt-in
> package rather than part of the core platform. Install it only if you want it.

## What it is

- **Search + watch** anime from the allanime.day catalog, in an in-app HLS player.
- **Watch history / continue-watching** tracking.
- **Cast to a TV** (optional) — plays an episode on a Home Assistant media player.

## Install

```bash
# from a running skipperbot-platform checkout:
cd apps
git clone <this-repo> anime
cd ..   # restart the platform — the loader discovers apps/anime/ on boot
```

The clone target directory **must** be `anime` (the app id) so it lands at
`skipperbot-platform/apps/anime`. No extra Python deps — the platform already
provides what it needs.

**Casting** is optional and requires the separate **Automation** (Home Assistant)
app; without it, search/stream still work and casting returns a clear message.

## Layout

```
manifest.yaml   app manifest (id: anime)
data.py         data layer (app_anime schema)
allanime.py     allanime.day source client
tools.py        chat tools (search / play / cast / history)
routes.py       REST router, mounted at /api/apps/anime
migrations/     per-app SQL migrations
ui/             desktop UI (AnimeApp + AnimePlayerApp, auto-discovered by Vite)
guide.md / help.md
```

## Status

Prerelease extraction — carved out of the bundled platform so controversial /
niche apps are opt-in. MIT licensed.
