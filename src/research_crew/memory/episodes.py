import time
import uuid
from pathlib import Path
from typing import Any

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


def _normalize_query_text(raw: str | None) -> str:
    return " ".join((raw or "").strip().lower().split())


def record_search_event(
    *,
    query_text: str,
    job_id: str | None = None,
    episode_id: str | None = None,
    event_type: str = "search",
    outcome: str = "ok",
    result_count: int | None = None,
    results: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
):
    query = " ".join((query_text or "").strip().split())
    normalized = _normalize_query_text(query)
    if not normalized:
        return False

    safe_results = results if isinstance(results, list) else []
    safe_count = int(result_count) if isinstance(result_count, int) else len(safe_results)
    safe_type = str(event_type or "search").strip().lower() or "search"
    safe_outcome = str(outcome or "ok").strip().lower() or "ok"
    safe_metadata = metadata if isinstance(metadata, dict) else {}

    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO search_events (
                    event_id, episode_id, job_id, created_at,
                    query_text, normalized_query, event_type, outcome,
                    result_count, results_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    episode_id,
                    job_id,
                    time.time(),
                    query,
                    normalized,
                    safe_type,
                    safe_outcome,
                    safe_count,
                    _dumps(safe_results),
                    _dumps(safe_metadata),
                ),
            )
    except Exception:
        return False
    return True


def record_search_context_feedback(
    *,
    search_queries: list[str] | None,
    job_id: str | None = None,
    episode_id: str | None = None,
    event_type: str = "used_in_run",
    outcome: str = "started",
    metadata: dict[str, Any] | None = None,
):
    queries = []
    seen = set()
    for raw in search_queries or []:
        cleaned = " ".join(str(raw or "").strip().split())
        normalized = _normalize_query_text(cleaned)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        queries.append(cleaned)
    if not queries:
        return 0

    wrote = 0
    for query in queries:
        if record_search_event(
            query_text=query,
            job_id=job_id,
            episode_id=episode_id,
            event_type=event_type,
            outcome=outcome,
            result_count=None,
            results=None,
            metadata=metadata,
        ):
            wrote += 1
    return wrote


def _canonical_slug(raw: str | None) -> str:
    return (raw or "").strip().lower().replace("-", "_")


def _anchor_slug(raw: str | None) -> str:
    slug = _canonical_slug(raw)
    if "__" in slug:
        return slug.split("__", 1)[0]
    return slug


def _normalize_topic_entry(raw: Any):
    if isinstance(raw, str):
        slug = _canonical_slug(raw)
        if not slug:
            return None
        return {
            "slug": slug,
            "base_slug": _anchor_slug(slug),
            "label": slug.replace("_", " ").title(),
            "type": "template",
        }
    if not isinstance(raw, dict):
        return None
    slug = _canonical_slug(str(raw.get("slug", "")))
    if not slug:
        return None
    base_slug = _canonical_slug(str(raw.get("base_slug") or raw.get("anchor_slug") or _anchor_slug(slug)))
    return {
        "slug": slug,
        "base_slug": base_slug or _anchor_slug(slug),
        "label": str(raw.get("label") or raw.get("topic") or slug.replace("_", " ").title()),
        "type": str(raw.get("type") or "template"),
    }


def _record_topic_event(
    conn,
    *,
    episode_id: str | None,
    job_id: str | None,
    topic_slug: str,
    base_slug: str,
    topic_label: str,
    event_type: str,
    outcome: str,
    score: float,
    metadata: dict[str, Any] | None = None,
):
    conn.execute(
        """
        INSERT INTO topic_events (
            event_id, episode_id, job_id, created_at,
            topic_slug, base_slug, topic_label, event_type, outcome, score, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            episode_id,
            job_id,
            time.time(),
            topic_slug,
            base_slug,
            topic_label,
            event_type,
            outcome,
            float(score),
            _dumps(metadata or {}),
        ),
    )


def record_proposal_impressions(
    *,
    job_id: str | None = None,
    episode_id: str | None = None,
    proposed_topics: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
):
    """Record which topic proposals were shown to the user."""
    payload: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in proposed_topics or []:
        normalized = _normalize_topic_entry(raw)
        if not normalized:
            continue
        source = raw if isinstance(raw, dict) else {}
        row_metadata: dict[str, Any] = {
            "topic_type": normalized["type"],
            "shown": True,
        }
        if source.get("confidence"):
            row_metadata["confidence"] = source.get("confidence")
        if source.get("score") is not None:
            row_metadata["score"] = source.get("score")
        if source.get("rationale"):
            row_metadata["rationale"] = source.get("rationale")
        if metadata:
            row_metadata.update(metadata)
        payload.append((normalized, row_metadata))

    if not payload:
        return 0

    wrote = 0
    try:
        with get_conn() as conn:
            for topic, row_metadata in payload:
                _record_topic_event(
                    conn,
                    episode_id=episode_id,
                    job_id=job_id,
                    topic_slug=topic["slug"],
                    base_slug=topic["base_slug"],
                    topic_label=topic["label"],
                    event_type="impression",
                    outcome="shown",
                    score=0.05,
                    metadata=row_metadata,
                )
                wrote += 1
    except Exception:
        return 0
    return wrote


def record_selection_feedback(
    job_id: str | None,
    episode_id: str | None,
    proposed_topics: list[Any] | None,
    approved_topics: list[Any] | None,
):
    """Record user approval/skips so ranking can adapt to explicit choices."""
    proposed = [_normalize_topic_entry(item) for item in (proposed_topics or [])]
    approved = [_normalize_topic_entry(item) for item in (approved_topics or [])]
    proposed = [item for item in proposed if item]
    approved = [item for item in approved if item]
    if not proposed and not approved:
        return 0

    approved_slugs = {item["slug"] for item in approved}
    wrote = 0
    try:
        with get_conn() as conn:
            if proposed:
                for item in proposed:
                    selected = item["slug"] in approved_slugs
                    outcome = "approved" if selected else "skipped"
                    score = 0.8 if selected else -0.15
                    _record_topic_event(
                        conn,
                        episode_id=episode_id,
                        job_id=job_id,
                        topic_slug=item["slug"],
                        base_slug=item["base_slug"],
                        topic_label=item["label"],
                        event_type="selection",
                        outcome=outcome,
                        score=score,
                        metadata={"topic_type": item["type"], "selected": selected},
                    )
                    wrote += 1
            else:
                for item in approved:
                    _record_topic_event(
                        conn,
                        episode_id=episode_id,
                        job_id=job_id,
                        topic_slug=item["slug"],
                        base_slug=item["base_slug"],
                        topic_label=item["label"],
                        event_type="selection",
                        outcome="approved",
                        score=0.7,
                        metadata={"topic_type": item["type"], "selected": True, "source": "manual"},
                    )
                    wrote += 1
    except Exception:
        return 0
    return wrote


def record_execution_feedback(
    job_id: str | None,
    episode_id: str | None,
    approved_topics: list[Any] | None,
    status_map: dict[str, Any] | None,
    error: str | None = None,
):
    """Record per-topic execution outcomes (success/failure/missing)."""
    topics = [_normalize_topic_entry(item) for item in (approved_topics or [])]
    topics = [item for item in topics if item]
    if not topics:
        return 0

    status_map = status_map or {}
    score_by_outcome = {
        "success": 1.0,
        "failed": -0.75,
        "missing": -0.55,
        "aborted": -0.45,
    }
    wrote = 0
    try:
        with get_conn() as conn:
            for item in topics:
                st = status_map.get(item["slug"])
                if st is None and "__" in item["slug"]:
                    st = status_map.get(item["slug"].split("__", 1)[0])
                state = (st or {}).get("state")
                if state == "done":
                    outcome = "success"
                elif state == "missing":
                    outcome = "missing"
                elif state == "failed":
                    outcome = "failed"
                else:
                    outcome = "aborted" if error else "failed"

                _record_topic_event(
                    conn,
                    episode_id=episode_id,
                    job_id=job_id,
                    topic_slug=item["slug"],
                    base_slug=item["base_slug"],
                    topic_label=item["label"],
                    event_type="execution",
                    outcome=outcome,
                    score=score_by_outcome[outcome],
                    metadata={"state": state, "job_error": error},
                )
                wrote += 1
    except Exception:
        return 0
    return wrote


def record_delivery_feedback(
    job_id: str | None,
    episode_id: str | None,
    approved_topics: list[Any] | None,
    delivery_status: str,
):
    """Record whether generated topics were actually sent to users."""
    topics = [_normalize_topic_entry(item) for item in (approved_topics or [])]
    topics = [item for item in topics if item]
    if not topics:
        return 0

    normalized = str(delivery_status or "").strip().lower()
    if normalized in {"success", "sent"}:
        outcome = "sent"
        score = 1.25
    elif normalized in {"failed", "send_failed"}:
        outcome = "send_failed"
        score = -0.45
    else:
        outcome = "send_blocked"
        score = -0.1

    wrote = 0
    try:
        with get_conn() as conn:
            for item in topics:
                _record_topic_event(
                    conn,
                    episode_id=episode_id,
                    job_id=job_id,
                    topic_slug=item["slug"],
                    base_slug=item["base_slug"],
                    topic_label=item["label"],
                    event_type="delivery",
                    outcome=outcome,
                    score=score,
                    metadata={"delivery_status": normalized},
                )
                wrote += 1
    except Exception:
        return 0
    return wrote
