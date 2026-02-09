# Memory Implementation Learning Notes

This file captures teaching notes from the memory implementation so you can review later.

## 2026-01-31

### Change: add `src/research_crew/memory/__init__.py`

**Why this change**
- In Python, a folder becomes an importable package when it has `__init__.py`.
- This lets us later do `from research_crew import memory` or `from research_crew.memory import db`.

**If you want to remember**
- "Create a package so the memory code can be organized under one namespace."

### Change: add `src/research_crew/memory/db.py`

**Why this change (plain English)**
- This file is the door to `memory.db`. It knows where the DB lives, how to connect, and how to create tables.
- It defines two tables:
  - `episodes` for full run history
  - `user_profile` for long-term preferences
- `_dumps` and `_loads` make JSON storage in SQLite easy.

**Key functions**
1. `get_conn()` opens SQLite with row access by column name.
2. `init_db()` creates tables if missing.
3. `_dumps(value)` converts Python list/dict to JSON text.
4. `_loads(value, default)` converts JSON text back, safely.

**If you want to remember**
- "db.py is the one place for DB path, connection, and table creation."

### Change: add `src/research_crew/memory/episodes.py`

**Why this change (plain English)**
- This file stores full-run episodes in memory DB.

**Key functions**
1. `create_episode(...)` opens an episode at run start.
2. `finish_episode(...)` closes it on success/failure with timestamps.

**If you want to remember**
- "create_episode opens the run; finish_episode closes it with results."

### Change: wire episodes into Flask run flow (`src/research_crew/web/routes.py`)

**Why this change (plain English)**
- Without this wiring, episodes never get created or closed.

**What changed**
- Start run -> `create_episode(...)`
- End run -> `finish_episode(...)`

**Simple mental model**
- Start run -> open episode
- End run -> close episode

### Change: initialize memory DB on app startup (`src/research_crew/web/__init__.py`)

**Why this change (plain English)**
- If tables do not exist, writes fail.
- Startup init guarantees DB is ready.

**What changed**
- `create_app()` now calls both UI DB init and memory DB init.

**If you want to remember**
- "Always initialize new databases at app startup."

### Change: expand `episodes.py` with retention, reconciliation, and metrics helpers

**Why this change (plain English)**
- Episodes can remain `running` if the app is interrupted.
- Old data needs cleanup.
- Metrics are needed for learning/training readiness.

**What changed**
- `cleanup_old_episodes(retention_days=180)` removes old finished episodes.
- `reconcile_running_episodes(load_job_fn)` closes orphaned `running` episodes on startup.
- `build_job_metrics(job, reconciled=False)` creates compact metrics for each run.

**If you want to remember**
- "Episodes need lifecycle maintenance, not just writes."

### Change: add `src/research_crew/memory/profile.py` (profile service layer)

**Why this change (plain English)**
- The table existed, but there was no usable code around it.

**Key functions**
- `get_profile(user_id)` reads profile data.
- `save_profile(profile, user_id)` upserts profile data.
- `ensure_profile(user_id)` creates default profile if missing.

**If you want to remember**
- "Tables are not features; service functions make them usable."

### Change: add startup maintenance wiring (`src/research_crew/web/__init__.py`)

**Why this change (plain English)**
- Startup should run one-time memory hygiene.

**What changed**
- On app startup now:
  - reconcile orphaned running episodes,
  - cleanup old episodes,
  - ensure default profile exists.

**If you want to remember**
- "Startup is where one-time reconciliation and cleanup belong."

### Change: capture `search_queries`, `run_config`, and metrics in run flow (`routes.py`)

**Why this change (plain English)**
- These memory columns stayed empty because nothing populated them.

**What changed**
- Added `parse_search_queries(...)`.
- Added `build_run_config(...)`.
- `/run` now passes `search_queries` and `run_config` into `create_episode(...)`.
- `run_job` now passes `metrics=build_job_metrics(job)` into `finish_episode(...)`.

**If you want to remember**
- "A DB field stays empty until you explicitly pass values at write time."

### Change: add optional search context input in `topics.html`

**Why this change (plain English)**
- Without UI input, real runs cannot produce search query memory.

**What changed**
- Added optional input `name="search_queries"` in the run form.
- Values now flow into episode memory as verbatim queries.

**If you want to remember**
- "Useful memory needs a complete path: UI input -> parser -> DB write."
