# Memory System Deep Explanation

This document explains what has been implemented in the memory system, why it was done, and how the pieces connect technically.

## Big Picture

You now have two separate databases with different responsibilities:

1. `instance/ui_jobs.db`
- Stores live UI/job workflow state (status, email actions, previews, etc.).

2. `instance/memory.db`
- Stores long-term memory for agent behavior and learning (episodes + profile).

This separation is intentional:
- UI operations stay stable and focused.
- Memory can evolve independently for recommendations/training.

## What Was Added

### 1) New memory DB and schema

File: `src/research_crew/memory/db.py`

It defines:

1. `episodes` table
- `episode_id`
- `job_id`
- `started_at`, `ended_at`, `duration_sec`
- `status`, `error`
- `selected_topics_json`
- `search_queries_json`
- `output_path`
- `run_config_json`
- `metrics_json`

2. `user_profile` table
- `user_id`, `name`, `email_group`
- `preferred_topics_json`, `tone`, `exclusions_json`, `cadence`
- `timezone`, `content_length`, `section_order_json`
- `source_prefs_json`, `risk_sensitivity`, `region_focus`
- `allow_duplicate`, `subject_template`, `exclude_keywords_json`

Why:
- `episodes` = timeline of what happened each run.
- `user_profile` = persistent personal preferences.

---

### 2) Episode lifecycle functions

File: `src/research_crew/memory/episodes.py`

Core functions:

1. `create_episode(...)`
- Called when a run starts.
- Inserts a new row with `status = "running"` and `started_at`.

2. `finish_episode(...)`
- Called when run finishes (success/failure/aborted).
- Sets `ended_at`, calculates `duration_sec`, writes status/error/output/metrics.

Why:
- Gives complete run history from start to finish, not just snapshots.

---

### 3) Reconciliation + retention (memory hygiene)

File: `src/research_crew/memory/episodes.py`

Added:

1. `reconcile_running_episodes(load_job_fn)`
- Finds episodes still marked `running` on startup.
- Cross-checks linked job in `ui_jobs.db`.
- Closes stale episodes as:
  - `success` if job actually completed,
  - `failed` if job has error,
  - `aborted` if unclear/incomplete.

2. `cleanup_old_episodes(retention_days=180)`
- Deletes old finished episodes after retention window.

Why:
- Prevents zombie `running` episodes.
- Keeps memory DB manageable over time.

---

### 4) Metrics and run context capture

Files:
- `src/research_crew/memory/episodes.py`
- `src/research_crew/web/routes.py`

Added:

1. `build_job_metrics(job, reconciled=False)` in `episodes.py`
- Builds summarized metrics:
  - topic count
  - state counts (`done/failed/running/queued`)
  - per-topic durations where available

2. `build_run_config(selected_slugs, search_queries)` in `routes.py`
- Captures run setup:
  - entrypoint
  - selected topics
  - topic count
  - search query count

3. Episode writes now include:
- `run_config_json` on `create_episode(...)`
- `metrics_json` on `finish_episode(...)`

Why:
- Makes memory training-ready and analyzable later.

---

### 5) Search query memory capture

Files:
- `src/research_crew/web/templates/fragments/topics.html`
- `src/research_crew/web/routes.py`

Added:

1. Optional UI input:
- `Search context (optional)` in topic form.

2. `parse_search_queries(...)` in routes:
- Parses comma/newline-separated text.
- Stores verbatim query strings in `search_queries_json`.

Why:
- Starts collecting real user intent now.
- This data can power recommendation logic later.

Important:
- Queries are currently stored only.
- They do not yet change generation behavior.

---

### 6) Long-term profile memory service (CRUD layer)

File: `src/research_crew/memory/profile.py`

Added:

1. `get_profile(user_id)`
2. `save_profile(profile, user_id)`
3. `ensure_profile(user_id)`

Behavior:
- Uses a default single-user profile (`single_user`).
- `ensure_profile()` guarantees a row exists.
- JSON list fields are converted safely to/from Python.

Why:
- Table existed conceptually; now it is actually usable in code.

---

### 7) Startup wiring for memory maintenance

File: `src/research_crew/web/__init__.py`

App startup now does:

1. Init UI DB
2. Init memory DB
3. Reconcile stale running episodes
4. Cleanup old episodes (180 days)
5. Ensure default profile exists

Why:
- Startup is the safest place for one-time DB bootstrap and cleanup tasks.

---

### 8) Run flow wiring

File: `src/research_crew/web/routes.py`

On `/run`:
- Parse selected topics
- Parse search queries
- Build run config
- Create episode
- Store `episode_id` in current job

In `run_job(...)`:
- On success: close episode with status `success`, output path, metrics
- On exception: close episode with status `failed`, error, metrics

Why:
- Memory fields stay empty until explicitly written.
- This wiring ensures each run writes meaningful memory data.

---

### 9) Stuck-run timeout protection

File: `src/research_crew/web/routes.py`

Added:
- `MAX_JOB_RUNTIME_SECONDS = 45 * 60`
- `maybe_abort_stalled_job(job)` called from status polling route

Behavior:
- If job remains in active states too long, it is marked aborted/failed.
- Corresponding running episode is also closed.

Why:
- Prevents forever-running jobs and episodes when runtime hangs.

---

### 10) Related non-memory fix

File: `src/research_crew/web/routes.py`

Added:
- `normalize_subject(value)`

Behavior:
- If subject is empty/`None`/`null`-like text, fallback to default subject.

Why:
- Prevents `"None"` from appearing in email UI/history.

## What This Gives You Right Now

You now have:

1. Persistent episodic memory across restarts
2. Profile memory with usable service functions
3. Automatic stale-run reconciliation
4. Retention policy for old memory
5. Stored run context (`run_config`) and run outcomes (`metrics`)
6. Captured verbatim search context per run
7. Runtime stall protection to reduce orphaned states

## What Is Not Yet Done (Next Phase)

Foundation is complete, but behavior usage is next:

1. Use profile/episodes/search memory to influence topic recommendations
2. Use memory to influence generation prompts/output
3. Build profile edit UI
4. Add formal automated tests around memory flows

## Practical Mental Model

Use this to remember the system:

1. User starts run -> open episode
2. Run executes -> job state updates
3. Run ends -> close episode with results and metrics
4. App restarts -> reconcile stale episodes
5. Periodically -> clean old episodes
6. Always -> keep profile memory ready

