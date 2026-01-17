from flask import Blueprint, render_template, request, send_file, abort, url_for
import json
import time
from pathlib import Path
import threading
import uuid

from research_crew.app import generate_newsletter, send_newsletter_email

bp = Blueprint("web", __name__)

TOPICS = [
    {"slug": "ai_at_work", "label": "AI at Work", "icon": "\U0001F916"},
    {"slug": "it_hacks", "label": "IT Hacks", "icon": "\U0001F527"},
    {"slug": "o365_updates", "label": "O365 Updates", "icon": "\u2601"},
    {"slug": "tech_discovery", "label": "Tech Discovery", "icon": "\U0001F50D"},
    {"slug": "tech_trends", "label": "Tech Trends", "icon": "\U0001F680"},
]
TOPIC_SLUGS = {t["slug"] for t in TOPICS}
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


def canonicalize_slug(slug: str | None) -> str:
    """Normalize slugs so UI inputs (hyphen) and backend (underscore) stay aligned."""
    return (slug or "").replace("-", "_")


def parse_selected(selected_csv: str):
    parts = (selected_csv or "").split(",")
    normalized = [canonicalize_slug(p) for p in parts]
    return [slug for slug in normalized if slug in TOPIC_SLUGS]


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


def now_timestamp():
    return time.strftime("%Y-%m-%d %H:%M")

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


def percent_complete(status):
    if not status:
        return 0
    now = time.time()
    stats = runtime_snapshot()
    per_topic = []
    for slug, entry in status.items():
        durations = stats.get(slug, [])
        expected = sum(durations) / len(durations) if durations else None
        per_topic.append(topic_progress(entry, now, expected))
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
        warn_errors = {"No recipients selected.", "Draft is not ready or not approved."}
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


def next_send_label(history):
    return "Send Email" if not history else "Resend Email"


def next_send_hint(history):
    return "First send" if not history else "Resend"


def add_send_history(job, status, recipients, subject, error=None):
    history = job.setdefault("email_history", [])
    attempt = len(history) + 1
    entry = {
        "attempt": attempt,
        "label": "First send" if attempt == 1 else "Resend",
        "status": status,
        "timestamp": now_timestamp(),
        "recipient_count": len(recipients or []),
        "recipients": recipients or [],
        "subject": subject,
        "error": error,
    }
    history.append(entry)
    return entry


def init_job(selected_slugs):
    job_id = uuid.uuid4().hex
    status = {slug: {"state": "queued", "message": "Queued", "started_at": None} for slug in selected_slugs}
    JOBS[job_id] = {
        "status": status,
        "html_ready": False,
        "review_confirmed": False,
        "approved": False,
        "error": None,
        "output_path": None,
        "selected": selected_slugs,
        "email_sent": False,
        "email_error": None,
        "email_sending": False,
        "email_sent_to": [],
        "email_group": "all",
        "email_extra": "",
        "email_history": [],
    }
    return job_id


def run_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return
    selected = job["selected"]

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

    def log_cb(msg):
        # TODO: store logs per job if needed
        pass

    try:
        html, outp, _ = generate_newsletter(selected_slugs=selected, cb=log_cb, status_cb=status_cb)
        job["html_ready"] = True
        job["output_path"] = Path(outp)
        for slug, st in job["status"].items():
            if st["state"] != "failed":
                st["state"] = "done"
                st["message"] = "Finished."
    except Exception as e:
        job["error"] = str(e)
        for st in job["status"].values():
            if st["state"] in {"queued", "active", "waiting"}:
                st["state"] = "failed"
                st["message"] = f"Failed: {e}"


def get_job_or_404(job_id):
    job = JOBS.get(job_id)
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

    topics_selected = [t for t in TOPICS if t["slug"] in job["selected"]]
    notice_ctx = build_notice(job)
    email_history = job.get("email_history", [])
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
        "email_groups": EMAIL_GROUPS,
        "email_group": job.get("email_group", "all"),
        "email_extra": job.get("email_extra", ""),
        "email_sent": job.get("email_sent", False),
        "email_sent_to": job.get("email_sent_to", []),
        "email_error": job.get("email_error"),
        "email_sending": job.get("email_sending", False),
        "email_subject": job.get("email_subject", default_subject()),
        "resolved_recipients": resolved_recipients,
        "preview_snippet": preview_snippet,
        "download_url": download_url,
        "preview_url": preview_url,
        "oob": False,
        "email_history": email_history,
        "next_send_label": next_label,
        "next_send_hint": next_hint,
        **notice_ctx,
    }


@bp.route("/")
def home():
    notice_ctx = build_notice(None)
    return render_template(
        "home.html",
        topics=TOPICS,
        selected=[],
        job_id=None,
        status={},
        progress=0,
        ready=False,
        error=None,
        review_confirmed=False,
        **notice_ctx,
    )


@bp.route("/topics/toggle", methods=["POST"])
def topics_toggle():
    selected_csv = request.form.get("selected", "")
    current_slugs = parse_selected(selected_csv)
    toggled_slug = canonicalize_slug(request.form.get("toggle"))

    if toggled_slug not in TOPIC_SLUGS:
        return render_template("fragments/topics.html", topics=TOPICS, selected=current_slugs, job_id=None, error=None)

    updated_selected = current_slugs.copy()
    if toggled_slug in updated_selected:
        updated_selected.remove(toggled_slug)
    else:
        updated_selected.append(toggled_slug)

    return render_template("fragments/topics.html", topics=TOPICS, selected=updated_selected, job_id=None, error=None)


@bp.route("/run", methods=["POST"])
def run():
    raw_list = request.form.getlist("selected")
    selected_slugs = [canonicalize_slug(s) for s in raw_list] or parse_selected(request.form.get("selected_csv", ""))
    selected_slugs = [s for s in selected_slugs if s in TOPIC_SLUGS]
    if not selected_slugs:
        return render_template("fragments/topics.html", topics=TOPICS, selected=[], job_id=None, error="Select at least one section.")

    job_id = init_job(selected_slugs)
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()

    job = JOBS[job_id]
    notice_ctx = build_notice(job)
    return render_template(
        "home.html",
        topics=TOPICS,
        selected=selected_slugs,
        job_id=job_id,
        status=job["status"],
        progress=percent_complete(job["status"]),
        ready=False,
        error=None,
        review_confirmed=False,
        **notice_ctx,
    )


@bp.route("/status/<job_id>")
def status_fragment(job_id):
    job = get_job_or_404(job_id)
    status = job["status"]
    ready = job_is_ready(job)
    progress = percent_complete(status)
    topics_selected = [t for t in TOPICS if t["slug"] in job["selected"]]
    notice_ctx = build_notice(job)
    return render_template(
        "fragments/status.html",
        topics=topics_selected,
        status=status,
        progress=progress,
        ready=ready,
        job_id=job_id,
        error=job["error"],
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
    return review_fragment(job_id)


@bp.route("/review/approve/<job_id>", methods=["POST"])
def review_approve(job_id):
    job = get_job_or_404(job_id)
    # Placeholder side effect: mark approved; hook real send/publish here using job["output_path"]
    job["approved"] = True
    job["review_confirmed"] = False
    job["email_error"] = None
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
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    group = request.form.get("group", job.get("email_group", "all"))
    extra = request.form.get("extra_emails", job.get("email_extra", ""))
    subject = request.form.get("subject", job.get("email_subject", default_subject())).strip() or default_subject()

    recipients = resolve_recipients(group, extra)
    job["email_group"] = group
    job["email_extra"] = extra
    job["email_subject"] = subject
    job["email_sending"] = True
    job["email_error"] = None
    job["email_sent"] = False
    job["email_sent_to"] = []

    if not recipients:
        job["email_error"] = "No recipients selected."
        job["email_sending"] = False
        add_send_history(job, "blocked", recipients, subject, error=job["email_error"])
        ctx = build_review_context(job_id, include_preview=False)
        ctx["notice_oob"] = True
        return render_template("fragments/email_section.html", **ctx)

    try:
        html = Path(job["output_path"]).read_text(encoding="utf-8")
        send_newsletter_email(recipients, subject, html)
        job["email_sent"] = True
        job["email_sent_to"] = recipients
        add_send_history(job, "success", recipients, subject)
    except Exception as e:
        job["email_error"] = str(e)
        add_send_history(job, "failed", recipients, subject, error=job["email_error"])
    finally:
        job["email_sending"] = False

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
