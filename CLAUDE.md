# Anime — a Skipperbot app package

This repo is a **Skipperbot app** (app id `anime`, entity prefixes
`an-`/`anwh-`, Postgres schema `app_anime`). It installs by being dropped
into a Skipperbot platform's `apps/anime/` folder.

## The binding contract — read it FIRST

**`specs/APP_PACKAGES.md` in this repo is the app contract.** Before writing
or reviewing any code here, read it. It is written as prompt guidance for an
AI assistant; every rule in it is binding. Do not invent conventions this
file already settles.

Non-negotiables it will hold you to (headlines only — the contract has the
details and the patterns to mirror):

- **Memory digestion**: every data-layer mutation (create/update/delete/
  complete) calls `app_platform.memory.digest_record` — an app that skips
  this is invisible to chat recall.
- **Messaging**: anything said to a user goes through
  `app_platform.notifications.create_notification` — NEVER a channel-specific
  sender (`send_dm`, pushover, FCM, raw WebSocket).
- **Recurring work**: `public.schedules` (via `app_platform.schedules`), never
  self-inserted job rows; one-off work via `app_platform.jobs.submit_job`.
  A Python schedule-seed migration is NOT run automatically — see the
  contract's Schedules section.
- **Time**: `app_platform.time` (`now`/`utcnow`/`to_local`) — never naive
  `datetime.now()` / `date.today()`.
- **Settings**: your own settings auto-scope to `app:anime`;
  platform-wide reads need an explicit `scope="platform"`.
- **UI ↔ chat parity**: every meaningful UI capability has a matching tool in
  `tools.py` + `guide.md` coverage; ship `help.md` for users.
- **Styling**: semantic design-system classes only (`surface-card`,
  `btn-primary`, `text-muted`, …) — raw Tailwind color scales fail the build.
- **Isolation**: import only `app_platform.*` and this package — never
  `apps.<other>.*`; cross-app reads via `query_entities`, writes via events.

## Working in this repo

- Tests live in `tests/` and ship with the app: `python -m pytest tests/`.
- To try the app on a platform: copy/clone this folder to
  `<platform>/apps/anime/` and restart the agent.
- `specs/APP_PACKAGES.md` is a copy of the platform's canonical contract —
  don't edit it here; when the platform's copy changes it is re-copied in
  by hand (there is no automated sync).
