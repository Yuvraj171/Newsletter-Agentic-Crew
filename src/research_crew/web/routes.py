from flask import Blueprint, render_template, request, send_file, abort, url_for
import json
import queue
import hashlib
import os
import time
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import threading
import uuid
from typing import Any

from research_crew.newsletter_service import generate_newsletter, send_newsletter_email
from research_crew.web.db import load_job, load_latest_job, save_email_history_entry, save_job, find_duplicate_send
from research_crew.memory.episodes import (
    create_episode,
    finish_episode,
    build_job_metrics,
    find_running_episode_id,
    record_proposal_impressions,
    record_selection_feedback,
    record_execution_feedback,
    record_delivery_feedback,
    record_search_event,
    record_search_context_feedback,
)
from research_crew.main import topic_definitions
from research_crew.topic_planner import (
    DEFAULT_TEMPLATE_PROPOSAL_COUNT,
    build_freeform_topic_label,
    propose_topics,
)

bp = Blueprint("web", __name__)

TOPICS = [
    {"slug": "ai_at_work", "label": "AI at Work", "icon": "\U0001F916"},
    {"slug": "it_hacks", "label": "IT Hacks", "icon": "\U0001F527"},
    {"slug": "o365_updates", "label": "O365 Updates", "icon": "\u2601"},
    {"slug": "tech_discovery", "label": "Tech Discovery", "icon": "\U0001F50D"},
    {"slug": "tech_trends", "label": "Tech Trends", "icon": "\U0001F680"},
]
TOPIC_SLUGS = {t["slug"] for t in TOPICS}
TOPIC_BY_SLUG = {t["slug"]: t for t in TOPICS}
TOPIC_DEF_BY_SLUG = {d["topic_slug"]: d for d in topic_definitions}
EMAIL_GROUPS = {
    "all": [],
    "ops": [],
    "eng": [],
}

# In-memory job store: resets on restart.
JOBS = {}
RUNTIME_PATH = Path("instance") / "memory" / "topic_runtimes.json"
RUNTIME_LOCK = threading.Lock()
RUNTIME_CACHE = {}
RUNTIME_LOADED = False
WORK_QUEUE = queue.Queue()
WORKER_LOCK = threading.Lock()
WORKER_STARTED = False
APP_START_TS = time.time()
MAX_JOB_RUNTIME_SECONDS = 45 * 60


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


DYNAMIC_TOPICS_ENABLED = env_flag("DYNAMIC_TOPICS_ENABLED", False)
FREEFORM_TOPIC_SLOT_ENABLED = env_flag("FREEFORM_TOPIC_SLOT_ENABLED", False)
SEARCH_CONTEXT_IN_PROMPTS = env_flag("SEARCH_CONTEXT_IN_PROMPTS", True)
MAX_TEMPLATE_PROPOSALS = env_int("DYNAMIC_TOPIC_CANDIDATES", DEFAULT_TEMPLATE_PROPOSAL_COUNT)
AUTO_BROAD_QUERY_SECTION_CAP = 3

BROAD_QUERY_MARKERS = {
    "trend",
    "trends",
    "latest",
    "news",
    "updates",
    "overview",
    "landscape",
    "future",
    "best",
    "top",
    "tools",
    "ideas",
    "strategies",
    "innovation",
    "innovations",
    "market",
    "use cases",
}

SPECIFIC_QUERY_MARKERS = {
    "pricing",
    "price",
    "cost",
    "tutorial",
    "how to",
    "setup",
    "configure",
    "integration",
    "compare",
    "vs",
    "alternative",
}


def canonicalize_slug(slug: str | None) -> str:
    """Normalize slugs so UI inputs (hyphen) and backend (underscore) stay aligned."""
    return (slug or "").replace("-", "_")


def parse_selected(selected_csv: str):
    parts = (selected_csv or "").split(",")
    normalized = [canonicalize_slug(p) for p in parts]
    return [slug for slug in normalized if slug in TOPIC_SLUGS]


def parse_json_list(raw: str | None):
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def parse_search_results(raw: str | None):
    rows = parse_json_list(raw)
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        link = str(row.get("link") or "").strip()
        snippet = str(row.get("snippet") or "").strip()
        domain = str(row.get("domain") or "").strip()
        if not title or not link:
            continue
        if not domain:
            parsed = urllib.parse.urlparse(link)
            domain = parsed.netloc
        normalized.append(
            {
                "title": title,
                "link": link,
                "snippet": snippet,
                "domain": domain,
            }
        )
        if len(normalized) >= 5:
            break
    return normalized


def safe_int(raw: str | None, default: int = 0) -> int:
    try:
        return int(raw or default)
    except (TypeError, ValueError):
        return default


def parse_search_queries(raw_text: str):
    text = (raw_text or "").replace("\r", "\n").strip()
    if not text:
        return []
    queries = []
    for line in text.split("\n"):
        for part in line.split(","):
            query = part.strip()
            if query:
                queries.append(query)
    return queries


def append_search_query(search_queries: list[str], query: str):
    cleaned = " ".join((query or "").strip().split())
    if not cleaned:
        return list(search_queries or [])
    out = list(search_queries or [])
    existing = {q.strip().lower() for q in out}
    if cleaned.strip().lower() not in existing:
        out.append(cleaned)
    return out


def normalize_query_text(raw: str | None) -> str:
    return " ".join(str(raw or "").strip().split())


def infer_query_scope(query_text: str) -> str:
    lower = normalize_query_text(query_text).lower()
    if not lower:
        return "single"

    token_count = len(re.findall(r"[a-z0-9]+", lower))
    has_broad_marker = any(marker in lower for marker in BROAD_QUERY_MARKERS)
    has_specific_marker = any(marker in lower for marker in SPECIFIC_QUERY_MARKERS)
    has_multi_intent_pattern = any(sep in lower for sep in [",", " and ", " or ", "/", "|", "&"])

    if has_specific_marker and not has_broad_marker and token_count <= 10:
        return "single"
    if has_broad_marker:
        return "multi"
    if has_multi_intent_pattern and token_count >= 4:
        return "multi"
    if token_count >= 9 and not has_specific_marker:
        return "multi"
    return "single"


def build_search_topic_plan(query_text: str, search_queries: list[str]):
    cleaned_query = normalize_query_text(query_text)
    scope = infer_query_scope(cleaned_query)
    if scope == "single":
        digest = hashlib.sha1(f"{cleaned_query}|{time.time()}".encode("utf-8")).hexdigest()[:10]
        slug = f"search_{digest}"
        approved_topics = [
            {
                "slug": slug,
                "type": "freeform",
                "label": cleaned_query,
                "topic": cleaned_query,
                "base_slug": "freeform",
                "anchor_label": "Search Topic",
                "icon": "*",
                "system_suggested": True,
            }
        ]
        return approved_topics, "search_auto_single", "single"

    catalog = searchable_catalog([t["slug"] for t in TOPICS])
    proposals, _planner_warning = propose_topics(
        catalog,
        search_queries,
        include_freeform=False,
        max_template_proposals=AUTO_BROAD_QUERY_SECTION_CAP,
    )

    approved_topics = []
    for proposal in proposals[:AUTO_BROAD_QUERY_SECTION_CAP]:
        if not isinstance(proposal, dict):
            continue
        slug = canonicalize_slug(str(proposal.get("slug")))
        proposal_type = str(proposal.get("type", "dynamic_template")).strip().lower()
        base_slug = canonicalize_slug(
            str(proposal.get("base_slug") or proposal.get("anchor_slug") or slug.split("__", 1)[0])
        )
        if proposal_type == "template":
            if slug not in TOPIC_SLUGS:
                continue
            base_slug = slug
        elif proposal_type in {"dynamic_template", "template_variant"}:
            if base_slug not in TOPIC_SLUGS:
                continue
        else:
            continue

        anchor = TOPIC_BY_SLUG.get(base_slug, {})
        approved_topics.append(
            {
                "slug": slug,
                "type": proposal_type,
                "label": proposal.get("label") or proposal.get("topic") or slug.replace("_", " ").title(),
                "topic": proposal.get("topic") or proposal.get("label") or slug.replace("_", " ").title(),
                "base_slug": base_slug,
                "anchor_label": proposal.get("anchor_label") or anchor.get("label"),
                "icon": proposal.get("icon") or anchor.get("icon", "*"),
                "rationale": proposal.get("rationale"),
                "confidence": proposal.get("confidence"),
                "system_suggested": True,
            }
        )

    if approved_topics:
        return approved_topics, "search_auto_multi", "multi"

    # Safety fallback: if planner yields nothing, generate one focused free-form topic.
    digest = hashlib.sha1(f"{cleaned_query}|{time.time()}".encode("utf-8")).hexdigest()[:10]
    slug = f"search_{digest}"
    fallback_topic = {
        "slug": slug,
        "type": "freeform",
        "label": cleaned_query,
        "topic": cleaned_query,
        "base_slug": "freeform",
        "anchor_label": "Search Topic",
        "icon": "*",
        "system_suggested": True,
    }
    return [fallback_topic], "search_auto_single", "single"


def serper_google_search(query: str, *, top_k: int = 5, timeout_sec: float = 20.0):
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is missing.")

    payload = json.dumps({"q": query, "num": max(1, min(int(top_k), 10))}).encode("utf-8")
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=payload,
        method="POST",
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Serper HTTP {exc.code}: {details[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Serper connection failed: {exc.reason}") from exc

    try:
        payload_json = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Serper returned invalid JSON.") from exc

    organic = payload_json.get("organic")
    if not isinstance(organic, list):
        return []

    results = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = str(item.get("link") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not title or not link:
            continue
        domain = urllib.parse.urlparse(link).netloc
        results.append(
            {
                "title": title,
                "link": link,
                "snippet": snippet,
                "domain": domain,
            }
        )
        if len(results) >= top_k:
            break
    return results


def parse_emails(csv: str):
    return [e.strip() for e in (csv or "").split(",") if e and "@" in e]


def resolve_recipients(group: str, extra: str):
    base = EMAIL_GROUPS.get(group, [])
    extras = parse_emails(extra)
    # de-dupe while preserving order
    seen = set()
    out = []
    for e in [*base, *extras]:
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def default_subject():
    month = time.strftime("%B")
    return f"BEST Group Tech Newsletter - {month}"


def normalize_subject(value):
    text = value.strip() if isinstance(value, str) else ""
    if not text or text.lower() in {"none", "null"}:
        return default_subject()
    return text


def now_timestamp():
    return time.strftime("%Y-%m-%d %H:%M")


def build_run_config(selected_slugs, search_queries, *, search_results=None):
    return {
        "entrypoint": "web_ui",
        "selected_topics": list(selected_slugs or []),
        "topic_count": len(selected_slugs or []),
        "search_query_count": len(search_queries or []),
        "search_result_count": len(search_results or []),
        "dynamic_topics_enabled": DYNAMIC_TOPICS_ENABLED,
        "search_context_in_prompts": SEARCH_CONTEXT_IN_PROMPTS,
    }


def searchable_catalog(selected_slugs: list[str] | None = None):
    allowed = set(selected_slugs or TOPIC_SLUGS)
    catalog = []
    for definition in topic_definitions:
        slug = definition["topic_slug"]
        if slug not in allowed:
            continue
        visual = TOPIC_BY_SLUG.get(slug, {})
        catalog.append(
            {
                "slug": slug,
                "label": visual.get("label", definition["topic"]),
                "icon": visual.get("icon", "*"),
                "topic": definition["topic"],
                "research_brief": definition["research_brief"],
            }
        )
    return catalog


def selected_topic_cards(job: dict[str, Any]) -> list[dict[str, str]]:
    approved_by_slug = {
        canonicalize_slug(str(item.get("slug"))): item
        for item in (job.get("approved_topics") or [])
        if isinstance(item, dict)
    }
    cards = []
    for slug in job.get("selected", []):
        canonical = canonicalize_slug(slug)
        visual = TOPIC_BY_SLUG.get(canonical)
        approved = approved_by_slug.get(canonical, {})
        if visual:
            cards.append({"slug": canonical, "label": visual["label"], "icon": visual["icon"]})
            continue
        if approved:
            cards.append(
                {
                    "slug": canonical,
                    "label": str(approved.get("label") or approved.get("topic") or canonical.replace("_", " ").title()),
                    "icon": str(approved.get("icon") or approved.get("base_icon") or "*"),
                }
            )
            continue
        cards.append({"slug": canonical, "label": canonical.replace("_", " ").title(), "icon": "*"})
    return cards


def render_topics_fragment(
    *,
    selected: list[str],
    job_id: str | None,
    error: str | None = None,
    search_queries_text: str = "",
    proposals: list[dict[str, Any]] | None = None,
    proposal_version: int = 0,
    planner_warning: str | None = None,
    topic_scope: str = "static_manual",
    allow_manual_fallback: bool = False,
    search_results: list[dict[str, str]] | None = None,
    search_error: str | None = None,
    searched_query: str = "",
):
    safe_search_results = search_results or []
    return render_template(
        "fragments/topics.html",
        topics=TOPICS,
        selected=selected,
        job_id=job_id,
        error=error,
        search_queries_text=search_queries_text,
        dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
        freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
        proposals=proposals or [],
        proposal_version=proposal_version,
        proposed_topics_json=json.dumps(proposals or []),
        planner_warning=planner_warning,
        topic_scope=topic_scope,
        allow_manual_fallback=allow_manual_fallback,
        search_results=safe_search_results,
        search_results_json=json.dumps(safe_search_results),
        search_error=search_error,
        searched_query=searched_query,
        freeform_preview=build_freeform_topic_label(parse_search_queries(search_queries_text))
        if FREEFORM_TOPIC_SLOT_ENABLED
        else None,
    )


def normalize_html(html):
    return " ".join((html or "").split())

def hash_html(html):
    normalized = normalize_html(html)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_runtime_cache():
    global RUNTIME_LOADED
    if RUNTIME_LOADED:
        return
    RUNTIME_LOADED = True
    if not RUNTIME_PATH.exists():
        return
    try:
        payload = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, list):
                durations = [float(v) for v in value if isinstance(v, (int, float))]
                if durations:
                    RUNTIME_CACHE[key] = durations

def runtime_snapshot():
    with RUNTIME_LOCK:
        load_runtime_cache()
        return {k: list(v) for k, v in RUNTIME_CACHE.items()}

def update_runtime_cache(slug, duration, max_samples=5):
    if duration <= 0:
        return
    with RUNTIME_LOCK:
        load_runtime_cache()
        durations = RUNTIME_CACHE.get(slug, [])
        durations.append(duration)
        if len(durations) > max_samples:
            durations = durations[-max_samples:]
        RUNTIME_CACHE[slug] = durations
        RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_PATH.write_text(json.dumps(RUNTIME_CACHE, indent=2), encoding="utf-8")

def duration_stats(durations):
    if not durations:
        return None, None
    values = sorted(durations)
    n = len(values)
    mid = n // 2
    if n % 2 == 1:
        median = values[mid]
    else:
        median = (values[mid - 1] + values[mid]) / 2
    idx = int((n - 1) * 0.95)
    p95 = values[idx]
    return median, p95

def format_eta_range(min_seconds, max_seconds):
    min_seconds = max(0.0, min_seconds)
    max_seconds = max(0.0, max_seconds)
    if max_seconds <= 90:
        min_val = int(round(min_seconds))
        max_val = int(round(max_seconds))
        if min_val == max_val:
            return f"{min_val} sec"
        return f"{min_val}-{max_val} sec"
    min_min = max(1, int(round(min_seconds / 60)))
    max_min = max(1, int(round(max_seconds / 60)))
    if min_min == max_min:
        return f"{min_min} min"
    return f"{min_min}-{max_min} min"

def compute_eta(status):
    if not status:
        return {}, None
    stats = runtime_snapshot()
    now = time.time()
    eta_by_slug = {}
    total_min = 0.0
    total_max = 0.0
    has_any = False
    for slug, entry in status.items():
        durations = stats.get(slug, [])
        median, p95 = duration_stats(durations)
        if median is None or p95 is None:
            continue
        state = entry.get("state")
        elapsed = 0.0
        if state in {"active", "running"}:
            started_at = entry.get("started_at") or now
            elapsed = max(0.0, now - started_at)
        if state in {"done", "failed", "missing"}:
            elapsed = median
        rem_min = max(0.0, median - elapsed)
        rem_max = max(0.0, p95 - elapsed)
        eta_by_slug[slug] = format_eta_range(rem_min, rem_max)
        total_min += rem_min
        total_max += rem_max
        has_any = True
    eta_range = format_eta_range(total_min, total_max) if has_any else None
    return eta_by_slug, eta_range



def percent_complete(status):
    if not status:
        return 0
    now = time.time()
    stats = runtime_snapshot()
    per_topic = []
    for slug, entry in status.items():
        durations = stats.get(slug, [])
        median, _ = duration_stats(durations)
        per_topic.append(topic_progress(entry, now, median))
    return int(sum(per_topic) / len(per_topic)) if per_topic else 0

def all_done(status):
    return all(s.get("state") == "done" for s in status.values()) if status else False


def topic_progress(entry, now, expected_duration):
    state = entry.get("state")
    if state in {"done", "failed", "missing"}:
        return 100
    if state in {"active", "running"}:
        started_at = entry.get("started_at") or now
        elapsed = max(0.0, now - started_at)
        base = 5
        cap = 90
        if expected_duration and expected_duration > 0:
            ratio = min(elapsed / expected_duration, 1.0)
            return int(base + (cap - base) * ratio)
        ramp_seconds = 120
        ratio = min(elapsed / ramp_seconds, 1.0)
        return int(base + (cap - base) * ratio)
    return 0

def job_is_ready(job):
    status = job.get("status", {})
    path = job.get("output_path")
    html_ready = job.get("html_ready") and path and Path(path).exists()
    return bool(html_ready and job.get("error") is None and all_done(status))


def maybe_abort_stalled_job(job):
    status = job.get("status", {})
    if not status or job.get("error") or job_is_ready(job):
        return False

    now = time.time()
    started_values = []
    for entry in status.values():
        state = (entry or {}).get("state")
        if state in {"queued", "active", "running", "waiting"}:
            started_values.append((entry or {}).get("started_at") or now)

    if not started_values:
        return False

    oldest_started = min(started_values)
    if (now - oldest_started) < MAX_JOB_RUNTIME_SECONDS:
        return False

    reason = f"Run exceeded {MAX_JOB_RUNTIME_SECONDS // 60} minutes; marked as aborted."
    job["error"] = reason
    for entry in status.values():
        state = (entry or {}).get("state")
        if state in {"queued", "active", "running", "waiting"}:
            entry["state"] = "failed"
            entry["message"] = "Aborted: runtime threshold exceeded."
            if entry.get("ended_at") is None:
                entry["ended_at"] = now
    save_job(job)

    episode_id = job.get("episode_id") or find_running_episode_id(job.get("job_id"))
    if episode_id:
        finish_episode(
            episode_id,
            "aborted",
            error=reason,
            metrics=build_job_metrics(job),
        )
    return True


def build_notice(job):
    if not job:
        return {"notice": "Pick sections to start a run.", "notice_level": "info"}

    status = job.get("status", {})
    ready = job_is_ready(job)
    progress = percent_complete(status)
    error = job.get("error")
    if error:
        return {"notice": f"Generation failed: {error}", "notice_level": "danger"}

    if job.get("email_sending"):
        return {"notice": "Sending email...", "notice_level": "info"}

    email_error = job.get("email_error")
    if email_error:
        warn_errors = {
            "No recipients selected.",
            "Draft is not ready or not approved.",
            "Please confirm recipients before sending.",
            "Duplicate content detected. Review and allow duplicate to send.",
        }
        level = "warning" if email_error in warn_errors else "danger"
        return {"notice": f"Email error: {email_error}", "notice_level": level}

    if job.get("email_sent"):
        sent_to = job.get("email_sent_to", [])
        count = len(sent_to)
        label = "recipient" if count == 1 else "recipients"
        return {"notice": f"Email sent to {count} {label}.", "notice_level": "success"}

    if ready and job.get("approved"):
        recipients = resolve_recipients(job.get("email_group", "all"), job.get("email_extra", ""))
        count = len(recipients)
        if count == 0:
            return {"notice": "Draft approved. Add recipients to send.", "notice_level": "warning"}
        label = "recipient" if count == 1 else "recipients"
        return {"notice": f"Draft approved. Ready to send to {count} {label}.", "notice_level": "success"}

    if ready:
        return {"notice": "Draft ready. Review and approve to unlock email.", "notice_level": "success"}

    if status:
        return {"notice": f"Research in progress ({progress}% complete).", "notice_level": "info"}

    return {"notice": "Run started. Preparing tasks...", "notice_level": "info"}


def successful_history(history):
    return [h for h in history if h.get("status") == "success"]


def next_send_label(history):
    return "Send Email" if not successful_history(history) else "Resend Email"


def next_send_hint(history):
    return "First send" if not successful_history(history) else "Resend"


def add_send_history(job, status, recipients, subject, error=None, content_hash=None):
    history = job.setdefault("email_history", [])
    attempt = len(history) + 1
    send_count = len(successful_history(history)) + 1
    label = "First send" if send_count == 1 else "Resend"
    entry = {
        "attempt": attempt,
        "label": label,
        "status": status,
        "timestamp": now_timestamp(),
        "recipient_count": len(recipients or []),
        "recipients": recipients or [],
        "subject": subject,
        "error": error,
        "content_hash": content_hash,
    }
    history.append(entry)
    save_email_history_entry(job["job_id"], entry)
    save_job(job)
    return entry


def init_job(
    selected_slugs,
    *,
    search_queries=None,
    search_results=None,
    proposed_topics=None,
    approved_topics=None,
    topic_scope="static_manual",
):
    job_id = uuid.uuid4().hex
    status = {slug: {"state": "queued", "message": "Queued", "started_at": None} for slug in selected_slugs}
    JOBS[job_id] = {
        "job_id": job_id,
        "episode_id": None,
        "status": status,
        "html_ready": False,
        "review_confirmed": False,
        "approved": False,
        "error": None,
        "output_path": None,
        "selected": selected_slugs,
        "search_queries": list(search_queries or []),
        "search_results": list(search_results or []),
        "proposed_topics": list(proposed_topics or []),
        "approved_topics": list(approved_topics or []),
        "topic_scope": topic_scope,
        "email_sent": False,
        "email_error": None,
        "email_sending": False,
        "email_sent_to": [],
        "email_group": "all",
        "email_extra": "",
        "email_history": [],
        "email_preview": None,
    }
    save_job(JOBS[job_id])
    return job_id


def start_generation_job(
    *,
    run_selected_slugs: list[str],
    search_queries: list[str],
    search_results: list[dict[str, Any]] | None,
    proposed_topics: list[dict[str, Any]] | None,
    approved_topics: list[dict[str, Any]] | None,
    topic_scope: str,
    run_config_updates: dict[str, Any] | None = None,
):
    job_id = init_job(
        run_selected_slugs,
        search_queries=search_queries,
        search_results=search_results,
        proposed_topics=proposed_topics,
        approved_topics=approved_topics,
        topic_scope=topic_scope,
    )
    run_config = build_run_config(
        run_selected_slugs,
        search_queries,
        search_results=search_results,
    )
    if run_config_updates:
        run_config.update(run_config_updates)

    episode_id = create_episode(
        run_selected_slugs,
        search_queries=search_queries,
        job_id=job_id,
        run_config=run_config,
    )
    JOBS[job_id]["episode_id"] = episode_id
    JOBS[job_id]["search_queries"] = list(search_queries or [])
    JOBS[job_id]["search_results"] = list(search_results or [])
    JOBS[job_id]["proposed_topics"] = list(proposed_topics or [])
    JOBS[job_id]["approved_topics"] = list(approved_topics or [])
    JOBS[job_id]["topic_scope"] = topic_scope
    JOBS[job_id]["run_config"] = run_config

    record_selection_feedback(
        job_id=job_id,
        episode_id=episode_id,
        proposed_topics=proposed_topics,
        approved_topics=approved_topics,
    )
    record_search_context_feedback(
        search_queries=search_queries,
        job_id=job_id,
        episode_id=episode_id,
        event_type="used_in_run",
        outcome="started",
        metadata={
            "topic_scope": topic_scope,
            "approved_topics": len(approved_topics or []),
            "search_result_count": len(search_results or []),
        },
    )
    save_job(JOBS[job_id])
    enqueue_job(job_id)
    return job_id


def run_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return
    selected = job["selected"]
    approved_topics = job.get("approved_topics") or []
    search_queries = job.get("search_queries") or []
    search_results = job.get("search_results") or []
    episode_id = job.get("episode_id")

    def status_cb(slug, state, detail=""):
        st = job["status"].get(slug)
        if st is None:
            job["status"][slug] = {"state": state, "message": detail, "started_at": time.time()}
        else:
            st["state"] = state
            st["message"] = detail
            if st["started_at"] is None and state in {"active", "running", "done"}:
                st["started_at"] = time.time()
            if state == "done" and st.get("ended_at") is None:
                st["ended_at"] = time.time()
                duration = st["ended_at"] - (st.get("started_at") or st["ended_at"])
                update_runtime_cache(slug, duration)

        save_job(job)

    def log_cb(msg):
        # TODO: store logs per job if needed
        pass

    try:
        html, outp, _ = generate_newsletter(
            selected_slugs=selected,
            selected_topics=approved_topics,
            search_queries=search_queries,
            search_results=search_results,
            cb=log_cb,
            status_cb=status_cb,
        )
        job["html_ready"] = True
        job["output_path"] = Path(outp)
        for slug, st in job["status"].items():
            if st["state"] != "failed":
                st["state"] = "done"
                st["message"] = "Finished."
        save_job(job)
        if episode_id:
            record_execution_feedback(
                job_id=job_id,
                episode_id=episode_id,
                approved_topics=approved_topics,
                status_map=job.get("status"),
                error=None,
            )
            record_search_context_feedback(
                search_queries=search_queries,
                job_id=job_id,
                episode_id=episode_id,
                event_type="run",
                outcome="success",
                metadata={"stage": "generation"},
            )
            finish_episode(
                episode_id,
                "success",
                output_path=outp,
                metrics=build_job_metrics(job),
            )
    except Exception as e:
        job["error"] = str(e)
        for st in job["status"].values():
            if st["state"] in {"queued", "active", "waiting"}:
                st["state"] = "failed"
                st["message"] = f"Failed: {e}"
        save_job(job)
        if episode_id:
            record_execution_feedback(
                job_id=job_id,
                episode_id=episode_id,
                approved_topics=approved_topics,
                status_map=job.get("status"),
                error=str(e),
            )
            record_search_context_feedback(
                search_queries=search_queries,
                job_id=job_id,
                episode_id=episode_id,
                event_type="run",
                outcome="failed",
                metadata={"stage": "generation", "error": str(e)},
            )
            finish_episode(
                episode_id,
                "failed",
                error=str(e),
                metrics=build_job_metrics(job),
            )


def worker_loop():
    while True:
        job_id = WORK_QUEUE.get()
        try:
            run_job(job_id)
        finally:
            WORK_QUEUE.task_done()

def ensure_worker():
    global WORKER_STARTED
    with WORKER_LOCK:
        if WORKER_STARTED:
            return
        thread = threading.Thread(target=worker_loop, daemon=True)
        thread.start()
        WORKER_STARTED = True

def enqueue_job(job_id):
    ensure_worker()
    WORK_QUEUE.put(job_id)

def get_job_or_404(job_id):
    job = JOBS.get(job_id)
    if not job:
        job = load_job(job_id)
        if job:
            JOBS[job_id] = job
    if not job:
        abort(404)
    return job


def build_review_context(job_id, *, include_preview=True):
    """Assemble context for review/email fragments so routes stay consistent."""
    job = get_job_or_404(job_id)
    status = job["status"]
    html_ready = job["html_ready"] and job.get("output_path") and Path(job["output_path"]).exists()
    ready = html_ready and job["error"] is None and all_done(status)
    if ready:
        # Auto-unlock approval when ready
        job["review_confirmed"] = True
    resolved_recipients = resolve_recipients(job.get("email_group", "all"), job.get("email_extra", ""))

    preview_snippet = ""
    preview_url = None
    download_url = None
    if include_preview and html_ready:
        try:
            data = Path(job["output_path"]).read_text(encoding="utf-8")
            preview_snippet = data  # full html for iframe srcdoc fallback
            preview_url = url_for("web.download_newsletter", job_id=job_id)
            download_url = preview_url
        except Exception:
            preview_snippet = ""

    topics_selected = selected_topic_cards(job)
    search_queries = job.get("search_queries", [])
    notice_ctx = build_notice(job)
    email_history = job.get("email_history", [])
    last_email_attempt = email_history[-1] if email_history else None
    email_preview = job.get("email_preview")
    next_label = next_send_label(email_history)
    next_hint = next_send_hint(email_history)
    return {
        "job_id": job_id,
        "topics": topics_selected,
        "status": status,
        "ready": ready,
        "review_confirmed": job.get("review_confirmed", False),
        "approved": job.get("approved", False),
        "error": job.get("error"),
        "search_queries": search_queries,
        "search_queries_text": ", ".join(search_queries),
        "topic_scope": job.get("topic_scope", "static_manual"),
        "approved_topics": job.get("approved_topics", []),
        "email_groups": EMAIL_GROUPS,
        "email_group": job.get("email_group", "all"),
        "email_extra": job.get("email_extra", ""),
        "email_sent": job.get("email_sent", False),
        "email_sent_to": job.get("email_sent_to", []),
        "email_error": job.get("email_error"),
        "email_sending": job.get("email_sending", False),
        "email_subject": normalize_subject(job.get("email_subject")),
        "resolved_recipients": resolved_recipients,
        "preview_snippet": preview_snippet,
        "download_url": download_url,
        "preview_url": preview_url,
        "oob": False,
        "email_history": email_history,
        "last_email_attempt": last_email_attempt,
        "email_preview": email_preview,
        "next_send_label": next_label,
        "next_send_hint": next_hint,
        **notice_ctx,
    }


@bp.route("/")
def home():
    if request.args.get("reset") == "1":
        notice_ctx = {"notice": None, "notice_level": "info"}
        return render_template(
            "home.html",
            topics=TOPICS,
            selected=[],
            job_id=None,
            status={},
            progress=0,
            eta_by_slug={},
            eta_range=None,
            ready=False,
            error=None,
            review_confirmed=False,
            search_queries_text="",
            search_results=[],
            search_results_json="[]",
            search_error=None,
            searched_query="",
            dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
            freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
            proposals=[],
            proposal_version=0,
            proposed_topics_json="[]",
            planner_warning=None,
            topic_scope="static_manual",
            allow_manual_fallback=False,
            freeform_preview=build_freeform_topic_label([]) if FREEFORM_TOPIC_SLOT_ENABLED else None,
            **notice_ctx,
        )

    job = load_latest_job(since=APP_START_TS)
    if job:
        JOBS[job["job_id"]] = job
        notice_ctx = build_notice(job)
        status = job.get("status", {})
        eta_by_slug, eta_range = compute_eta(status)
        selected_template_slugs = [s for s in job.get("selected", []) if s in TOPIC_SLUGS]
        proposals = job.get("proposed_topics", [])
        proposal_version = safe_int(job.get("run_config", {}).get("proposed_topics_version") if isinstance(job.get("run_config"), dict) else 0)
        return render_template(
            "home.html",
            topics=TOPICS,
            selected=selected_template_slugs,
            job_id=job["job_id"],
            status=status,
            progress=percent_complete(status),
            eta_by_slug=eta_by_slug,
            eta_range=eta_range,
            ready=job_is_ready(job),
            error=job.get("error"),
            review_confirmed=job.get("review_confirmed", False),
            search_queries_text=", ".join(job.get("search_queries", [])),
            search_results=[],
            search_results_json="[]",
            search_error=None,
            searched_query=(job.get("search_queries") or [""])[0],
            dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
            freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
            proposals=proposals,
            proposal_version=proposal_version,
            proposed_topics_json=json.dumps(proposals),
            planner_warning=None,
            topic_scope=job.get("topic_scope", "static_manual"),
            allow_manual_fallback=False,
            freeform_preview=build_freeform_topic_label(job.get("search_queries", []))
            if FREEFORM_TOPIC_SLOT_ENABLED
            else None,
            **notice_ctx,
        )

    notice_ctx = build_notice(None)
    return render_template(
        "home.html",
        topics=TOPICS,
        selected=[],
        job_id=None,
        status={},
        progress=0,
        eta_by_slug={},
        eta_range=None,
        ready=False,
        error=None,
        review_confirmed=False,
        search_queries_text="",
        search_results=[],
        search_results_json="[]",
        search_error=None,
        searched_query="",
        dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
        freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
        proposals=[],
        proposal_version=0,
        proposed_topics_json="[]",
        planner_warning=None,
        topic_scope="static_manual",
        allow_manual_fallback=False,
        freeform_preview=build_freeform_topic_label([]) if FREEFORM_TOPIC_SLOT_ENABLED else None,
        **notice_ctx,
    )

@bp.route("/topics/toggle", methods=["POST"])
def topics_toggle():
    selected_csv = request.form.get("selected", "")
    current_slugs = parse_selected(selected_csv)
    toggled_slug = canonicalize_slug(request.form.get("toggle"))
    search_queries_text = request.form.get("search_queries", "")
    search_results = parse_search_results(request.form.get("search_results_json"))
    searched_query = request.form.get("searched_query", "")

    if toggled_slug not in TOPIC_SLUGS:
        return render_topics_fragment(
            selected=current_slugs,
            job_id=None,
            error=None,
            search_queries_text=search_queries_text,
            proposals=[],
            proposal_version=0,
            topic_scope="static_manual",
            search_results=search_results,
            searched_query=searched_query,
        )

    updated_selected = current_slugs.copy()
    if toggled_slug in updated_selected:
        updated_selected.remove(toggled_slug)
    else:
        updated_selected.append(toggled_slug)

    return render_topics_fragment(
        selected=updated_selected,
        job_id=None,
        error=None,
        search_queries_text=search_queries_text,
        proposals=[],
        proposal_version=0,
        topic_scope="static_manual",
        search_results=search_results,
        searched_query=searched_query,
    )


@bp.route("/topics/search", methods=["POST"])
def topics_search():
    query_text = normalize_query_text(request.form.get("search_query"))
    search_queries = [query_text] if query_text else []
    search_results: list[dict[str, str]] = []

    if not query_text:
        notice_ctx = build_notice(None)
        return render_template(
            "home.html",
            topics=TOPICS,
            selected=[],
            job_id=None,
            status={},
            progress=0,
            eta_by_slug={},
            eta_range=None,
            ready=False,
            error="Enter a topic before searching.",
            review_confirmed=False,
            search_queries_text="",
            search_results=[],
            search_results_json="[]",
            search_error=None,
            searched_query="",
            dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
            freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
            proposals=[],
            proposal_version=0,
            proposed_topics_json="[]",
            planner_warning=None,
            topic_scope="search_auto_single",
            allow_manual_fallback=False,
            freeform_preview=build_freeform_topic_label([]) if FREEFORM_TOPIC_SLOT_ENABLED else None,
            **notice_ctx,
        )

    try:
        search_results = serper_google_search(query_text, top_k=5)
        record_search_event(
            query_text=query_text,
            event_type="search",
            outcome="ok",
            result_count=len(search_results),
            results=search_results,
            metadata={"source": "topics_search_bar_auto_run"},
        )
    except Exception as exc:
        record_search_event(
            query_text=query_text,
            event_type="search",
            outcome="error",
            result_count=0,
            results=[],
            metadata={"source": "topics_search_bar_auto_run", "error": str(exc)},
        )
        notice_ctx = build_notice(None)
        return render_template(
            "home.html",
            topics=TOPICS,
            selected=[],
            job_id=None,
            status={},
            progress=0,
            eta_by_slug={},
            eta_range=None,
            ready=False,
            error=f"Search unavailable right now: {exc}",
            review_confirmed=False,
            search_queries_text=query_text,
            search_results=[],
            search_results_json="[]",
            search_error=None,
            searched_query=query_text,
            dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
            freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
            proposals=[],
            proposal_version=0,
            proposed_topics_json="[]",
            planner_warning=None,
            topic_scope="search_auto_single",
            allow_manual_fallback=False,
            freeform_preview=build_freeform_topic_label(search_queries) if FREEFORM_TOPIC_SLOT_ENABLED else None,
            **notice_ctx,
        )

    approved_topics, topic_scope, query_scope = build_search_topic_plan(query_text, search_queries)
    run_selected_slugs = [str(item.get("slug")) for item in approved_topics if isinstance(item, dict) and item.get("slug")]
    if not run_selected_slugs:
        notice_ctx = build_notice(None)
        return render_template(
            "home.html",
            topics=TOPICS,
            selected=[],
            job_id=None,
            status={},
            progress=0,
            eta_by_slug={},
            eta_range=None,
            ready=False,
            error="Unable to build a run plan from this topic. Try a more specific query.",
            review_confirmed=False,
            search_queries_text=query_text,
            search_results=[],
            search_results_json="[]",
            search_error=None,
            searched_query=query_text,
            dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
            freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
            proposals=[],
            proposal_version=0,
            proposed_topics_json="[]",
            planner_warning=None,
            topic_scope="search_auto_single",
            allow_manual_fallback=False,
            freeform_preview=build_freeform_topic_label(search_queries) if FREEFORM_TOPIC_SLOT_ENABLED else None,
            **notice_ctx,
        )

    job_id = start_generation_job(
        run_selected_slugs=run_selected_slugs,
        search_queries=search_queries,
        search_results=search_results,
        proposed_topics=[],
        approved_topics=approved_topics,
        topic_scope=topic_scope,
        run_config_updates={
            "topic_scope": topic_scope,
            "approved_topic_count": len(approved_topics),
            "query_scope": query_scope,
            "entrypoint": "search_auto_run",
        },
    )

    job = JOBS[job_id]
    notice_ctx = build_notice(job)
    eta_by_slug, eta_range = compute_eta(job["status"])
    return render_template(
        "home.html",
        topics=TOPICS,
        selected=[],
        job_id=job_id,
        status=job["status"],
        progress=percent_complete(job["status"]),
        eta_by_slug=eta_by_slug,
        eta_range=eta_range,
        ready=False,
        error=None,
        review_confirmed=False,
        search_queries_text=query_text,
        search_results=[],
        search_results_json="[]",
        search_error=None,
        searched_query=query_text,
        dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
        freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
        proposals=[],
        proposal_version=0,
        proposed_topics_json="[]",
        planner_warning=None,
        topic_scope=topic_scope,
        allow_manual_fallback=False,
        freeform_preview=build_freeform_topic_label(search_queries) if FREEFORM_TOPIC_SLOT_ENABLED else None,
        **notice_ctx,
    )


@bp.route("/topics/propose", methods=["POST"])
def topics_propose():
    selected_csv = request.form.get("selected", "")
    selected_slugs = parse_selected(selected_csv)
    search_queries_text = request.form.get("search_queries", "")
    search_queries = parse_search_queries(search_queries_text)
    search_results = parse_search_results(request.form.get("search_results_json"))
    searched_query = request.form.get("searched_query", "")

    if not DYNAMIC_TOPICS_ENABLED:
        return render_topics_fragment(
            selected=selected_slugs,
            job_id=None,
            error="Dynamic topic proposals are disabled. Using manual selection.",
            search_queries_text=search_queries_text,
            proposals=[],
            proposal_version=0,
            topic_scope="static_manual",
            search_results=search_results,
            searched_query=searched_query,
        )

    scope_slugs = selected_slugs if selected_slugs else [t["slug"] for t in TOPICS]
    catalog = searchable_catalog(scope_slugs)
    if not catalog:
        return render_topics_fragment(
            selected=selected_slugs,
            job_id=None,
            error="No template topics available for proposal. Select at least one template topic.",
            search_queries_text=search_queries_text,
            proposals=[],
            proposal_version=0,
            topic_scope="dynamic_hybrid",
            search_results=search_results,
            searched_query=searched_query,
        )

    try:
        proposals, planner_warning = propose_topics(
            catalog,
            search_queries,
            include_freeform=FREEFORM_TOPIC_SLOT_ENABLED,
            max_template_proposals=MAX_TEMPLATE_PROPOSALS,
        )
        record_proposal_impressions(
            proposed_topics=proposals,
            metadata={
                "source": "topics_propose",
                "search_query_count": len(search_queries),
                "search_result_count": len(search_results),
            },
        )
        proposal_version = int(time.time())
        topic_scope = "dynamic_hybrid" if FREEFORM_TOPIC_SLOT_ENABLED else "dynamic_templates"
        return render_topics_fragment(
            selected=selected_slugs,
            job_id=None,
            error=None,
            search_queries_text=search_queries_text,
            proposals=proposals,
            proposal_version=proposal_version,
            planner_warning=planner_warning,
            topic_scope=topic_scope,
            search_results=search_results,
            searched_query=searched_query,
        )
    except Exception:
        return render_topics_fragment(
            selected=selected_slugs,
            job_id=None,
            error=(
                "Topic proposal service is temporarily unavailable. "
                "Falling back to manual selection for this run."
            ),
            search_queries_text=search_queries_text,
            proposals=[],
            proposal_version=0,
            topic_scope="static_manual",
            allow_manual_fallback=True,
            search_results=search_results,
            searched_query=searched_query,
        )


@bp.route("/run", methods=["POST"])
def run():
    raw_list = request.form.getlist("selected")
    selected_slugs = [canonicalize_slug(s) for s in raw_list] or parse_selected(request.form.get("selected_csv", ""))
    selected_slugs = [s for s in selected_slugs if s in TOPIC_SLUGS]
    search_queries_text = request.form.get("search_queries", "")
    search_queries = parse_search_queries(search_queries_text)
    search_results = parse_search_results(request.form.get("search_results_json"))
    searched_query = request.form.get("searched_query", "")
    topic_scope = request.form.get("topic_scope", "static_manual")
    proposed_topics = parse_json_list(request.form.get("proposed_topics_json"))
    proposed_topics_version = safe_int(request.form.get("proposed_topics_version"), 0)

    approved_topics = parse_json_list(request.form.get("approved_topics_json"))
    approved_slugs = [canonicalize_slug(s) for s in request.form.getlist("approved_topic")]
    if not approved_topics and approved_slugs and proposed_topics:
        proposed_by_slug = {
            canonicalize_slug(str(item.get("slug"))): item
            for item in proposed_topics
            if isinstance(item, dict)
        }
        approved_topics = [proposed_by_slug[s] for s in approved_slugs if s in proposed_by_slug]

    run_selected_slugs = selected_slugs
    dynamic_scope_active = DYNAMIC_TOPICS_ENABLED and topic_scope != "static_manual"
    if dynamic_scope_active:
        if not proposed_topics or proposed_topics_version <= 0:
            return render_topics_fragment(
                selected=selected_slugs,
                job_id=None,
                error="Propose topics first, then approve at least one before running.",
                search_queries_text=search_queries_text,
                proposals=proposed_topics,
                proposal_version=proposed_topics_version,
                topic_scope=topic_scope,
                search_results=search_results,
                searched_query=searched_query,
            )

        if not approved_topics:
            return render_topics_fragment(
                selected=selected_slugs,
                job_id=None,
                error="Approve at least one proposed topic before running.",
                search_queries_text=search_queries_text,
                proposals=proposed_topics,
                proposal_version=proposed_topics_version,
                topic_scope=topic_scope,
                search_results=search_results,
                searched_query=searched_query,
            )

        proposed_by_slug = {
            canonicalize_slug(str(item.get("slug"))): item
            for item in proposed_topics
            if isinstance(item, dict)
        }
        validated_approved = []
        for item in approved_topics:
            if not isinstance(item, dict):
                continue
            slug = canonicalize_slug(str(item.get("slug")))
            source = proposed_by_slug.get(slug)
            if not source:
                continue
            proposal_type = str(source.get("type", "dynamic_template")).strip().lower()
            base_slug = canonicalize_slug(
                str(source.get("base_slug") or source.get("anchor_slug") or slug.split("__", 1)[0])
            )
            if proposal_type == "freeform":
                if not FREEFORM_TOPIC_SLOT_ENABLED:
                    continue
            elif proposal_type == "template":
                if slug not in TOPIC_SLUGS:
                    continue
                base_slug = slug
            elif proposal_type in {"dynamic_template", "template_variant"}:
                if base_slug not in TOPIC_SLUGS:
                    continue
            else:
                continue
            anchor = TOPIC_BY_SLUG.get(base_slug, {})
            validated_approved.append(
                {
                    "slug": slug,
                    "type": proposal_type,
                    "label": source.get("label") or source.get("topic") or slug.replace("_", " ").title(),
                    "topic": source.get("topic") or source.get("label") or slug.replace("_", " ").title(),
                    "base_slug": base_slug,
                    "anchor_label": source.get("anchor_label") or anchor.get("label"),
                    "icon": source.get("icon") or anchor.get("icon", "*"),
                    "rationale": source.get("rationale"),
                    "confidence": source.get("confidence"),
                    "system_suggested": bool(source.get("system_suggested", True)),
                }
            )
        approved_topics = validated_approved
        run_selected_slugs = [item["slug"] for item in approved_topics]
        if not run_selected_slugs:
            return render_topics_fragment(
                selected=selected_slugs,
                job_id=None,
                error="No valid approved topics found. Re-propose topics and try again.",
                search_queries_text=search_queries_text,
                proposals=proposed_topics,
                proposal_version=proposed_topics_version,
                topic_scope=topic_scope,
                search_results=search_results,
                searched_query=searched_query,
            )
    else:
        proposed_topics = []
        proposed_topics_version = 0
        approved_topics = [
            {
                "slug": slug,
                "type": "template",
                "label": TOPIC_BY_SLUG[slug]["label"],
                "topic": TOPIC_DEF_BY_SLUG.get(slug, {}).get("topic", TOPIC_BY_SLUG[slug]["label"]),
                "base_slug": slug,
                "anchor_label": TOPIC_BY_SLUG[slug]["label"],
                "icon": TOPIC_BY_SLUG[slug]["icon"],
                "system_suggested": False,
            }
            for slug in selected_slugs
        ]

    if not run_selected_slugs:
        return render_topics_fragment(
            selected=selected_slugs,
            job_id=None,
            error="Select at least one section.",
            search_queries_text=search_queries_text,
            proposals=proposed_topics,
            proposal_version=proposed_topics_version,
            topic_scope=topic_scope,
            search_results=search_results,
            searched_query=searched_query,
        )

    job_id = start_generation_job(
        run_selected_slugs=run_selected_slugs,
        search_queries=search_queries,
        search_results=search_results,
        proposed_topics=proposed_topics,
        approved_topics=approved_topics,
        topic_scope=topic_scope,
        run_config_updates={
            "proposed_topics_version": proposed_topics_version,
            "topic_scope": topic_scope,
            "approved_topic_count": len(approved_topics),
        },
    )

    job = JOBS[job_id]
    notice_ctx = build_notice(job)
    eta_by_slug, eta_range = compute_eta(job["status"])
    return render_template(
        "home.html",
        topics=TOPICS,
        selected=[s for s in run_selected_slugs if s in TOPIC_SLUGS],
        job_id=job_id,
        status=job["status"],
        progress=percent_complete(job["status"]),
        eta_by_slug=eta_by_slug,
        eta_range=eta_range,
        ready=False,
        error=None,
        review_confirmed=False,
        search_queries_text=search_queries_text,
        search_results=[],
        search_results_json="[]",
        search_error=None,
        searched_query=(search_queries[0] if search_queries else ""),
        dynamic_topics_enabled=DYNAMIC_TOPICS_ENABLED,
        freeform_topic_slot_enabled=FREEFORM_TOPIC_SLOT_ENABLED,
        proposals=proposed_topics,
        proposal_version=proposed_topics_version,
        proposed_topics_json=json.dumps(proposed_topics),
        planner_warning=None,
        topic_scope=topic_scope,
        allow_manual_fallback=False,
        freeform_preview=build_freeform_topic_label(search_queries) if FREEFORM_TOPIC_SLOT_ENABLED else None,
        **notice_ctx,
    )


@bp.route("/status/<job_id>")
def status_fragment(job_id):
    job = get_job_or_404(job_id)
    maybe_abort_stalled_job(job)
    status = job["status"]
    ready = job_is_ready(job)
    progress = percent_complete(status)
    eta_by_slug, eta_range = compute_eta(status)
    topics_selected = selected_topic_cards(job)
    notice_ctx = build_notice(job)
    return render_template(
        "fragments/status.html",
        topics=topics_selected,
        status=status,
        progress=progress,
        eta_by_slug=eta_by_slug,
        eta_range=eta_range,
        ready=ready,
        job_id=job_id,
        error=job["error"],
        search_queries=job.get("search_queries", []),
        search_queries_text=", ".join(job.get("search_queries", [])),
        notice_oob=True,
        **notice_ctx,
    )


@bp.route("/review/<job_id>")
def review_fragment(job_id):
    ctx = build_review_context(job_id)
    return render_template("fragments/review.html", **ctx)


@bp.route("/review/toggle/<job_id>", methods=["POST"])
def review_toggle(job_id):
    job = get_job_or_404(job_id)
    job["review_confirmed"] = request.form.get("confirm") == "on"
    save_job(job)
    return review_fragment(job_id)


@bp.route("/review/approve/<job_id>", methods=["POST"])
def review_approve(job_id):
    job = get_job_or_404(job_id)
    # Placeholder side effect: mark approved; hook real send/publish here using job["output_path"]
    job["approved"] = True
    job["review_confirmed"] = False
    job["email_error"] = None
    save_job(job)
    ctx = build_review_context(job_id)
    ctx["notice_oob"] = True
    review_html = render_template("fragments/review_section.html", **ctx)
    email_html = render_template("fragments/email_section.html", **{**ctx, "oob": True})
    return review_html + email_html


@bp.route("/email/<job_id>", methods=["POST"])
def email_send(job_id):
    job = get_job_or_404(job_id)
    ready = job_is_ready(job)
    if not ready or not job.get("approved"):
        job["email_error"] = "Draft is not ready or not approved."
        save_job(job)
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    group = request.form.get("group", job.get("email_group", "all"))
    extra = request.form.get("extra_emails", job.get("email_extra", ""))
    subject = normalize_subject(request.form.get("subject", job.get("email_subject")))
    action = request.form.get("action", "send")
    confirm_send = request.form.get("confirm_send") == "on"
    allow_duplicate = request.form.get("allow_duplicate") == "on"

    resolved_recipients = resolve_recipients(group, extra)
    recipients = resolved_recipients

    job["email_group"] = group
    job["email_extra"] = extra
    job["email_subject"] = subject
    job["email_error"] = None

    try:
        html = Path(job["output_path"]).read_text(encoding="utf-8")
    except Exception as e:
        job["email_error"] = f"Unable to load draft HTML: {e}"
        save_job(job)
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    content_hash = hash_html(html)
    duplicate = find_duplicate_send(content_hash, recipients)
    job["email_preview"] = {
        "subject": subject,
        "recipients": recipients,
        "recipient_count": len(recipients),
        "content_hash": content_hash,
        "duplicate": duplicate,
        "group": group,
        "extra": extra,
        "confirm_send": confirm_send,
        "allow_duplicate": allow_duplicate,
        "timestamp": now_timestamp(),
    }

    if action == "preview":
        job["email_sending"] = False
        save_job(job)
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    if not recipients:
        job["email_error"] = "No recipients selected."
        job["email_sending"] = False
        add_send_history(
            job,
            "blocked",
            recipients,
            subject,
            error=job["email_error"],
            content_hash=content_hash,
        )
        record_delivery_feedback(
            job_id=job_id,
            episode_id=job.get("episode_id"),
            approved_topics=job.get("approved_topics"),
            delivery_status="blocked",
        )
        record_search_context_feedback(
            search_queries=job.get("search_queries"),
            job_id=job_id,
            episode_id=job.get("episode_id"),
            event_type="delivery",
            outcome="send_blocked",
            metadata={"reason": "no_recipients"},
        )
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    if not confirm_send:
        job["email_error"] = "Please confirm recipients before sending."
        job["email_sending"] = False
        add_send_history(
            job,
            "blocked",
            recipients,
            subject,
            error=job["email_error"],
            content_hash=content_hash,
        )
        record_delivery_feedback(
            job_id=job_id,
            episode_id=job.get("episode_id"),
            approved_topics=job.get("approved_topics"),
            delivery_status="blocked",
        )
        record_search_context_feedback(
            search_queries=job.get("search_queries"),
            job_id=job_id,
            episode_id=job.get("episode_id"),
            event_type="delivery",
            outcome="send_blocked",
            metadata={"reason": "confirm_missing"},
        )
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    if duplicate and not allow_duplicate:
        job["email_error"] = "Duplicate content detected. Review and allow duplicate to send."
        job["email_sending"] = False
        add_send_history(
            job,
            "blocked",
            recipients,
            subject,
            error=job["email_error"],
            content_hash=content_hash,
        )
        record_delivery_feedback(
            job_id=job_id,
            episode_id=job.get("episode_id"),
            approved_topics=job.get("approved_topics"),
            delivery_status="blocked",
        )
        record_search_context_feedback(
            search_queries=job.get("search_queries"),
            job_id=job_id,
            episode_id=job.get("episode_id"),
            event_type="delivery",
            outcome="send_blocked",
            metadata={"reason": "duplicate_blocked"},
        )
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    job["email_sending"] = True
    save_job(job)

    try:
        send_newsletter_email(recipients, subject, html)
        job["email_sent"] = True
        job["email_sent_to"] = recipients
        add_send_history(
            job,
            "success",
            recipients,
            subject,
            content_hash=content_hash,
        )
        record_delivery_feedback(
            job_id=job_id,
            episode_id=job.get("episode_id"),
            approved_topics=job.get("approved_topics"),
            delivery_status="success",
        )
        record_search_context_feedback(
            search_queries=job.get("search_queries"),
            job_id=job_id,
            episode_id=job.get("episode_id"),
            event_type="delivery",
            outcome="sent",
            metadata={"recipient_count": len(recipients)},
        )
    except Exception as e:
        job["email_error"] = str(e)
        add_send_history(
            job,
            "failed",
            recipients,
            subject,
            error=job["email_error"],
            content_hash=content_hash,
        )
        record_delivery_feedback(
            job_id=job_id,
            episode_id=job.get("episode_id"),
            approved_topics=job.get("approved_topics"),
            delivery_status="failed",
        )
        record_search_context_feedback(
            search_queries=job.get("search_queries"),
            job_id=job_id,
            episode_id=job.get("episode_id"),
            event_type="delivery",
            outcome="send_failed",
            metadata={"error": str(e)},
        )
    finally:
        job["email_sending"] = False
        save_job(job)

    ctx = build_review_context(job_id, include_preview=False)
    ctx["notice_oob"] = True
    return render_template("fragments/email_section.html", **ctx)


@bp.route("/download/<job_id>")
def download_newsletter(job_id):
    job = get_job_or_404(job_id)
    path = job.get("output_path")
    if not path or not Path(path).exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=Path(path).name)
