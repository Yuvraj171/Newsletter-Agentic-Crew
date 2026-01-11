from flask import Blueprint, render_template, request, send_file, abort, url_for
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


def percent_complete(status):
    total = len(status)
    done = sum(1 for s in status.values() if s.get("state") == "done")
    active = sum(1 for s in status.values() if s.get("state") == "active")
    return int(((done + 0.5 * active) / total) * 100) if total else 0


def all_done(status):
    return all(s.get("state") == "done" for s in status.values()) if status else False


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
            if st["started_at"] is None and state in {"active", "done"}:
                st["started_at"] = time.time()

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
    }


@bp.route("/")
def home():
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
    )


@bp.route("/status/<job_id>")
def status_fragment(job_id):
    job = get_job_or_404(job_id)
    status = job["status"]
    ready = job["html_ready"] and job["error"] is None and all_done(status)
    progress = percent_complete(status)
    topics_selected = [t for t in TOPICS if t["slug"] in job["selected"]]
    return render_template(
        "fragments/status.html",
        topics=topics_selected,
        status=status,
        progress=progress,
        ready=ready,
        job_id=job_id,
        error=job["error"],
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
    ctx = build_review_context(job_id)
    review_html = render_template("fragments/review_section.html", **ctx)
    email_html = render_template("fragments/email_section.html", **{**ctx, "oob": True})
    return review_html + email_html


@bp.route("/email/<job_id>", methods=["POST"])
def email_send(job_id):
    job = get_job_or_404(job_id)
    html_ready = job["html_ready"] and job.get("output_path") and Path(job["output_path"]).exists()
    ready = html_ready and job["error"] is None and all_done(job["status"])
    if not ready or not job.get("approved"):
        job["email_error"] = "Draft is not ready or not approved."
        ctx = build_review_context(job_id, include_preview=False)
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
        return review_fragment(job_id)

    try:
        html = Path(job["output_path"]).read_text(encoding="utf-8")
        send_newsletter_email(recipients, subject, html)
        job["email_sent"] = True
        job["email_sent_to"] = recipients
    except Exception as e:
        job["email_error"] = str(e)
    finally:
        job["email_sending"] = False

    ctx = build_review_context(job_id, include_preview=False)
    return render_template("fragments/email_section.html", **ctx)



@bp.route("/download/<job_id>")
def download_newsletter(job_id):
    job = get_job_or_404(job_id)
    path = job.get("output_path")
    if not path or not Path(path).exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=Path(path).name)
