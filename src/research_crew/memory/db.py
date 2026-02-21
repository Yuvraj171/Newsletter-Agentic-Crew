import sqlite3
import json
from pathlib import Path

DB_PATH = Path("instance") / "memory.db"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                job_id TEXT,
                started_at REAL,
                ended_at REAL,
                duration_sec REAL,
                status TEXT,
                error TEXT,
                selected_topics_json TEXT,
                search_queries_json TEXT,
                output_path TEXT,
                run_config_json TEXT,
                metrics_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                email_group TEXT,
                preferred_topics_json TEXT,
                tone TEXT,
                exclusions_json TEXT,
                cadence TEXT,
                timezone TEXT,
                content_length TEXT,
                section_order_json TEXT,
                source_prefs_json TEXT,
                risk_sensitivity TEXT,
                region_focus TEXT,
                allow_duplicate INTEGER,
                subject_template TEXT,
                exclude_keywords_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_events (
                event_id TEXT PRIMARY KEY,
                episode_id TEXT,
                job_id TEXT,
                created_at REAL,
                topic_slug TEXT,
                base_slug TEXT,
                topic_label TEXT,
                event_type TEXT,
                outcome TEXT,
                score REAL,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_topic_events_created ON topic_events(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_topic_events_topic ON topic_events(topic_slug, base_slug)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_topic_events_job ON topic_events(job_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_events (
                event_id TEXT PRIMARY KEY,
                episode_id TEXT,
                job_id TEXT,
                created_at REAL,
                query_text TEXT,
                normalized_query TEXT,
                event_type TEXT,
                outcome TEXT,
                result_count INTEGER,
                results_json TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_events_created ON search_events(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_events_query ON search_events(normalized_query)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_events_job ON search_events(job_id)"
        )


def _dumps(value):
    return json.dumps(value) if value is not None else None


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
