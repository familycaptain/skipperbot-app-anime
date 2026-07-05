# Skipperbot — App Packages

> **The canonical guide to building a Skipperbot app.**
>
> A verbatim copy of this file ships in every app repo at `specs/APP_PACKAGES.md`
> so each app repo is self-sufficient for AI-assisted development. The
> platform repo's copy is the source of truth; an automated CI sync keeps
> all copies aligned.
>
> **Note:** `APP_PACKAGES.md` is primarily the canonical prompt guidance and
> app contract for AI-assisted app generation and review. Human authors should
> treat it as the app design ruleset, not as the only step-by-step authoring
> tutorial. For a more practical authoring workflow, see
> [`docs/BUILDING_APPS.md`](../docs/BUILDING_APPS.md).

---

## Why packaged apps

Historically, building an app on the agentic desktop required weaving code into
10+ locations across the codebase: a migration in `migrations/`, a data layer
file in `data_layer/`, tool functions in `tools/`, registrations in
`__init__.py` and `mcp_server.py`, a category in `tool_routes.json`, a guide in
`prompts/guides/`, API endpoints spliced into `agent.py`, a UI component in
`web/src/apps/`, and a manifest entry in `registry.js`.

That is the "absorbed into the OS" model. Apps become inseparable from the
platform. You can't remove one without surgery across a dozen files. You can't
add one without touching the same dozen files. And critically, **Skipper can't
safely build or modify apps** because every change requires coordinated edits
to core platform infrastructure.

A packaged app follows a **macOS-style model** instead: each app is a
self-contained package in a single folder. Installing an app means dropping the
folder in. Removing it means deleting the folder. The platform discovers,
loads, and connects apps at runtime through well-defined extension points.

---

## Design Principles

1. **Vertical, not horizontal.** All of an app's code lives in one directory.
   No file belonging to App X should live outside `apps/x/`.

2. **Convention over configuration.** The platform discovers app capabilities
   by the presence of well-known files (`tools.py`, `routes.py`, `ui/`,
   `guide.md`), not by explicit registration in a central manifest.

3. **Loose coupling via events.** Apps never import each other's code. They
   communicate through a platform event bus. If App A needs to react to
   something App B did, it subscribes to an event — it doesn't call B's
   functions.

4. **Platform services, not shared libraries.** Common capabilities (links,
   notifications, entity types, documents) are platform services with stable
   APIs. Apps call platform services; they don't reach into each other's data
   layers.

5. **Safe to fail.** A broken app cannot crash the platform or other apps.
   The loader catches errors per-app and disables the app with a log message.
   This is essential for Skipper-built apps.

6. **Incremental adoption.** Legacy apps (the older "absorbed" architecture)
   continue to work unchanged. New packaged apps coexist alongside them. Over
   time, legacy apps can be migrated one at a time.

7. **UI ↔ chat parity.** Anything a user can do through an app's UI must also be
   doable through chat. Every meaningful UI capability has a corresponding MCP
   tool in the app's `tools.py` (with a docstring the LLM turns into a schema),
   plus a `tool_category` in `manifest.yaml` for routing and a `guide.md`. A
   UI-only app is incomplete — ship the tools too, so Skipper can do by
   conversation whatever the user can do by clicking. See the section below for
   the full contract.

---

## Core principle: UI ↔ chat parity

Anything a user can do through an app's **UI must also be doable through chat.**
Skipper is a conversational assistant first; an app that only works by clicking
is incomplete. Concretely, for every meaningful UI capability:

- Expose a corresponding **MCP tool** in the app's `tools.py` (a public function
  with a docstring the platform turns into the tool schema — see the Tool
  Loader). Helpers stay underscore-prefixed so they aren't registered.
- Declare a **`tool_category`** in `manifest.yaml` (description + keywords) so
  chat routes to the app, and ship a **`guide.md`** describing the tools.
- A read-only/viewer UI still needs lookup tools (e.g. "show my …"); a UI that
  creates/edits data needs create/update tools.

This matters doubly for **Evolve**: when Skipper builds a new app, it must build
the chat tools alongside the UI, or the app fails this contract. App reviews
should reject a UI-only app that has no matching tools.

---

## App Package Structure

```
apps/
  recipes/
    manifest.yaml          # REQUIRED — metadata, capabilities, dependencies
    __init__.py            # REQUIRED — makes apps.recipes an importable package
    migrations/
      001_initial.sql      # app-specific schema (TIMESTAMPTZ, TEXT ids)
    data.py                # data layer (SQL CRUD; calls digest_record)
    store.py               # optional business logic
    tools.py               # MCP / chat tool functions
    routes.py              # FastAPI router (bare; platform mounts /api/apps/<id>/)
    handlers.py            # optional @subscribe handlers + job handlers
    hooks.py               # optional register_hooks() — platform provider slots
    runner.py              # optional background pipeline
    guide.md               # agent guide (how to operate the tools)
    help.md                # user manual (shown in-app; returned by get_app_help)
    think.md               # thinking-domain prompt (only if manifest.thinking set)
    ui/
      index.js             # REQUIRED for a UI — exports the launcher registry array
      RecipeListApp.jsx    # React components
      RecipeDetailApp.jsx
    specs/
      SPEC.md              # this app's prose design spec
      _capability.yaml     # the app's C/F/S tree (Capability/Feature/Specification)
      <feature>/           #   — Evolve's source of truth; travels WITH the app
        _feature.yaml      #     (in-repo or in the app's own repo), see EVOLVE.md §4
        <spec>.yaml
    tests/                 # the app's OWN tests — co-located so they ship with the app
      __init__.py          #   (makes it discoverable when mounted at apps/<id>/tests)
      test_*.py            #   unit/smoke tests; a spec's `unit` bound test lives here
```

The platform discovers capabilities by **well-known file names** — no central
registration. A file's mere presence wires it in:

| Path | Required? | What the platform does with it |
|------|-----------|--------------------------------|
| `manifest.yaml` | **yes** | The only file the loader *must* read; everything else is discovered. |
| `__init__.py` | **yes** | Makes `apps.<id>` importable so intra-app imports (`from apps.<id> import data`) resolve. |
| `migrations/*.sql` | if the app owns data | Run in order inside the `app_<id>` schema; tracked in `public.app_migrations`. |
| `tools.py` | for any app with chat tools | Public functions → MCP tools (docstring → schema). May expose `get_guide_context()` for dynamic guide injection. |
| `routes.py` | if the app has a REST API | Must export `router` (a bare `APIRouter`); mounted at `/api/apps/<id>/`. |
| `handlers.py` | optional | `@subscribe`-decorated event handlers and `job_types` handlers. |
| `hooks.py` | optional | A `register_hooks()` function called on load to register platform provider slots (backlog/activity/nag providers, schedule claims, **prompt-context providers** via `app_platform.prompt_context.register_prompt_context`) and lifecycle entries — background workers via `app_platform.lifecycle.register_background_task(task_id, factory)` (factory = the worker function, not its result) and graceful shutdown via `register_shutdown_hook(fn)` — so the platform never imports the app. |
| `guide.md` | for any app with tools | Agent-facing guide, loaded into context when the app's tool category is active. |
| `help.md` | yes (any app a user interacts with) | **User-facing manual**, shown via the in-app **?** button and returned by the `get_app_help` chat tool. Distinct from `guide.md`. |
| `ui/index.js` | for any app with a UI | Default-exports the launcher registry array (see UI Collector). |
| `think.md` | convention | Your thinking-domain prompt, if you keep one — **your own handler loads it**; the platform does not (the manifest `thinking:` block is inert, see extension point #8). |
| `data.py` / `store.py` / `runner.py` | convention | App-internal data layer / business logic / background pipeline. Not discovered by name — imported by the app's own `tools.py`/`routes.py`. |
| `specs/SPEC.md` | convention | The app's own prose design spec. |
| `specs/**/*.yaml` | convention | The app's C/F/S tree (Evolve's source of truth), co-located so it travels with the app whether in-repo or in its own repo. See [EVOLVE.md §4](EVOLVE.md). |
| `tests/**/*.py` | convention | The app's own tests, co-located so they ship with the app (a distributable app carries its tests). `tests/__init__.py` makes them discoverable when mounted at `apps/<id>/tests`. Platform/cross-cutting tests live in the top-level `tests/`. |

> **`guide.md` vs `help.md` — ship both.** `guide.md` tells *the agent* how to
> drive the tools (names, args, edge cases). `help.md` is a full *user manual*
> shown in the app. Don't collapse them into one; an app that ships only
> `guide.md` forces the agent to improvise user help.

### manifest.yaml

Every app package must have a manifest. This is the only file the platform
reads to understand the app.

```yaml
id: recipes
name: Recipes
version: 1.0.0
description: Create and manage family recipes with ingredients, steps, and categories

# Entity types this app owns (auto-registered on install)
entity_types:
  - prefix: re
    name: recipe
    id_format: "re-"
    table: recipes

# Platform services this app depends on
platform_deps:
  - links        # uses the link system
  - images       # uses image storage
  - documents    # can link to docs

# Events this app emits
emits:
  - recipe.created
  - recipe.updated
  - recipe.deleted

# Events this app subscribes to
subscribes:
  - entity.linked     # react when something is linked to a recipe
  - entity.unlinked

# Tool category for tool_routes
tool_category:
  description: "Create and manage family recipes with structured ingredients, steps, and categories"
  keywords:
    - recipe
    - cook
    - ingredient
    - meal
    - dinner
    - food

# UI registrations
ui:
  apps:
    - id: recipes
      name: Recipes
      icon: UtensilsCrossed
      component: RecipeListApp
      singleton: true
    - id: recipe
      name: Recipe
      icon: UtensilsCrossed
      component: RecipeDetailApp
      singleton: false
      hidden: true

# Job types this app registers
job_types:
  # - type: recipe_import
  #   handler: handlers.handle_import
  #   max_concurrent: 1
```

#### Manifest field reference

These are the fields the loader actually reads (`app_platform/manifest.py`).
Anything else in the file is ignored.

| Field | Required | Purpose |
|-------|----------|---------|
| `id` | **yes** | App package id. Must equal the folder name under `apps/`. |
| `name` | yes | Display name (defaults to `id`). |
| `version` | — | Semantic version string (defaults to `0.0.0`). |
| `description` | yes | One-line summary; also used for store listings. |
| `core` | — | `true` marks the app as platform-required (see below). Optional apps omit it or set `false`. |
| `schema` | — | Override the Postgres schema name; defaults to `app_<id>`. |
| `platform_deps` | — | List of platform services the app uses (documentation/intent). |
| `entity_types` | — | Entity types the app owns — `prefix`, `name`, `id_format`, `table`. Auto-registered so the entities are linkable. |
| `tool_category` | — (but **required for any app with tools**) | `description` + `keywords` used to route chat to the app's tools. Set to `null` for a UI-only app whose chat tools live at the platform level. |
| `emits` / `subscribes` | — | Event names this app publishes / listens for. |
| `ui.apps[]` | — | UI registrations: `id`, `name`, `icon`, `component`, `singleton`, `hidden`. Mirrors `ui/index.js`. |
| `job_types[]` | — | Background job handlers: `type`, `handler` (`module.fn`), `max_concurrent`, `cancel_on_shutdown`. |
| `thinking` | — | Thinking-domain declaration — **parsed but currently INERT** (documentation of intent only; the working mechanism is `register_domain` — see extension point #8). |
| `config[]` | — | Per-app settings surfaced in the UI cog wheel (see below). |

> **Note:** the loader does **not** read `package`, `web`, or `requires` —
> those aren't part of the contract. `platform_min_version` is conventional but
> advisory only (this is a single-platform deployment; the platform is always
> at its own latest version). Don't add fields expecting the loader to act on
> them; the table above is the whole surface.

#### Required (core) apps vs optional apps

- **Optional apps** live in their own repo (`skipperbot-app-<name>`) and are
  installed by dropping the folder into `apps/<id>/`. Delete the folder and
  restart to uninstall. This is the normal case for an app you build.
- **Required (core) apps** ship inside the platform repo and the platform
  **refuses to boot without them** — the loader holds a `REQUIRED_APPS` list
  and calls `require_apps(...)` after loading; a missing or errored core app
  aborts startup with the exact app to fix. `uninstall_app()` also refuses to
  remove them. A core app sets `core: true` in its manifest to document the
  intent. You generally won't author a core app — those are platform-owned.

#### Per-app settings (`config:`)

An app that needs user-tunable settings declares them under `config:` in the
manifest. Each entry has a `key`, `type`, `default`, `label`, `description`,
and optional `secret`, `choices`, `requires_restart`. The platform renders
them in the app's toolbar cog wheel (and the Settings app), and stores values
in `public.app_config` scoped to `app:<id>`. Read them through the platform
settings service rather than parsing the manifest yourself. Uninstalling an app
with full purge also clears its `app_config` rows.

> **⚠ The auto-scope trap.** `app_platform.settings.get(key)` **auto-scopes to
> the calling app** (`app:<id>`) when no `scope=` is given. That's what you
> want for your own settings — but it means reading a **platform-level**
> setting from app code silently returns your `default` instead of the real
> value unless you pass `scope="platform"` explicitly. This bug is invisible
> (no error, just a wrong default) and has bitten core apps in production.
> Rule: your own settings → no scope; anything platform-wide →
> `scope="platform"`, always.

#### Declared dependencies between apps

The default for cross-app needs is the entity query service (reads) and events
(writes) — see [Cross-App Data Access](#cross-app-data-access). Rarely, a
platform feature or app genuinely *requires* another app to be present to work
at all (e.g. an autonomous loop that reads another app's data as its source of
truth). For that case the platform exposes
`app_platform.loader.require_apps(*app_ids)`, called at the dependent feature's
entry point so a missing prerequisite fails loudly with a clear remediation
message instead of degrading silently. Reach for this only when the dependency
is genuinely load-bearing; otherwise stay decoupled via events.

---

## Platform Extension Points

The platform provides **8 extension points** that app packages hook into.
Each is a discoverer/loader that runs at startup (and optionally on hot-reload).

### 1. Schema Migrator

Everything lives in **one Postgres database**, but each app gets its own
**Postgres schema** (namespace). The platform's core tables stay in `public`;
each packaged app's tables live in `app_<id>` (e.g., `app_recipes.recipes`,
`app_recipes.recipe_categories`).

```sql
-- Platform creates the schema when loading a new app
CREATE SCHEMA IF NOT EXISTS app_recipes;
```

App migrations run inside their schema. The `search_path` is set to the app's
schema before executing, so migration SQL can use unqualified table names:

```sql
-- apps/recipes/migrations/001_initial.sql
-- Runs with search_path = app_recipes
CREATE TABLE recipes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    ...
);
```

The platform tracks applied migrations in a `public.app_migrations` table:

```
app_migrations
  app_id       TEXT
  filename     TEXT
  applied_at   TIMESTAMPTZ
  PRIMARY KEY (app_id, filename)
```

**Benefits of per-app schemas:**
- **Namespace isolation** — no table name collisions between apps
- **Clean uninstall** — `--purge` drops the schema (`DROP SCHEMA … RESTRICT`;
  see Migration Strategy for why RESTRICT, never CASCADE)
- **Visibility** — `\dn` in psql shows all installed apps at a glance
- **Permissions** — future option to restrict an app's DB user to only its
  own schema
- **Data layer simplicity** — app code uses unqualified table names; the
  platform sets `search_path` on each connection to
  `app_<id>, public` so apps can read platform tables (links, entity_types,
  etc.) but write only to their own schema

If an app is removed, its schema is **preserved** by default (data safety).
A `--purge` flag drops the schema and cleans `app_migrations`.

### 2. Tool Loader

The platform imports `apps/<id>/tools.py` and collects all public functions.
Each function becomes an MCP tool, using the same docstring-to-schema
convention as today.

- Tools are namespaced: function `create_recipe` in `apps/recipes/tools.py`
  becomes MCP tool `create_recipe` (no namespace prefix needed if names are
  unique; the loader checks for collisions)
- The tool category from `manifest.yaml` replaces the entry in
  `tool_routes.json` — the platform builds the route table dynamically
- `guide.md` is loaded when the category is activated, same as today

### 3. Route Mounter

The platform imports `apps/<id>/routes.py`, which must export a FastAPI
`APIRouter`. The platform mounts it at `/api/apps/<id>/`.

```python
# apps/recipes/routes.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/")
async def list_recipes(category: str = "", q: str = ""):
    ...

@router.get("/{recipe_id}")
async def get_recipe(recipe_id: str):
    ...
```

The platform does:
```python
app.include_router(pkg.router, prefix=f"/api/apps/{pkg.id}")
```

### 4. Entity Type Registrar

At startup, the platform reads `entity_types` from each manifest and ensures
they exist in the `entity_types` table. This makes the app's entities linkable
and resolvable from day one — no separate migration needed for the registry.

### 5. UI Collector

The web app discovers `apps/*/ui/index.js` files at build time via Vite's
`import.meta.glob` (in `web/src/apps/registry.js`) — no pre-build step, no
generated file in source control. Each `index.js` **default-exports an array**
of app registrations that get merged into the runtime app registry. Every
entry the loop discovers is auto-tagged with **`appPackage: true`** so the
launcher can style packaged apps distinctly.

```js
// apps/recipes/ui/index.js
import { lazy } from "react";
import { UtensilsCrossed } from "lucide-react";

export default [
  {
    id: "recipes",
    name: "Recipes",
    icon: UtensilsCrossed,
    component: lazy(() => import("./RecipeListApp")),
    singleton: true,
  },
  {
    id: "recipe",
    name: "Recipe",
    icon: UtensilsCrossed,
    component: lazy(() => import("./RecipeDetailApp")),
    singleton: true,
    hidden: true,
  },
];
```

**`index.js` must export the registry array** — it is *not* a DOM-mount entry
point. Do not call `createRoot(...)` there; the platform shell mounts your
`component` for you. Because packaged-app UI files live outside `web/`, Vite
needs help resolving their npm imports: `web/vite.config.js` aliases `react`,
`react-dom`, and `lucide-react` into `web/node_modules`. If your UI pulls in a
new npm dependency, add it to the `packagedAppDepAliases` map there — that is
the only ongoing tax of the convention.

#### Component layout requirements

The desktop mounts each app inside a fixed-size flex region of the window
shell. To fill that region correctly, the app's **root element** must be a
column flex container that takes the full width and height of its parent:

```jsx
export default function MyApp({ ... }) {
  return (
    <div className="flex flex-col h-full w-full bg-zinc-950 text-zinc-100">
      {/* tab bar / toolbar — fixed height, must not be squeezed */}
      <div className="... shrink-0">...</div>

      {/* scrollable content — must min-h-0 inside a flex column */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        {/* app content */}
      </div>
    </div>
  );
}
```

The three classes that are easy to forget but matter:

- **`w-full`** on the root — without it the app shrinks to its content width
  and leaves empty space on the right side of the window.
- **`shrink-0`** on any fixed-height bar (tabs, toolbars) — without it the bar
  collapses when content overflows.
- **`min-h-0`** on the scrollable child of a flex column — without it the
  child refuses to shrink and `overflow-y-auto` never engages, so the whole
  app scrolls instead of just the inner panel.

These are general flexbox-in-Tailwind gotchas, not app-platform conventions —
but missing any of them produces visible layout bugs that look like the
window shell is broken. Match the pattern above when writing a new app's
root component.

### 6. Event Bus

The platform provides a lightweight publish/subscribe event bus. Apps emit
events when things happen; other apps (or the platform itself) subscribe.

```python
# Platform service: apps/recipes/tools.py
from app_platform.events import emit

def create_recipe(...):
    recipe = _data.create(...)
    emit("recipe.created", {
        "id": recipe["id"],
        "title": recipe["title"],
        "created_by": created_by,
    })
    return recipe
```

```python
# Another app subscribing: apps/meal_planner/handlers.py
from app_platform.events import subscribe

@subscribe("recipe.created")
def on_recipe_created(event):
    # Maybe add to weekly suggestions
    ...
```

**Event bus characteristics:**
- **In-process, synchronous** — not a separate service. `emit()` persists the
  event to `app_events` and runs subscribers **inline, in the same call** (a slow
  handler blocks the emitter — keep handlers light or hand off to a job).
- **Best-effort delivery** — a subscriber exception is logged and isolated (not
  propagated), and is **not automatically retried**. The delivery-tracking +
  `retry_failed_deliveries()` in `events.py` are scaffolding that is **not wired
  to a scheduler**, so there is **no at-least-once guarantee** today. See
  [specs/EVENTS.md](EVENTS.md) for the full state.
- **No subscribers yet** — currently only `bounties`/`chores` *emit*; nothing
  subscribes, so the bus is effectively a write-only event log for now.
- **Typed events** — event names follow `<domain>.<action>` convention.

**Standard platform events** — the intended catalog and naming convention.
*(Aspirational: today only `bounties`/`chores` emit any events; the rows below are
the convention to follow, not a guarantee that each is currently emitted.)*

| Event | Payload | When |
|-------|---------|------|
| `entity.created` | `{id, type, created_by}` | Any entity created |
| `entity.updated` | `{id, type, fields, updated_by}` | Any entity updated |
| `entity.deleted` | `{id, type, deleted_by}` | Any entity deleted |
| `entity.linked` | `{source_id, target_id, relation}` | Link created |
| `entity.unlinked` | `{source_id, target_id, relation}` | Link removed |
| `job.completed` | `{job_id, job_type, result}` | Job finished |
| `job.failed` | `{job_id, job_type, error}` | Job failed |
| `notification.sent` | `{user, channel, entity_id}` | Notification delivered |

### 7. Job Handler Registry

Apps that need background processing export handler functions. The platform
discovers them from the manifest and registers them with `job_dispatcher`.

```python
# apps/recipes/handlers.py
async def handle_import(job, ctx):
    """Import recipes from a URL or file."""
    ...
    return "Imported 12 recipes"
```

```yaml
# In manifest.yaml
job_types:
  - type: recipe_import
    handler: handlers.handle_import
    max_concurrent: 1
```

### 8. Thinking Domain Registrar

Apps can run **periodic autonomous LLM passes** ("thinking domains") — but the
mechanism is NOT the manifest.

> **⚠ The manifest `thinking:` block is currently INERT.** The loader parses it
> and logs the registration, and that is all
> (`app_platform/loader.py::_register_thinking_domain` is an acknowledged
> stub). No cron `schedule:` is honored, `prompt_file:` is never loaded as a
> system prompt, `tools:` is not scoped, `model:` is not consumed, and nothing
> is written to the scheduler. An app that declares `thinking:` in its manifest
> and stops there has an inner voice that **silently never runs** (a real
> shipped app made exactly this mistake). You may still declare the block as
> documentation of intent, but do not rely on it.

**The working mechanism** (what the core apps actually do — see
`apps/documents/handlers.py` for the minimal example):

1. **Register a handler** from your `handlers.py` at import time:

   ```python
   # apps/<id>/handlers.py — the platform loader imports this at app-load time
   from domain_modules import register_domain

   async def my_domain_handler(domain: dict, budget_status: dict) -> dict:
       """One thinking cycle. Build your own prompt (read your think.md
       yourself if you keep one), call agent_loop.run(...), and return the
       cycle-result dict (reasoning, actions_taken, next_check_seconds, ...)."""
       ...

   register_domain("my_domain", my_domain_handler)
   ```

2. **Create the scheduler row** — the scheduler runs rows in the
   `public.thinking_domains` table (interval-driven via `check_interval` /
   `next_check`, **not cron**). Seed the row idempotently (a Python seed
   script run once at install — see the Python-migration note in the
   Schedules section).

The handler owns everything the manifest block *implies*: it loads its own
prompt file, picks its own model tier, and builds its own tool list.

> **Direction of travel (specs/CONSCIOUSNESS.md):** Skipper is ONE
> consciousness; thinking domains are **alarm clocks, not separate minds**. The
> platform's own domains are being converted so the scheduler merely raises an
> owed event and a voice **skill** runs the turn through the shared attention
> system. Prefer that shape for anything user-facing: background work belongs
> in `job_types` + `public.schedules`; anything said to a user goes through
> `create_notification`. A periodic silent analysis pass is fine as a plain
> domain handler.

---

## Platform Services

These are capabilities provided by the platform that any app can use. They are
**not** apps themselves — they're core infrastructure, exposed under the stable
`app_platform.*` package namespace.

| Service | Import | Purpose |
|---------|--------|---------|
| `app_platform.db` | `execute_in_schema`, `execute_returning_in_schema`, `fetch_one_in_schema`, `fetch_all_in_schema`, `fetch_all_vector_in_schema`, `scoped_conn` (+ re-exported `fetch_one`, `fetch_all` for platform tables) | Database access — use the `*_in_schema` forms for your own tables |
| `app_platform.events` | `emit`, `subscribe` | Event bus |
| `app_platform.memory` | `digest_record` | **Semantic memory ingestion (required on every CRUD — see below)** |
| `app_platform.links` | `create_link`, `ensure_edge`, `get_links`, `get_blast_radius`, `delete_link`, `delete_links_for_entity` | Entity linking — the platform `link` entity type (soft references in `public.links`) |
| `app_platform.images` | `save_image`, `get_image`, `get_all_images`, `update_image_title`, `delete_image` | The platform `image` entity type (`public.images`); the Images app is just its viewer |
| `app_platform.notifications` | `create_notification` | Multi-surface user notifications (see below — **never call channel-specific senders directly**) |
| `app_platform.documents` | `create_doc`, `get_doc`, `update_doc` | Document store |
| `app_platform.auth` | `current_principal`, `scope_user` | Authentication |
| `app_platform.jobs` | `submit_job` | Job queue |
| `app_platform.schedules` | `create_schedule`, `list_schedules`, `complete_schedule`, `get_due_schedules` | Recurring schedules |
| `app_platform.entities` | `query_entities` | Cross-app read-only entity queries |
| `app_platform.time` | `now`, `get_timezone`, `utcnow`, `to_local` | Timezone-aware clock (see the time rule below) |

Apps import these via the stable `app_platform` package. The platform guarantees
backward compatibility of these APIs — app code should never need to change
because the platform internals changed. (Several `app_platform.*` modules are
thin facades that forward to the owning core app, e.g.
`app_platform.notifications` → `apps.notifications.store`.)

---

### Required: Memory digestion on every CRUD

Every packaged app's `data.py` **must** call `digest_record` after every
successful create / update / delete / completion. This pushes the entity's
record through `app_platform.memory` → DUMB_MODEL fact extraction →
`memory_store` with a text embedding. That is the mechanism that lets chat
disambiguate ambiguous user messages later.

> **Why this is non-negotiable:** when a kid says "I mowed the yard", the
> tool router loads multiple apps' tools by keyword and the LLM has to pick
> the right one. The decisive signal is semantic memory recall —
> `search_memories("mowed the yard")` returns the bounty memory with its
> `bnt-` id (and not a chore memory), so the LLM calls the Bounties tool.
> An app that never calls `digest_record` is invisible to that recall and
> will lose every disambiguation contest against apps that do.

**Pattern (mirror this exactly — see `apps/bounties/data.py` and
`apps/chores/data.py` for working examples):**

```python
from app_platform.memory import digest_record

_RECIPE_HINT = (
    "Focus on: the recipe title, cuisine, key ingredients, tags, prep time. "
    "These memories are how chat recalls which recipe is meant when the "
    "user says 'make the chicken thing'."
)

def create_recipe(title: str, ..., by: str = "") -> dict:
    row = execute_returning_in_schema(SCHEMA, "INSERT INTO recipes ...", (...))
    recipe = _recipe_row(row)
    if recipe:
        digest_record(app_id="recipes", entity_type="recipe", action="created",
                      entity_id=recipe["id"], record=recipe, by=by,
                      context_hint=_RECIPE_HINT)
    return recipe

def update_recipe(recipe_id: str, by: str = "", **fields) -> dict | None:
    # ... do the update ...
    if recipe:
        digest_record(app_id="recipes", entity_type="recipe", action="updated",
                      entity_id=recipe["id"], record=recipe, by=by,
                      context_hint=_RECIPE_HINT)
    return recipe

def delete_recipe(recipe_id: str, by: str = "") -> bool:
    recipe = get_recipe(recipe_id)     # fetch BEFORE deleting
    ok = execute_in_schema(SCHEMA, "DELETE FROM recipes WHERE id=%s", (recipe_id,)) > 0
    if ok and recipe:
        digest_record(app_id="recipes", entity_type="recipe", action="deleted",
                      entity_id=recipe_id, record=recipe, by=by)
    return ok
```

The full `digest_record` signature:

```python
digest_record(
    app_id: str,          # app package ID, e.g. "recipes"
    entity_type: str,     # human label, e.g. "recipe"
    action: str,          # "created", "updated", "deleted", "completed", ...
    entity_id: str,       # the entity's ID, e.g. "re-abc123"
    record: dict,         # full record dict as returned from the data layer
    by: str = "",         # who acted (user_id or "system")
    context_hint: str = "",  # extraction focus hint — what attributes matter
    blocking: bool = False,  # True runs extraction inline (use for backfills)
) -> None
```

For create/update/completed it runs LLM extraction in a background thread by
default; for deleted it writes one direct memory synchronously (no LLM needed).

**Rules:**

1. **Every mutation calls it.** create / update / delete / completed /
   submitted / approved — any action a user might later refer to in chat.
2. **After the DB write succeeds**, not before. So IDs and timestamps are
   present in `record`.
3. **For deletes, fetch the record first** so you can pass it to
   `digest_record` after the row is gone.
4. **Thread `by` through.** Add `by: str = ""` to your data-layer signatures
   and pass `acted_by` from your routes/tools. The user attribution is
   stored on the memory.
5. **Write a `_HINT` constant** explaining what attributes matter most for
   that entity type. The LLM reads it as `context_hint` and prioritizes
   those fields in the extracted facts. Bad hints → useless facts.
6. **Augment the record before digesting if a related field would help
   recall.** A `chore` row contains only `zone_id`; the memory is far more
   useful if it says "*Dust* is a chore on Thursdays in *Bedroom – E & C*"
   — so the data layer joins the zone name in before passing the dict.
7. **Skip pure ledger / transaction rows** (e.g. `bounty_transactions`,
   `chore_completions`) — those would generate one memory per check-off and
   bury the searchable signal. Digest the *template* / *definition*, not
   every instance. (Exception: completions with notable detail like a
   submission note may be worth digesting; default is skip.)
8. **Never raise.** `digest_record` already swallows errors and logs them —
   you don't need a try/except around it. But your CRUD must keep working
   if memory is briefly unavailable.

**Backfill seed data.** If the app's first migration seeds entities (like
the Chores app does from the Google sheet, or like Recipes did from JSON),
also write a one-shot backfill script that calls `digest_record` with
`blocking=True` for each pre-existing row. Otherwise everything created
before the data-layer wiring is invisible to recall. Place it next to the
seed migration, e.g. `apps/<id>/migrations/00X_backfill_memories.py`.

**Verification.** After install / backfill, run a recall sanity check:

```python
from memory_store import search_memories
for q in ["I dusted my room", "cleaned the toilet"]:
    for m in search_memories(query_text=q, max_results=3):
        print(m["about"], "—", m["content"][:120])
```

If the top hit isn't your app's entity, your hints are too vague or you're
missing a digest call — fix it before shipping.

---

### Required: Notify via `create_notification`, not channel-specific senders

Apps must **never** call `discord_bot.send_dm`, `fcm_sender.send_push_to_user`,
`pushover_tool.send_pushover_notification`, or any other channel-specific
sender directly from a handler, runner, or job. That bypasses:

* **The notification record** in `public.notifications` (auditable, queryable,
  replayable).
* **Multi-surface fan-out** — a single insert ends up on Discord *and*
  Pushover *and* FCM mobile push *and* the WebSocket-connected web UI.
* **Chat-history persistence** — `notification_delivery` auto-saves the
  message into the recipient's chat log via `save_notification`, so a
  subsequent "did it" / "I'll do that later" reply has the original message
  in session context.
* **Async/sync safety** — `send_dm` is async; calling it without `await`
  from a sync handler returns an unevaluated coroutine and silently fails
  with no Discord delivery (lesson from the chores 9 AM bug in May 2026).
* **Skipper's own memory of having spoken** — `create_notification` also
  enters the message into Skipper's **consciousness log**
  (see `specs/CONSCIOUSNESS.md`), so when the user replies to your app's
  message, Skipper knows what it said and answers in context. A raw channel
  send leaves Skipper **context-blind about its own words** — the user
  replies "yes, do that" and Skipper has no idea what "that" is. (A real
  shipped app had exactly this bug — hand-rolled Discord/Pushover/WS sends —
  and was fixed by replacing all three with one `create_notification` call.)

**Pattern (mirror this exactly):**

```python
from app_platform.notifications import create_notification

# In a sync or async job handler:
notif = create_notification(
    recipient=username,             # public.users.name — lowercased internally
    message=body,                   # markdown supported
    source_type="chores_morning",   # free-form; surfaces in the audit trail
    source_id=job.get("id", ""),    # the job/entity that triggered this
    channel="all",                  # see channel matrix below
    delivered=False,                # picked up by the delivery loop
)
```

A separate background process (`notification_delivery.deliver_pending_notifications`,
polled every ~30s from the reminder scheduler loop) reads undelivered rows
and fans them out. Your handler is done the moment the row is inserted.

**Channel values:**

| `channel` | Discord DM | Pushover | FCM mobile | WebSocket | Chat log |
|-----------|:----------:|:--------:|:----------:|:---------:|:--------:|
| `""` *(unset — the default)* | *per the `default_channels` setting* | | | ✓ | ✓ |
| `"discord"` | ✓ | | | ✓ | ✓ |
| `"push"` | | ✓ | | ✓ | ✓ |
| `"both"` | ✓ | ✓ | | ✓ | ✓ |
| `"app"` | ✓ | | | ✓ | ✓ |
| `"mobile"` | | | ✓ | ✓ | ✓ |
| `"all"` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `"none"` | | | | ✓ | ✓ |

An **empty/unset** channel resolves to the operator-configurable
`default_channels` setting (Settings → Notifications; factory default
`discord,pushover`) — so omit `channel` unless your app has a real reason to
force a surface. `"none"` sends no external push at all (used e.g. by schedule
seeds whose *job* does its own notifying). WebSocket and chat-log delivery
happen on every row regardless of `channel`.

**When you'd legitimately reach past the abstraction:** you wouldn't. If
your app needs a new surface (e.g. SMS), add it to `notification_delivery`
and every existing handler benefits. If your app needs message-channel
preferences per-user, the user-level prefs belong in `public.users` (or a
new `user_notification_prefs` table) and `notification_delivery` consults
them — not your handler.

---

### Required: Recurring work goes in `public.schedules`, never `public.jobs`

The platform separates **what to run** (the schedule, recurrence rule, time
of day) from **a single execution** (the job row). Apps **must not**
create job rows for recurring work — that's `public.schedules`' job.

| Table | What it represents | Who writes |
|-------|--------------------|------------|
| `public.schedules` | A *recurring intent* — "run job X every weekday at 9 AM" | Apps (via `app_platform.schedules.create_schedule`) |
| `public.jobs` | A single *execution instance* of work — claimed, started, completed, retried | The dispatcher (`schedule_job_trigger.check_schedule_jobs` submits one per fire; the job dispatcher claims and runs it) |

`public.jobs` has a leftover `schedule_expr` cron column — **it is
deprecated.** Never write to it. The active code path is:

```
schedules row (recurrence_type + recurrence_rule + time_of_day + next_due)
  ↓  schedule_job_trigger.check_schedule_jobs (polled ~30s)
  ↓  when next_due passes, submit_job(job_type, ...) creates a jobs row
  ↓  job_dispatcher claims the row and awaits the handler from manifest job_types
  ↓  on completion, complete_schedule(...) advances next_due
```

**Pattern (mirror this — see `apps/chores/migrations/003_seed_morning_schedule.py`):**

```python
"""Seed the chores_morning schedule (9:00 AM daily)."""
from app_platform.schedules import create_schedule, list_schedules

JOB_TYPE = "chores_morning"

def run():
    existing = list_schedules(active_only=False)
    if any(s.get("linked_entity_type") == "job"
           and s.get("linked_entity_id") == JOB_TYPE for s in existing):
        print(f"{JOB_TYPE} schedule already exists — skipping")
        return

    sch = create_schedule(
        title="Daily Chores Morning Push (9:00 AM)",
        created_by="system",
        category="general",
        assigned_to="john",
        description="Handler: apps/chores/handlers.py:handle_chores_morning.",

        # --- timing ---
        recurrence_type="daily",            # daily | weekly | monthly | rrule | interval | cron
        recurrence_rule={"every": 1},       # shape depends on recurrence_type — see below
        time_of_day="09:00",                # local wall-clock

        # --- what to fire ---
        linked_entity_type="job",           # tells schedule_job_trigger to submit_job
        linked_entity_id=JOB_TYPE,          # the manifest job_type whose handler will run

        notify_channel="none",              # the JOB notifies on its own via create_notification
    )
    print(f"Created {sch['id']} (next_due: {sch.get('next_due')})")

if __name__ == "__main__":
    run()
```

**Recurrence options** (`recurrence_type` → `recurrence_rule` shape):

| type | rule shape | example |
|------|------------|---------|
| `daily` | `{"every": N}` — every N days | `{"every": 1}` daily |
| `weekly` | `{"days": ["mon","tue",...]}` | `{"days": ["mon","wed","fri"]}` |
| `monthly` | `{"week": N, "weekday": "mon"}` | 1st Monday: `{"week": 1, "weekday": "mon"}` |
| `interval` | `{"days": N}` — every N days, no anchor to weekday | `{"days": 180}` for 6-month maintenance |
| `cron` | `{"expr": "0 17 * * 1-5"}` | weekday 5 PM |
| `rrule` | `{"rrule": "FREQ=...;BYDAY=...", "dtstart": "ISO"}` | full RFC 5545 — last-business-day-of-month etc. |

Prefer `daily` / `weekly` / `monthly` for readability. Fall back to `rrule`
only when you need calendar arithmetic the simpler types can't express
(e.g. *last weekday of the month*, *every other Tuesday*).

**Rules:**

1. **The seeder is a Python migration**, not SQL. Naming convention:
   `apps/<id>/migrations/NNN_seed_<name>_schedule.py`. **The migrator runs
   `.sql` files ONLY — nothing runs `.py` migrations automatically.** There is
   no installer hook: after installing/updating the app you (or your install
   notes) must run it by hand, once, inside the agent container:

   ```bash
   docker compose exec agent python apps/<id>/migrations/NNN_seed_<name>_schedule.py
   ```

   (Native runtime: `.venv/bin/python apps/<id>/...`.) That's why rule 2 —
   idempotence — is mandatory: the script must be safe to run again on every
   redeploy. Document the command in your app's README install steps; an app
   that forgets this ships a schedule that never fires.
2. **It's idempotent.** Check `list_schedules` for an existing row with the
   same `linked_entity_id` before inserting. Re-running the migration on
   redeploy must be safe.
3. **The handler is declared in `manifest.yaml` under `job_types`.** The
   schedule only points at the *job_type string*; the platform resolves it
   to your handler at startup.
4. **One schedule per cadence.** If your app needs morning *and* evening
   nudges, that's two schedule rows pointing at two different job_types,
   not one schedule that "knows" both.
5. **Never INSERT into `public.jobs` from app code.** Even for one-off
   work, use `from app_platform.jobs import submit_job; submit_job(...)`
   so the dispatcher claims and tracks it properly.
6. **Never write to `jobs.schedule_expr`.** It's a deprecated leftover.

**Catch-up behavior.** If the agent was down at `next_due`, the scheduler
will fire the missed occurrence as soon as it comes back up (the trigger
treats any `next_due <= now()` row as due). If you need a *no-catch-up*
schedule (e.g. a market-open trigger that's pointless after the bell), set
a window in the handler — fail fast if the current time is outside the
intended window.

---

### Required: Use platform time, never naive local time

Apps must **never** read the wall clock through the standard library's
local-time calls: `datetime.now()` (no tz), `datetime.today()`, `date.today()`,
`datetime.utcnow()` (naive), or `time.localtime()`. The platform runs in a
container/Pi whose system clock is typically UTC, while the family lives in a
different timezone — so a naive `datetime.now()` is both timezone-wrong and
returns a naive datetime that silently mismatches the `TIMESTAMPTZ` columns and
aware platform datetimes. The symptoms are subtle and bad: reminders fire an
hour off, "today" rolls over at the wrong midnight, schedule math drifts.

The timezone is a **setting**, not a constant — resolved per call as: the
user's `users.timezone` override → the platform-level `app_config` timezone (set
at onboarding, editable in Settings) → `Etc/UTC`. Never hardcode a zone string.

Use `app_platform.time`:

| Function | Returns | Use for |
|----------|---------|---------|
| `now(user_id=None)` | aware datetime in the configured zone | "what time is it for the user", day boundaries, display, schedule math |
| `get_timezone(user_id=None)` | the `ZoneInfo` itself | when you need the zone object (e.g. `datetime.now(get_timezone())`, building an aware date) |
| `utcnow()` | aware UTC `now` | **DB storage** (`TIMESTAMPTZ` columns) |
| `to_local(dt, user_id=None)` | the datetime converted to the configured zone | rendering a stored/aware (or naive-as-UTC) timestamp for the user |

**Pattern:**

```python
from app_platform.time import now, utcnow, get_timezone, to_local

created_at = utcnow()                 # store in UTC (TIMESTAMPTZ)
today      = now().date()             # "today" in the user's zone, not the server's
local_hour = now(user_id=user).hour   # per-user local hour for a morning nudge
shown      = to_local(row["created_at"], user_id=user)  # display a stored timestamp
```

**Rules:**

1. **Store UTC, display local.** Persist `utcnow()` into `TIMESTAMPTZ` columns;
   convert with `to_local` only at the edge where you show or compare against
   the user's wall clock.
2. **Derive "today"/day-rollover from `now()`**, never `date.today()`. The day
   boundary must be the user's midnight, not the server's.
3. **Pass `user_id` when the answer is user-specific** (a per-user reminder
   hour, a "good morning" window). Omit it for platform-wide timing and you get
   the platform default zone.
4. **Never hardcode a timezone** (`ZoneInfo("America/New_York")`, `US/Eastern`,
   `pytz`, `tzset()`, `TZ=` env reads). The zone always comes from settings —
   the per-user override first, then the platform default.
5. There's no autofix for the naive-clock trap — reach for `app_platform.time`
   by habit. A stray `datetime.now()` won't raise, it'll just silently do the
   wrong thing in another timezone.

---

## App Lifecycle

### Install (drop folder in `apps/`)
1. Platform discovers new `apps/<id>/manifest.yaml`
2. Runs migrations from `apps/<id>/migrations/`
3. Registers entity types from manifest
4. Imports and registers tools from `tools.py`
5. Mounts API router from `routes.py`
6. Registers job handlers from manifest
7. Wires event subscriptions from `handlers.py`
8. Next frontend build picks up `ui/index.js` → app appears in launcher

### Uninstall (delete folder from `apps/`)
1. Platform notices missing manifest on next restart
2. Tools, routes, job handlers, and event subscriptions are no longer loaded
3. UI components disappear from launcher on next build
4. Database tables and data are **preserved** (safe default)
5. Optional `--purge` drops the app's tables and cleans `app_migrations`

### Disable (without removing)
A `disabled: true` flag in manifest (or a platform-level override) skips all
loading steps. The app's folder stays but it's invisible to the system.

### Update
Modify files in the app folder. On restart:
- New migrations run automatically
- Tool/route/handler changes take effect
- UI changes take effect on next frontend build

---

## Skipper-Built Apps

> *(Aspirational: this section is the design direction, not current platform
> behavior. Today there is **no** `reload_app` hot-load — installing means
> restart; the loader does **not** enforce the cross-app import ban (it's a
> reviewed convention — see The dependency rule); there are **no** resource
> limits; and safety tiers exist only as an `app_registry` column. Don't
> build against these as capabilities, and don't assume the import ban is
> machine-checked — review for it.)*

This architecture directly enables Skipper to create apps autonomously:

1. **Skipper generates app code** — given a description or spec, Skipper
   writes the manifest, data layer, tools, routes, guide, and UI into a new
   `apps/<id>/` folder.

2. **Platform hot-loads the app** — a `reload_app(app_id)` platform service
   runs migrations, registers tools/routes/handlers without a full restart.

3. **Sandboxing** — Skipper-built apps:
   - Get their own DB tables (namespaced under `app_<id>`)
   - Cannot import from other apps (enforced by the loader)
   - Can only use `app_platform.*` services
   - Have resource limits (max tables, max API routes, max tools)
   - Run with a `created_by: "skipper"` flag for auditability

4. **Testing** — Skipper can test its own app by:
   - Calling its tools via MCP
   - Hitting its API endpoints
   - Checking for errors in the app's log namespace

5. **Rollback** — if an app is broken, the platform disables it. Skipper (or
   the user) can fix it or delete it. No impact on other apps.

### Safety Tiers

| Tier | Who builds it | Review required? | Can access |
|------|---------------|------------------|------------|
| **Core** | Human developer | Yes | Everything |
| **Verified** | Skipper, human-reviewed | Yes, once | Platform services + own data |
| **Sandbox** | Skipper, unreviewed | No | Platform services + own data, with resource limits |

---

## Cross-App Data Access

Apps must never import each other's code or query each other's schemas
directly. When an app needs data from another app, it goes through
**platform services**.

### Worked Example: Meal Planner Needs Recipes

The `meal_planner` app wants to show a list of recipes when the user is
building a weekly plan. The Recipes app owns its data in `app_recipes.recipes`.

**Wrong approach** (tight coupling):
```python
# apps/meal_planner/data.py — DON'T DO THIS
from apps.recipes.data import list_recipes  # ← direct import, breaks isolation
```

**Right approach** (platform entity query service):

The platform provides a **generic entity query service** that can read from
any registered app schema, scoped by entity type prefix:

```python
# Platform service
from app_platform.entities import query_entities

# Returns list of dicts — only public/read-safe fields
recipes = query_entities(
    prefix="re",                    # entity type from entity_types table
    filters={"category": "dinner"}, # optional column filters
    fields=["id", "title", "category", "prep_time"],  # explicit field list
    limit=50,
)
```

How this works under the hood:
1. Platform looks up `re` in `entity_types` → gets `table_name=recipes`,
   and resolves the schema from the app registry → `app_recipes`
2. Builds a safe, parameterized `SELECT` against `app_recipes.recipes`
3. Returns only the requested fields (no `SELECT *`, no mutations)

**Write access** is never cross-app. If the meal planner needs to create
something in the recipes domain, it emits an event:

```python
from app_platform.events import emit

emit("meal_planner.recipe_requested", {
    "title": "Quick Chicken Stir Fry",
    "requested_by": "meal_planner",
})
```

The recipes app can subscribe and decide whether to act on it.

### Worked Example: Investment App Links to Documents

The Investment app creates research documents using the platform's document
service (which lives in `public` schema). It doesn't need to query the
Documents app — it calls `app_platform.documents.create_doc(...)` directly.
Documents are a platform service, not an app.

This distinction is key: **platform services** (links, docs, notifications,
images) are available to all apps. **App data** is private and accessed only
through the entity query service or events.

---

## Shared UI Components

Common widgets live in `web/src/components/` (a shared library, not an app) —
e.g. `MarkdownEditor`, `AppPanel`, `ChatPanel`, `FlowchartEditor`. App JSX
imports them by path:

```jsx
import MarkdownEditor from "../../components/MarkdownEditor";
```

Prefer reusing an existing shared component over re-implementing one inside an
app. Widgets that are genuinely cross-app (entity pickers, link displays, tag
editors, confirmation dialogs) belong in `web/src/components/`, not duplicated
per app.

**Visual distinction on the desktop:** Packaged apps appear in the launcher
alongside built-in apps but with a subtly different accent so users can tell at
a glance which apps are platform-native vs. packaged. The discovery loop adds
an `appPackage: true` flag to each app registration it finds in
`apps/*/ui/index.js`; the launcher and tab bar use it to apply the accent
style. You don't set this flag yourself — it's injected automatically.

---

## Styling / Theme — the semantic design system (issue #38)

Skipper has a light and a dark theme. **Never hardcode raw Tailwind color scales**
(`bg-slate-800`, `text-gray-400`, `text-cyan-500`, `text-white`, `#1e293b`, …) in app
UI — they don't adapt correctly across both themes and they make the same element look
different in every app. Instead use the **semantic classes**, defined once for both
themes in `web/src/index.css`. A `check-no-raw-tokens` lint enforces this (it fails the
build on any raw color class), and the scaffolder emits semantic-only UI.

**The rule:** use a semantic class; if you need something the set lacks (e.g. a new
status color), **add a semantic token** to `index.css` for BOTH themes — never reach for
a raw scale.

### Class list (when to use each)

| Role | Class | Use for |
|---|---|---|
| Surfaces | `surface-page` | the outermost app background |
| | `surface-panel` | a panel/column within the app |
| | `surface-card` | a card / list container |
| | `surface-raised` | an elevated element (gets a shadow) |
| | `surface-overlay` | modal/dropdown scrim |
| Text | `text-default` | primary body text |
| | `text-muted` | secondary text |
| | `text-faint` | tertiary / hints |
| | `text-on-accent` | text on a colored/accent background |
| | `link` | hyperlinks (hover underline) |
| Borders | `border-subtle` / `border-strong` | dividers / emphasis borders |
| Buttons | `btn-primary` / `btn-success` / `btn-danger` / `btn-ghost` | actions — **foreground is baked in** (text never renders black-on-accent), incl. `:disabled` |
| Forms | `input` | text inputs / textareas / selects (bg+border+placeholder) |
| | `field-label` | a field's label |
| | `focus-ring` | add to any focusable element for an AA-visible keyboard focus ring |
| Badges | `pill` + `pill-success` / `pill-warn` / `pill-danger` / `pill-neutral` (or `status-pill`) | status chips |
| **Composites** (one definition so the SAME element looks identical in every app) | `tab` / `tab-active` | a tab / segmented-control row |
| | `list-row` | a row in a list (hover state included) |
| | `section-header` | an uppercase section label |
| | `empty-state` | the "nothing here yet" placeholder |

**Map each element to its CORRECT role** — a tab is `tab` (not just "some semantic
class"), a card is `surface-card`, a list row is `list-row`. The lint proves you used no
raw color; using the *right* semantic role is reviewed.

### Before → after

```jsx
// BEFORE — raw scales, breaks in light mode, inconsistent across apps
<div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
  <h3 className="text-xs uppercase text-slate-500">Tasks</h3>
  <button className="bg-indigo-600 text-white px-3 py-1.5 rounded">Add</button>
</div>

// AFTER — semantic, correct in both themes, consistent everywhere
<div className="surface-card border border-subtle rounded-xl p-4">
  <h3 className="section-header">Tasks</h3>
  <button className="btn-primary px-3 py-1.5 rounded focus-ring">Add</button>
</div>
```

### Adding a token you don't have (escape hatch)

If the set genuinely lacks a role you need (say a distinct "category" color), add it to
**both** theme blocks in `web/src/index.css` and expose a class — don't reach for a raw
scale:

```css
:root            { --ds-category-bg:#3b0764; --ds-category-fg:#e9d5ff; }
html[data-theme="light"] { --ds-category-bg:#f3e8ff; --ds-category-fg:#7e22ce; }
@layer components { .pill-category { background-color:var(--ds-category-bg); color:var(--ds-category-fg); } }
```

Dark is the default and must stay byte-stable; choose light values that are AA-legible
(≥4.5:1 body, ≥3:1 large/UI) and consistent with the warm/clean reference look.

---

## Event Bus: delivery model (synchronous best-effort today)

> **Reality:** the bus is **synchronous best-effort**, NOT at-least-once. The
> durable tables and retry function below are **scaffolding for a future**
> at-least-once mode that is **not wired up**, and **no app subscribes yet** (only
> `bounties`/`chores` emit) — so the delivery/retry path is dormant. See
> `specs/EVENTS.md` for the full state and the gaps to close.

Events are persisted to `public.app_events` before dispatch, giving a durable
audit trail (and a basis for a future retry).

```
app_events
  id            TEXT PRIMARY KEY
  event_type    TEXT NOT NULL         -- e.g. 'recipe.created'
  payload       JSONB NOT NULL
  emitted_by    TEXT NOT NULL         -- app_id of emitter
  emitted_at    TIMESTAMPTZ NOT NULL
  status        TEXT DEFAULT 'pending' -- 'pending', 'dispatched', 'completed'
```

```
app_event_deliveries
  event_id      TEXT REFERENCES app_events(id)
  subscriber    TEXT NOT NULL          -- app_id of subscriber
  status        TEXT DEFAULT 'pending' -- 'pending', 'delivered', 'failed'
  attempts      INT DEFAULT 0
  last_attempt  TIMESTAMPTZ
  error         TEXT
  PRIMARY KEY (event_id, subscriber)
```

**Delivery flow (as implemented):**
1. App calls `emit("recipe.created", {...})` → a row is inserted into
   `app_events` (status `dispatched`), plus one `app_event_deliveries` row per
   subscriber (status `pending`), in one transaction.
2. **Dispatch is synchronous, in the same call** — there is no async dispatcher
   loop. Each subscriber's handler runs inline; success → `delivered`, an
   exception is caught/logged/isolated → `failed` (`attempts += 1`). The emitter
   never sees the failure.
3. If every delivery succeeded (or there were no subscribers) → event
   `completed`.

**Not yet wired:** `retry_failed_deliveries()` (re-runs `failed` deliveries under
`MAX_ATTEMPTS = 3`) exists but has **no scheduled caller**, and it does not cover
deliveries left `pending` by a restart. So a failed/orphaned delivery is **not**
automatically retried — there is no "events are never dropped" guarantee. This is
fine today because nothing subscribes; revisit when a subscriber needs durability
(see `specs/EVENTS.md`).

---

## Migration Strategy: Forward-Only per Schema

App migrations are **forward-only**. No down migrations.

- **App schema** (`app_<id>`): the app's migrations create and alter tables
  within its own schema. Upgrading the app runs new migration files.
  Uninstalling with `--purge` does `DROP SCHEMA app_<id> RESTRICT` — Postgres
  will **refuse** the drop if anything outside the schema depends on it
  (e.g., a foreign key from a platform table). This is the safe default.

- **No cross-schema foreign keys.** Apps must **never** create foreign keys
  referencing tables outside their own schema (and the platform must never
  create FKs pointing into an app schema). Cross-entity references use the
  `links` table (soft references by entity ID), not hard FKs. The app loader
  should validate this constraint at install time by inspecting
  `information_schema.referential_constraints` and rejecting any FK that
  crosses schema boundaries.

  If an app needs referential integrity with a platform entity (e.g.,
  "this row belongs to user X"), it stores the entity ID as a plain `TEXT`
  column and relies on application-level validation, not a database
  constraint. This keeps schemas fully independent and makes
  `DROP SCHEMA ... RESTRICT` safe to run without risk of cascading into
  platform tables.

- **Platform schema** (`public`): if an app requires a platform extension
  (e.g., a new column on `entity_types`, a new platform service table), that
  goes into the main `migrations/` directory as a platform migration. Removing
  the app does **not** remove platform migrations — the platform only moves
  forward. This is intentional: platform extensions are additive and may serve
  other apps.

- **Failed migrations**: if an app migration fails mid-run, the transaction
  rolls back (each migration file runs in a transaction). The app is marked
  `status: 'error'` in `app_registry` and its tools/routes are not loaded.
  The user (or Skipper) can fix the migration and retry.

---

## App Store Model

Apps are designed for an **app store** model from day one:

- **`manifest.yaml`** already contains all metadata needed for a store listing:
  `id`, `name`, `version`, `description`, entity types, dependencies.
- **App registry table** (`public.app_registry`) tracks installed apps:

  ```
  app_registry
    app_id        TEXT PRIMARY KEY
    version       TEXT
    installed_at  TIMESTAMPTZ
    installed_by  TEXT           -- 'human', 'skipper', etc.
    status        TEXT           -- 'active', 'disabled', 'error'
    safety_tier   TEXT           -- 'core', 'verified', 'sandbox'
    manifest      JSONB          -- cached manifest content
  ```

- **Install/uninstall commands** *(partially aspirational: today only
  `app_platform.loader.uninstall_app(app_id, purge=False)` exists, as a Python
  function — none of these are exposed as chat tools yet; "install" = drop the
  folder in and restart)*:
  - `install_app(app_id)` — creates schema, runs migrations, registers
  - `uninstall_app(app_id, purge=False)` — deregisters; `purge=True` drops
    schema
  - `disable_app(app_id)` / `enable_app(app_id)` — toggle without removing
  - `list_apps()` — show installed apps with status

- **Skipper as store curator** — Skipper can browse a catalog of app templates,
  generate new apps, install them, test them, and publish them to the family.

---

## The dependency rule

Apps may depend on the platform; **the platform may not depend on any app.**
Concretely:

- App code imports from `app_platform.*` and its own package — never from
  `apps.<other>.*`.
- Cross-app reads go through `app_platform.entities.query_entities`; cross-app
  writes go through events. There is no third option.
- Platform/core code must never `import apps.<id>`. A few apps are flagged as
  **core** (the platform refuses to start without them — e.g. Goals); the
  platform reaches them only through their `app_platform.*` facade, not by
  importing the app package directly.

This keeps every app independently installable, removable, and safe to fail.
