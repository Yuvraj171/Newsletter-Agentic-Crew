import time
import uuid
from pathlib import Path

from .db import get_conn, _dumps

RUNNING_STATES = {"queued", "active", "running", "waiting"}


def create_episode(selected_topics, search_queries=None, job_id=None, run_config=None, metrics=None):
    episode_id = uuid.uuid4().hex
    started_at = time.time()
    payload = (
        episode_id,
        job_id,
        started_at,
        None,
        None,
        "running",
        None,
        _dumps(selected_topics or []),
        _dumps(search_queries or []),
        None,
        _dumps(run_config),
        _dumps(metrics),
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO episodes (
                episode_id, job_id, started_at, ended_at, duration_sec,
                status, error, selected_topics_json, search_queries_json,
                output_path, run_config_json, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
    return episode_id


def finish_episode(episode_id, status, output_path=None, error=None, metrics=None):
    ended_at = time.time()
    duration = None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT started_at FROM episodes WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
        if row:
            duration = ended_at - (row["started_at"] or ended_at)
        conn.execute(
            """
            UPDATE episodes
            SET ended_at = ?, duration_sec = ?, status = ?, error = ?,
                output_path = ?, metrics_json = ?
            WHERE episode_id = ?
            """,
            (
                ended_at,
                duration,
                status,
                error,
                str(Path(output_path)) if output_path else None,
                _dumps(metrics),
                episode_id,
            ),
        )


def cleanup_old_episodes(retention_days=180):
    cutoff = time.time() - (retention_days * 24 * 60 * 60)
    with get_conn() as conn:
        result = conn.execute(
            "DELETE FROM episodes WHERE ended_at IS NOT NULL AND ended_at < ?",
            (cutoff,),
        )
    return result.rowcount


def _all_done(status):
    return bool(status) and all((entry or {}).get("state") == "done" for entry in status.values())


def _job_ready(job):
    status = job.get("status", {})
    output_path = job.get("output_path")
    html_ready = bool(job.get("html_ready") and output_path and Path(output_path).exists())
    return bool(job.get("error") is None and html_ready and _all_done(status))


def build_job_metrics(job, reconciled=False):
    status = (job or {}).get("status", {})
    state_counts = {"done": 0, "failed": 0, "running": 0, "queued": 0, "other": 0}
    topic_durations = {}

    for slug, entry in status.items():
        state = (entry or {}).get("state")
        if state == "done":
            state_counts["done"] += 1
        elif state == "failed":
            state_counts["failed"] += 1
        elif state in {"active", "running"}:
            state_counts["running"] += 1
        elif state in {"queued", "waiting"}:
            state_counts["queued"] += 1
        else:
            state_counts["other"] += 1

        started_at = (entry or {}).get("started_at")
        ended_at = (entry or {}).get("ended_at")
        if isinstance(started_at, (int, float)) and isinstance(ended_at, (int, float)):
            topic_durations[slug] = max(0.0, ended_at - started_at)

    metrics = {
        "topic_count": len(status),
        "state_counts": state_counts,
        "topic_durations_sec": topic_durations,
    }
    if reconciled:
        metrics["reconciled"] = True
    return metrics


def reconcile_running_episodes(load_job_fn):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT episode_id, job_id FROM episodes WHERE status = 'running'"
        ).fetchall()

    reconciled_count = 0
    for row in rows:
        episode_id = row["episode_id"]
        job_id = row["job_id"]
        job = load_job_fn(job_id) if (load_job_fn and job_id) else None

        if job and _job_ready(job):
            finish_episode(
                episode_id,
                "success",
                output_path=job.get("output_path"),
                metrics=build_job_metrics(job, reconciled=True),
            )
        elif job and job.get("error"):
            finish_episode(
                episode_id,
                "failed",
                error=job.get("error"),
                metrics=build_job_metrics(job, reconciled=True),
            )
        else:
            finish_episode(
                episode_id,
                "aborted",
                error="Marked as aborted during startup reconciliation.",
                metrics=build_job_metrics(job, reconciled=True),
            )
        reconciled_count += 1

    return reconciled_count


def find_running_episode_id(job_id):
    if not job_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT episode_id
            FROM episodes
            WHERE job_id = ? AND status = 'running'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    return row["episode_id"] if row else None
