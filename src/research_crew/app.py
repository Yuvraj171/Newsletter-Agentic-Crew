#!/usr/bin/env python
import sys, pathlib, os, datetime, re

# Ensure the package is importable when running Streamlit from various working directories
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv()
import streamlit as st

from research_crew.crew import ResearchCrew
from research_crew.email_agent import create_email_agent
from research_crew.email_task import build_email_task
from research_crew.main import inject_topic_only, topic_definitions


def _month():
    return datetime.date.today().strftime("%B")


def apply_ui_theme():
    """Inline CSS so the app feels like a cohesive product instead of defaults."""
    st.markdown(
        """
        <style>
        :root {
            --bg: #f6f7fb;
            --panel: #ffffff;
            --ink: #0f1b2d;
            --muted: #5b6472;
            --teal: #0f5b8a;
            --teal-ghost: rgba(15,91,138,0.08);
            --amber: #f4a261;
            --success: #1da07a;
            --error: #d64545;
            --border: #e2e6ef;
            --shadow: 0 8px 24px rgba(15, 91, 138, 0.08);
        }
        html, body, [class*="block-container"] {
            font-family: 'Space Grotesk', 'Inter', system-ui, -apple-system, sans-serif;
            background: var(--bg);
            color: var(--ink);
        }
        .rc-hero {
            background: linear-gradient(120deg, #0f5b8a 0%, #0d3f62 50%, #0f5b8a 100%);
            color: #fff;
            padding: 18px 20px;
            border-radius: 14px;
            box-shadow: var(--shadow);
            border: 1px solid rgba(255,255,255,0.08);
        }
        .rc-hero h1 {
            margin: 0 0 6px;
            font-size: 26px;
            letter-spacing: -0.02em;
        }
        .rc-hero p {
            margin: 0;
            opacity: 0.9;
            font-size: 14px;
        }
        .rc-card {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px 14px 10px;
            box-shadow: var(--shadow);
            margin-bottom: 12px;
        }
        .rc-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
        }
        .rc-badge {
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 24px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--muted);
            text-transform: capitalize;
        }
        .rc-state-running { background: var(--teal-ghost); color: var(--teal); border-color: rgba(15,91,138,0.18); }
        .rc-state-done { background: rgba(29,160,122,0.1); color: var(--success); border-color: rgba(29,160,122,0.2); }
        .rc-state-missing, .rc-state-error { background: rgba(214,69,69,0.1); color: var(--error); border-color: rgba(214,69,69,0.2); }
        .rc-state-queued { background: rgba(244,162,97,0.12); color: var(--amber); border-color: rgba(244,162,97,0.3); }
        .rc-state-idle { background: var(--bg); color: var(--muted); border-color: var(--border); }
        .rc-card small { color: var(--muted); }
        .rc-log {
            font-family: 'JetBrains Mono', 'IBM Plex Mono', Consolas, monospace;
            font-size: 12px;
            background: #0f172a;
            color: #e2e8f0;
            padding: 12px;
            border-radius: 10px;
            border: 1px solid #1f2937;
            max-height: 240px;
            overflow-y: auto;
        }
        .rc-preview-frame {
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: var(--shadow);
        }
        .rc-section-title {
            margin: 12px 0 4px;
            font-weight: 600;
        }
        .stMultiSelect, .stTextInput, .stTextArea {
            border-radius: 10px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    # Load font from Google (falls back to system if unreachable)
    st.markdown(
        "<link href='https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600&display=swap' rel='stylesheet'>",
        unsafe_allow_html=True,
    )


def init_state():
    st.session_state.setdefault("newsletter_html", None)
    st.session_state.setdefault("generated_files", {})
    st.session_state.setdefault("run_log", [])
    st.session_state.setdefault("topic_status", {})
    st.session_state.setdefault("last_error", None)


def log_message(msg: str):
    st.session_state["run_log"].append(msg)
    # keep log reasonable
    st.session_state["run_log"] = st.session_state["run_log"][-300:]


def update_status(slug: str, state: str, detail: str = ""):
    st.session_state["topic_status"][slug] = {"state": state, "detail": detail}


def summarize_html(html: str):
    text = re.sub(r"<[^>]+>", " ", html)
    words = len([w for w in text.split() if w.strip()])
    headings = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, flags=re.IGNORECASE)
    headings = [re.sub(r"<[^>]+>", "", h).strip() for h in headings]
    headings = [h for h in headings if h]
    return words, headings


def generate_newsletter(selected_slugs=None, cb=None, status_cb=None):
    os.makedirs("outputs", exist_ok=True)
    gen = {}
    month = _month()

    for d in topic_definitions:
        tg, slug = d["topic"], d["topic_slug"]
        if selected_slugs and slug not in selected_slugs:
            continue
        if status_cb:
            status_cb(slug, "running", f"Generating {tg}")
        if cb:
            cb(f"Generating: {tg}")
        inputs = {
            "topic": tg,
            "topic_slug": slug,
            "output_path": f"{slug}_{month}",
            "research_brief": d["research_brief"].format(topic=tg),
            "research_output_schema": inject_topic_only(d["research_output_schema"], tg),
            "writer_brief": d["writer_brief"].format(topic=tg),
            "writer_output_schema": d["writer_output_schema"],
            "editor_brief": d["editor_brief"],
        }
        ResearchCrew().crew().kickoff(inputs=inputs)
        gen[tg] = f"{slug}_{month}.html"
        if status_cb:
            status_cb(slug, "done", f"Generated outputs/{slug}_{month}.html")

    if cb:
        cb("Consolidating...")

    secs = []
    for d in topic_definitions:
        tg, slug = d["topic"], d["topic_slug"]
        if selected_slugs and slug not in selected_slugs:
            continue
        fpath = f"outputs/{slug}_{month}.html"
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                html = f.read()
            m = re.search(r"<body[^>]*>([\s\S]*?)</body>", html, flags=re.IGNORECASE)
            body = m.group(1).strip() if m else html
            secs.append(f"<hr><h2>{tg}</h2>" + body)
        except FileNotFoundError:
            if cb:
                cb(f"Missing: {fpath}")
            if status_cb:
                status_cb(slug, "missing", "File not found during consolidation")

    title = f"BEST Group Tech Newsletter - {month}"
    body_intro = "Here are this month's highlights across all topics."
    email_html = (
        f"<!doctype html>"
        f"<html>"
        f"<head>"
        f"<meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width'>"
        f"<title>{title}</title>"
        f"<style>"
        f"body{{margin:0;padding:16px;font-family:Arial,Helvetica,sans-serif}}"
        f"h1{{font-size:22px;margin:0 0 12px}}"
        f"p,li{{font-size:14px;line-height:1.5}}"
        f"a{{color:#0b62d6}}"
        f"table{{border-collapse:collapse;width:100%}}"
        f"th,td{{border:1px solid #ddd;padding:6px;font-size:14px}}"
        f"</style>"
        f"</head>"
        f"<body>"
        f"<h1>{title}</h1>"
        f"<p>{body_intro}</p>"
        f"{''.join(secs)}"
        f"</body>"
        f"</html>"
    )
    outp = f"outputs/newsletter_email_{month}.html"
    with open(outp, "w", encoding="utf-8") as f:
        f.write(email_html)
    return email_html, outp, gen


def send_newsletter_email(recipients, subject, html):
    from crewai import Crew

    agent = create_email_agent()
    task = build_email_task(agent, recipients, subject, html)
    Crew(agents=[agent], tasks=[task], process="sequential", verbose=True).kickoff()


def main():
    st.set_page_config(page_title="BEST Group Newsletter", layout="wide")
    apply_ui_theme()
    init_state()

    month = _month()
    st.markdown(
        f"""
        <div class="rc-hero">
            <h1>Tech Newsletter</h1>
            <p>Generate topic pages, consolidate, and send the monthly update - {month}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    pairs = [(d["topic"], d["topic_slug"]) for d in topic_definitions]
    labels = [l for l, _ in pairs]
    default_subject = f"BEST Group Tech Newsletter - {month}"

    c_left, c_right = st.columns([1.8, 1], gap="large")

    with c_left:
        st.subheader("Topics & Generation")
        sel_labels = st.multiselect("Topics to include", labels, default=labels)
        sel_slugs = [s for l, s in pairs if l in sel_labels]

        generate_clicked = st.button("Generate newsletter", type="primary", use_container_width=True)

        if generate_clicked:
            st.session_state["newsletter_html"] = None
            st.session_state["generated_files"] = {}
            st.session_state["run_log"] = []
            st.session_state["last_error"] = None
            st.session_state["topic_status"] = {
                slug: {"state": "queued", "detail": "Queued"} for slug in sel_slugs
            }

            def cb(msg):
                log_message(msg)

            def scb(slug, state, detail=""):
                update_status(slug, state, detail)

            with st.spinner("Running agents..."):
                try:
                    html, outp, files = generate_newsletter(sel_slugs, cb=cb, status_cb=scb)
                    st.session_state["newsletter_html"] = html
                    st.session_state["generated_files"] = files
                    st.success(f"Consolidated newsletter saved to: {outp}")
                except Exception as e:
                    st.session_state["last_error"] = str(e)
                    st.error(f"Generation failed: {e}")
                    log_message(f"Generation failed: {e}")

        # Topic status cards
        for d in topic_definitions:
            slug = d["topic_slug"]
            if sel_slugs and slug not in sel_slugs:
                continue
            status = st.session_state["topic_status"].get(slug, {"state": "idle", "detail": "Waiting"})
            state = status.get("state", "idle")
            detail = status.get("detail", "")
            badge_class = f"rc-state-{state}"
            st.markdown(
                f"""
                <div class="rc-card">
                  <div class="rc-card-header">
                    <div><strong>{d["topic"]}</strong><br><small>{slug}</small></div>
                    <span class="rc-badge {badge_class}">{state}</span>
                  </div>
                  <div style="margin-top:8px; color: var(--muted); font-size:13px;">{detail}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if st.session_state.get("generated_files"):
            st.markdown("**Generated files**")
            for topic, fname in st.session_state["generated_files"].items():
                path = os.path.join("outputs", fname)
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        data = f.read()
                    st.download_button(
                        f"Download {topic}",
                        data=data,
                        file_name=fname,
                        mime="text/html",
                        use_container_width=True,
                        key=f"dl-{fname}",
                    )
                else:
                    st.info(f"Missing file for {topic}: {path}")

    with c_right:
        st.subheader("Send newsletter")
        subj = st.text_input("Subject", value=st.session_state.get("subject", default_subject))
        st.session_state["subject"] = subj
        rcpt_csv = st.text_input("Recipients (comma-separated)", value=os.getenv("GMAIL_EMAIL", ""))
        confirm = st.checkbox("I confirm the recipients", value=False)
        test = st.checkbox(
            "Send test to me only",
            value=True,
            help="Overrides recipients to your GMAIL_EMAIL from .env",
        )

        disabled = not st.session_state.get("newsletter_html") or not confirm
        rcpts = [r.strip() for r in rcpt_csv.split(",") if r.strip()]
        if test and os.getenv("GMAIL_EMAIL"):
            rcpts = [os.getenv("GMAIL_EMAIL")]

        st.markdown("**Will send to:**" if rcpts else "**Will send to:** _none yet_")
        if rcpts:
            st.markdown("\n".join([f"- {r}" for r in rcpts]))
        else:
            st.caption("Add recipients or use test mode to enable send.")

        if st.button("Send Email", type="primary", use_container_width=True, disabled=disabled):
            html = st.session_state.get("newsletter_html", "")
            if not html:
                st.error("No generated newsletter to send.")
            else:
                if not rcpts:
                    st.error("Please provide at least one recipient.")
                else:
                    with st.spinner("Sending via email agent..."):
                        try:
                            send_newsletter_email(rcpts, subj, html)
                            st.success(f"Email sent to: {', '.join(rcpts)}")
                        except Exception as e:
                            st.error(f"Failed to send: {e}")
                            log_message(f"Send failed: {e}")

        st.markdown("**Run log**")
        log_area = st.empty()
        if st.button("Clear log", use_container_width=True):
            st.session_state["run_log"] = []
        log_area.markdown(
            "<div class='rc-log'>" + "<br>".join(st.session_state["run_log"] or ["Waiting..."]) + "</div>",
            unsafe_allow_html=True,
        )

        if st.session_state.get("last_error"):
            st.error(st.session_state["last_error"])

    html = st.session_state.get("newsletter_html")
    if html:
        st.markdown("---")
        st.subheader("Preview")
        words, headings = summarize_html(html)
        st.markdown(f"**Quick summary:** {words} words / {len(headings)} headings")
        if headings:
            st.caption("Headings detected: " + " | ".join(headings[:6]))

        with st.container():
            st.markdown('<div class="rc-preview-frame">', unsafe_allow_html=True)
            st.components.v1.html(html, height=850, scrolling=True)
            st.markdown("</div>", unsafe_allow_html=True)

        st.download_button(
            "Download HTML",
            data=html,
            file_name=f"newsletter_email_{month}.html",
            mime="text/html",
            use_container_width=True,
        )
        st.text_area(
            "Copy HTML",
            value=html,
            height=180,
            help="Use Ctrl/Cmd + C to copy the rendered HTML.",
        )


if __name__ == "__main__":
    main()
