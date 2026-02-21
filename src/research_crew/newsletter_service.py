#!/usr/bin/env python
import datetime
import hashlib
import json
import os
import re
from typing import Any

from dotenv import load_dotenv

from research_crew.crew import ResearchCrew
from research_crew.email_agent import create_email_agent
from research_crew.email_task import build_email_task
from research_crew.main import inject_topic_only, topic_definitions
from research_crew.topic_planner import build_freeform_definition

load_dotenv()


def _month():
    return datetime.date.today().strftime("%B")


def _as_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _search_context_block(search_queries: list[str] | None) -> str:
    queries = [q.strip() for q in (search_queries or []) if q and q.strip()]
    if not queries:
        return ""
    lines = "\n".join(f"- {q}" for q in queries)
    return (
        "\n\nSEARCH CONTEXT (user-provided):\n"
        f"{lines}\n"
        "Use this context directly to prioritize source selection, examples, and recommendations when relevant."
    )


def _search_results_context_block(search_results: list[dict[str, Any]] | None) -> str:
    rows = []
    for item in search_results or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = str(item.get("link") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not title or not link:
            continue
        line = f"- {title} ({link})"
        if snippet:
            line = f"{line}: {snippet}"
        rows.append(line)
        if len(rows) >= 5:
            break
    if not rows:
        return ""
    joined = "\n".join(rows)
    return (
        "\n\nRECENT GOOGLE RESULTS (via Serper):\n"
        f"{joined}\n"
        "Use these results as high-priority context and cite relevant links when making factual claims."
    )


def _definition_map() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for definition in topic_definitions:
        slug = str(definition.get("topic_slug", "")).strip().lower().replace("-", "_")
        if slug:
            out[slug] = definition
    return out


def _canonical_slug(raw: str | None) -> str:
    return (raw or "").strip().lower().replace("-", "_")


def _dynamic_slug(base_slug: str, label: str) -> str:
    digest = hashlib.sha1(f"{base_slug}|{label.lower()}".encode("utf-8")).hexdigest()[:10]
    return f"{base_slug}__{digest}"


def _resolve_generation_plan(
    selected_slugs: list[str] | None,
    selected_topics: list[dict[str, Any]] | None,
    search_queries: list[str] | None,
) -> list[dict[str, Any]]:
    by_slug = _definition_map()

    if not selected_topics:
        plan: list[dict[str, Any]] = []
        for slug in selected_slugs or []:
            normalized = _canonical_slug(str(slug))
            definition = by_slug.get(normalized)
            if not definition and "__" in normalized:
                definition = by_slug.get(normalized.split("__", 1)[0])
            if definition:
                plan.append({**definition, "_selection_meta": {"slug": normalized, "type": "template"}})
        return plan

    plan = []
    for item in selected_topics:
        raw_slug = _canonical_slug(str(item.get("slug", "")))
        topic_type = str(item.get("type", "template")).strip().lower()
        if topic_type == "freeform":
            label = str(item.get("topic") or item.get("label") or "Emerging Manufacturing Innovation").strip()
            freeform_definition = build_freeform_definition(label, search_queries or [])
            # Keep the planner slug stable if provided in approved metadata.
            if raw_slug:
                freeform_definition["topic_slug"] = raw_slug
            freeform_definition["_selection_meta"] = {
                "slug": freeform_definition["topic_slug"],
                "type": "freeform",
                "topic": label,
            }
            plan.append(freeform_definition)
            continue

        if topic_type in {"dynamic_template", "template_variant"}:
            base_slug = _canonical_slug(
                str(item.get("base_slug") or item.get("anchor_slug") or raw_slug.split("__", 1)[0])
            )
            base_definition = by_slug.get(base_slug)
            if not base_definition:
                continue
            label = str(item.get("topic") or item.get("label") or base_definition["topic"]).strip()
            dynamic_slug = raw_slug or _dynamic_slug(base_slug, label)
            dynamic_definition = {
                **base_definition,
                "topic": label,
                "topic_slug": dynamic_slug,
                "_selection_meta": {
                    "slug": dynamic_slug,
                    "type": "dynamic_template",
                    "base_slug": base_slug,
                    "anchor_label": item.get("anchor_label") or base_definition.get("topic") or base_slug,
                    "topic": label,
                },
            }
            plan.append(dynamic_definition)
            continue

        definition = by_slug.get(raw_slug)
        if not definition and "__" in raw_slug:
            definition = by_slug.get(raw_slug.split("__", 1)[0])
        if definition:
            plan.append({**definition, "_selection_meta": {"slug": raw_slug, "type": "template"}})
    return plan


def generate_newsletter(
    selected_slugs=None,
    cb=None,
    status_cb=None,
    search_queries=None,
    search_results=None,
    selected_topics=None,
):
    os.makedirs("outputs", exist_ok=True)
    generated = {}
    month = _month()
    use_search_context = _as_bool_env("SEARCH_CONTEXT_IN_PROMPTS", True)
    run_plan = _resolve_generation_plan(selected_slugs, selected_topics, search_queries)
    context_block = _search_context_block(search_queries if use_search_context else [])
    search_results_block = _search_results_context_block(search_results if use_search_context else [])

    for definition in run_plan:
        topic, slug = definition["topic"], definition["topic_slug"]
        if status_cb:
            status_cb(slug, "running", f"Generating {topic}")
        if cb:
            cb(f"Generating: {topic}")

        research_brief = definition["research_brief"].format(topic=topic)
        if context_block:
            research_brief = research_brief + context_block
        if search_results_block:
            research_brief = research_brief + search_results_block

        selection_meta = definition.get("_selection_meta", {})
        inputs = {
            "topic": topic,
            "topic_slug": slug,
            "output_path": f"{slug}_{month}",
            "research_brief": research_brief,
            "research_output_schema": inject_topic_only(definition["research_output_schema"], topic),
            "writer_brief": definition["writer_brief"].format(topic=topic),
            "writer_output_schema": definition["writer_output_schema"],
            "editor_brief": definition["editor_brief"],
            "search_context": "\n".join(search_queries or []),
            "search_results_context": json.dumps(search_results or []),
            "selected_topic_metadata": json.dumps(selection_meta),
        }
        ResearchCrew().crew().kickoff(inputs=inputs)
        generated[topic] = f"{slug}_{month}.html"
        if status_cb:
            status_cb(slug, "done", f"Generated outputs/{slug}_{month}.html")

    if cb:
        cb("Consolidating...")

    sections = []
    for definition in run_plan:
        topic, slug = definition["topic"], definition["topic_slug"]
        path = f"outputs/{slug}_{month}.html"
        try:
            with open(path, "r", encoding="utf-8") as handle:
                html = handle.read()
            match = re.search(r"<body[^>]*>([\s\S]*?)</body>", html, flags=re.IGNORECASE)
            body = match.group(1).strip() if match else html
            sections.append(f"<hr><h2>{topic}</h2>" + body)
        except FileNotFoundError:
            if cb:
                cb(f"Missing: {path}")
            if status_cb:
                status_cb(slug, "missing", "File not found during consolidation")

    title = f"BEST Group Tech Newsletter - {month}"
    body_intro = "Here are this month's highlights across all topics."
    email_html = (
        "<!doctype html>"
        "<html>"
        "<head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width'>"
        f"<title>{title}</title>"
        "<style>"
        "body{margin:0;padding:16px;font-family:Arial,Helvetica,sans-serif}"
        "h1{font-size:22px;margin:0 0 12px}"
        "p,li{font-size:14px;line-height:1.5}"
        "a{color:#0b62d6}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:6px;font-size:14px}"
        "</style>"
        "</head>"
        "<body>"
        f"<h1>{title}</h1>"
        f"<p>{body_intro}</p>"
        f"{''.join(sections)}"
        "</body>"
        "</html>"
    )
    output_path = f"outputs/newsletter_email_{month}.html"
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(email_html)
    return email_html, output_path, generated


def send_newsletter_email(recipients, subject, html):
    from crewai import Crew

    agent = create_email_agent()
    task = build_email_task(agent, recipients, subject, html)
    Crew(agents=[agent], tasks=[task], process="sequential", verbose=True).kickoff()
