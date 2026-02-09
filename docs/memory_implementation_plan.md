# Memory Implementation Plan

This plan expands the system with three memory types (working, episodic, long-term user/profile) using a new SQLite DB. It also lists file impacts, new files/folders, and cleanup candidates.

## Scope Summary

- Add persistent memory storage in `instance/memory.db`.
- Capture end-to-end runs as episodic memory (one episode per full run).
- Store single-user profile preferences.
- Keep working memory in RAM for live session state.
- Record search queries verbatim (empty for now until search UI is added).
- Prepare schema to support future continuous training.

## Memory Architecture

### A) Working memory (short-term)

- Keep current in-memory structures (e.g., `JOBS` in Flask layer).
- No persistence in v1.
- Optional later: add `session_state` table to persist UI state across restarts.

### B) Episodic memory (end-to-end run)

- One episode = full run from start to completion.
- Store inputs, outcomes, timing, and artifact pointers.
- Store HTML path only (avoid DB bloat).

### C) Long-term user/profile memory (single user)

- One row profile with required + recommended fields.
- Editable later by UI or admin API.

## DB Schema (memory.db)

### `episodes`

- `episode_id` (TEXT, primary key)
- `job_id` (TEXT, nullable, link to UI job)
- `started_at` (REAL)
- `ended_at` (REAL)
- `duration_sec` (REAL)
- `status` (TEXT: success/failed)
- `error` (TEXT)
- `selected_topics_json` (TEXT)
- `search_queries_json` (TEXT)
- `output_path` (TEXT)
- `run_config_json` (TEXT, optional for training readiness)
- `metrics_json` (TEXT, optional: latency/tokens/cost)

### `user_profile`

- `user_id` (TEXT, primary key; default `single_user`)
- `name` (TEXT)
- `email_group` (TEXT)
- `preferred_topics_json` (TEXT)
- `tone` (TEXT)
- `exclusions_json` (TEXT)
- `cadence` (TEXT)
- Recommended additions:
  - `timezone`
  - `content_length`
  - `section_order_json`
  - `source_prefs_json`
  - `risk_sensitivity`
  - `region_focus`
  - `allow_duplicate`
  - `subject_template`
  - `exclude_keywords_json`

### Optional (later)

- `session_state` (if working memory persistence is needed)
- `feedback` table for ratings and manual review

## Data Flow (Episodic Memory)

1) Run starts:
   - Create episode row with `started_at` and `selected_topics`.
   - Store `search_queries` if any (empty list initially).
2) Run completes:
   - Update `ended_at`, `duration_sec`, `status`, `error`.
   - Store `output_path`.

## Implementation Steps (Detailed)

### Step 1: Create memory module

- Add new package `src/research_crew/memory/`.
- Files:
  - `db.py`: sqlite connect/init + migrations
  - `episodes.py`: create/update/load episodes
  - `profile.py`: load/save profile
  - `__init__.py`: exports

### Step 2: Initialize `memory.db`

- On app startup (Flask `create_app`), ensure memory DB tables exist.
- Create tables only if missing.

### Step 3: Wire episodic tracking into run flow

- When `/run` starts, create an episode with `selected_topics`.
- When job completes (success/failure), update the episode with end time, status, output path, error.
- Store `job_id` for cross-referencing.

### Step 4: Add profile read/write utilities

- Implement `get_profile()` and `save_profile()` in memory module.
- For now, profile can be created with defaults; UI integration can be added later.

### Step 5: Search query logging (no UI yet)

- Add support to accept a `search_queries` list from UI later.
- For now: record empty list in episodes.

### Step 6: Training readiness fields (optional)

- Add JSON columns (`run_config_json`, `metrics_json`) to enable future training.
- Keep empty until used.

## Files Affected (Existing)

- `src/research_crew/web/__init__.py`
  - Initialize memory DB on app creation.
- `src/research_crew/web/routes.py`
  - Create episode at run start, update at completion.
  - Pass job_id to memory.
- `src/research_crew/app.py`
  - Add hooks to mark episode completion for non-UI runs (if used).
- `run_flask.py`
  - No change expected, unless memory init is moved here.

## New Files (Planned)

- `src/research_crew/memory/db.py`
- `src/research_crew/memory/episodes.py`
- `src/research_crew/memory/profile.py`
- `src/research_crew/memory/__init__.py`

## New Folders (Proposed)

- `src/research_crew/memory/` (new)
- Optional later:
  - `src/research_crew/services/` for orchestration logic
  - `src/research_crew/models/` for dataclasses/DTOs

## Cleanup & Restructure Candidates (Review Before Removal)

### Likely removable / archive

- `src/research_crew/main_og.py` (legacy Streamlit UI, not referenced by Flask)
- `list_gemini_models.py` (not referenced)
- `knowledge/user_preference.txt` (not referenced)
- `output/` folder (older artifacts; code uses `outputs/`)
- `outputs/*.html`, `outputs/*.md` (generated artifacts; consider moving to `artifacts/` or keep in `outputs/` but add .gitignore)
- `__pycache__/` files under `src/` (should be excluded by .gitignore)

### Folder structure improvements

- Keep `outputs/` as the sole artifact directory (remove `output/`).
- Add `instance/.gitignore` to avoid committing DB and runtime files.
- Consider moving one-off scripts into `scripts/` (e.g., `list_gemini_models.py`) if kept.

## Removal Plan (Safe Order)

1) App entrypoint confirmed Flask only (`run_flask.py`).
2) Streamlit is not used; remove or archive `src/research_crew/main_og.py`.
3) Remove `output/` directory after verifying no references.
4) Add `.gitignore` rules for `outputs/` and `instance/*.db`.

## Risks & Mitigations

- Risk: Duplicate DB logic across `ui_jobs.db` and `memory.db`.
  - Mitigation: keep responsibilities separate (UI state vs memory).
- Risk: Episode not closed on failure.
  - Mitigation: ensure exception paths update `status` and `error`.

## Testing / Verification

- Start a run, confirm episode row created in `memory.db`.
- Complete run, verify `ended_at`, `status`, `output_path` updated.
- Simulate failure, verify `error` captured.
- Restart app, verify memory persists.

## Out of Scope (Later)

- Knowledge graph recommendation system.
- Search UI and query translation logic.
- Multi-user profile support.
- Episodic summaries and feedback capture.
