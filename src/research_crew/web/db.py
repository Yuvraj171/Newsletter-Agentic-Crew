import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path("instance") / "ui_jobs.db"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                created_at REAL,
                updated_at REAL,
                status_json TEXT,
                selected_json TEXT,
                html_ready INTEGER,
                review_confirmed INTEGER,
                approved INTEGER,
                error TEXT,
                output_path TEXT,
                email_group TEXT,
                email_extra TEXT,
                email_subject TEXT,
                email_sent INTEGER,
                email_error TEXT,
                email_sending INTEGER,
                email_sent_to_json TEXT,
                email_preview_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_history (
                job_id TEXT,
                attempt INTEGER,
                label TEXT,
                status TEXT,
                timestamp TEXT,
                recipient_count INTEGER,
                recipients_json TEXT,
                subject TEXT,
                error TEXT,
                content_hash TEXT,
                test_mode INTEGER,
                PRIMARY KEY (job_id, attempt)
            )
            """
        )
        ensure_columns(
            conn,
            "jobs",
            {"email_preview_json": "TEXT"},
        )
        ensure_columns(
            conn,
            "email_history",
            {"content_hash": "TEXT", "test_mode": "INTEGER"},
        )


def ensure_columns(conn, table, columns):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, spec in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")


def _as_bool(value):
    return bool(int(value)) if value is not None else False


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def save_job(job):
    now = time.time()
    job.setdefault("created_at", now)
    job["updated_at"] = now
    payload = (
        job["job_id"],
        job["created_at"],
        job["updated_at"],
        json.dumps(job.get("status", {})),
        json.dumps(job.get("selected", [])),
        1 if job.get("html_ready") else 0,
        1 if job.get("review_confirmed") else 0,
        1 if job.get("approved") else 0,
        job.get("error"),
        str(job.get("output_path")) if job.get("output_path") else None,
        job.get("email_group"),
        job.get("email_extra"),
        job.get("email_subject"),
        1 if job.get("email_sent") else 0,
        job.get("email_error"),
        1 if job.get("email_sending") else 0,
        json.dumps(job.get("email_sent_to", [])),
        json.dumps(job.get("email_preview")),
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, created_at, updated_at, status_json, selected_json,
                html_ready, review_confirmed, approved, error, output_path,
                email_group, email_extra, email_subject, email_sent, email_error,
                email_sending, email_sent_to_json, email_preview_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                status_json=excluded.status_json,
                selected_json=excluded.selected_json,
                html_ready=excluded.html_ready,
                review_confirmed=excluded.review_confirmed,
                approved=excluded.approved,
                error=excluded.error,
                output_path=excluded.output_path,
                email_group=excluded.email_group,
                email_extra=excluded.email_extra,
                email_subject=excluded.email_subject,
                email_sent=excluded.email_sent,
                email_error=excluded.email_error,
                email_sending=excluded.email_sending,
                email_sent_to_json=excluded.email_sent_to_json,
                email_preview_json=excluded.email_preview_json
            """,
            payload,
        )


def save_email_history_entry(job_id, entry):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO email_history (
                job_id, attempt, label, status, timestamp,
                recipient_count, recipients_json, subject, error, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                entry.get("attempt"),
                entry.get("label"),
                entry.get("status"),
                entry.get("timestamp"),
                entry.get("recipient_count"),
                json.dumps(entry.get("recipients", [])),
                entry.get("subject"),
                entry.get("error"),
                entry.get("content_hash"),
            ),
        )


def load_email_history(job_id):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM email_history
            WHERE job_id = ?
              AND (test_mode IS NULL OR test_mode = 0)
            ORDER BY attempt ASC
            """,
            (job_id,),
        ).fetchall()
    history = []
    for row in rows:
        history.append(
            {
                "attempt": row["attempt"],
                "label": row["label"],
                "status": row["status"],
                "timestamp": row["timestamp"],
                "recipient_count": row["recipient_count"],
                "recipients": _loads(row["recipients_json"], []),
                "subject": row["subject"],
                "error": row["error"],
                "content_hash": row["content_hash"],
            }
        )
    return history


def load_job(job_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = {
        "job_id": row["job_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": _loads(row["status_json"], {}),
        "selected": _loads(row["selected_json"], []),
        "html_ready": _as_bool(row["html_ready"]),
        "review_confirmed": _as_bool(row["review_confirmed"]),
        "approved": _as_bool(row["approved"]),
        "error": row["error"],
        "output_path": Path(row["output_path"]) if row["output_path"] else None,
        "email_group": row["email_group"],
        "email_extra": row["email_extra"],
        "email_subject": row["email_subject"],
        "email_sent": _as_bool(row["email_sent"]),
        "email_error": row["email_error"],
        "email_sending": _as_bool(row["email_sending"]),
        "email_sent_to": _loads(row["email_sent_to_json"], []),
        "email_preview": _loads(row["email_preview_json"], None),
    }
    job["email_history"] = load_email_history(job_id)
    return job


def find_duplicate_send(content_hash, recipients=None):
    if not content_hash or not recipients:
        return None
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM email_history
            WHERE content_hash = ?
              AND status = 'success'
              AND (test_mode IS NULL OR test_mode = 0)
            ORDER BY rowid DESC
            """,
            (content_hash,),
        ).fetchall()
    if not rows:
        return None
    for row in rows:
        prior_recipients = _loads(row["recipients_json"], [])
        overlap = [r for r in recipients if r in prior_recipients]
        if overlap:
            return {
                "job_id": row["job_id"],
                "attempt": row["attempt"],
                "timestamp": row["timestamp"],
                "subject": row["subject"],
                "recipient_count": row["recipient_count"],
                "overlap": overlap,
            }
    return None


def load_latest_job(since: float | None = None):
    with get_conn() as conn:
        if since is None:
            row = conn.execute(
                "SELECT job_id FROM jobs ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT job_id FROM jobs WHERE created_at >= ? ORDER BY updated_at DESC LIMIT 1",
                (since,),
            ).fetchone()
    if not row:
        return None
    return load_job(row["job_id"])

