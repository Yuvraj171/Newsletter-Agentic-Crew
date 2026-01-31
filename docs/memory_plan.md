# Memory Addition Plan

This document describes the planned memory system for the newsletter agent. It focuses on three memory types: short-term/working, episodic, and long-term user/profile. The initial implementation is persistence-first and training-ready, with clear paths for later extensions (recommendations, knowledge graph, and continuous optimization).

## Goals

- Provide persistent memory across restarts and sessions.
- Make the agent context-aware using prior runs and user preferences.
- Capture run outcomes (success/failure) so future behavior can improve.
- Keep learning gradual and additive by accumulating episodes over time.
- Keep storage simple and low-cost while enabling future training.

## Memory Types

### 1) Short-term / working memory

**Purpose**
- Holds live run state during a session (current job status, progress, ETA, approvals, etc.).

**Current plan**
- Keep the in-memory `JOBS` structure as the working memory for the active session.
- No persistence for working memory in v1. (Optional later: session_state table.)

### 2) Episodic memory (full run)

**Purpose**
- Store end-to-end episodes of each run (start -> completion).
- Support auditability, analytics, and learning from outcomes.

**Episode definition**
- A single complete run is one episode (end-to-end).

**Core fields**
- `episode_id` (uuid)
- `job_id` (links to UI job)
- `started_at`, `ended_at`, `duration_sec`
- `status` (success/failed)
- `error` (if any)
- `selected_topics` (JSON array)
- `search_queries` (JSON array, verbatim)
- `output_path` (path to generated HTML file)

**Storage choice**
- Store **paths** to HTML outputs, not full content, for efficiency.

**Retention**
- Recommended: keep full episodes for 180 days.
- Defer summary/archival for now; add later if needed.

### 3) Long-term user/profile memory

**Purpose**
- Persist user preferences so the system stays consistent across sessions.

**Single-user scope (current)**
- Single row user profile, since only one user uses the system.

**Required fields**
- `name`
- `email_group`
- `preferred_topics` (JSON array)
- `tone`
- `exclusions` (JSON array)
- `cadence`

**Recommended additions**
- `timezone`
- `content_length`
- `section_order` (JSON array)
- `source_prefs` (JSON array)
- `risk_sensitivity`
- `region_focus`
- `allow_duplicate` (default)
- `subject_template`
- `exclude_keywords` (JSON array)

## Storage Strategy

**New DB**
- Create a new SQLite database at `instance/memory.db`.
- Keep memory data separate from the UI/job database for clarity and future scaling.

**Why SQLite**
- Low operational overhead, easy backup, and fast local queries.

## Training Readiness (Continuous Improvement)

The episodic memory schema supports future continuous training by capturing:
- Inputs: `selected_topics`, `search_queries`
- Outcomes: `status`, `error`
- Artifacts: `output_path`
- Timing: `duration_sec`

Optional additions later (not in v1 unless requested):
- `run_config` (model, prompts, parameters)
- `tool_trace` (per-topic timings, failures)
- `feedback` / `rating` / `regret`
- `cost` / `tokens` / `latency`

## Checklist Mapping

- Memory persists across restarts and sessions: yes (`instance/memory.db`).
- Agents are aware of previously sent topics/themes: episodic `selected_topics`.
- Past success or failure influences future outputs: episodic `status`/`error`.
- Agents feel context-aware, not stateless: profile + episode history.
- Learning is gradual and additive: episodes accumulate over time.

## Implementation Plan (High Level)

1) Create `instance/memory.db` and tables:
   - `episodes`
   - `user_profile`
   - `search_history` (optional; can be embedded in episodes initially)

2) Write episodic records:
   - On run start: create episode with `started_at`.
   - On run completion: update `ended_at`, `status`, `duration_sec`, `error`, `output_path`.

3) Add profile load/save:
   - Single user profile row.

4) Record search queries verbatim:
   - Store in episodes for now; separate history table if needed later.

## Scope for Later Phases (Not in v1)

- Dynamic topic recommendations (knowledge graph).
- Search bar for arbitrary topics, mapped to a newsletter section.
- Episodic summaries for long-term analytics.
- Multi-user profile support.
