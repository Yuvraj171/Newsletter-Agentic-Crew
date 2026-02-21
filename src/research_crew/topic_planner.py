import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from research_crew.memory.db import get_conn as get_memory_conn


DEFAULT_TEMPLATE_PROPOSAL_COUNT = 5
DEFAULT_RECENT_EPISODES = 6
DEFAULT_RECENT_DAYS = 60
MAX_LATEST_QUERY_PROPOSALS = 2

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "your",
}

_ACRONYM_MAP = {
    "ai": "AI",
    "iot": "IoT",
    "oee": "OEE",
    "erp": "ERP",
    "mes": "MES",
    "plm": "PLM",
    "qa": "QA",
    "kpi": "KPI",
    "api": "API",
    "sop": "SOP",
    "o365": "O365",
    "hr": "HR",
}

_DYNAMIC_TOPIC_RULES = {
    "ai_at_work": {
        "defaults": ["maintenance diagnostics", "quality inspection", "shift handover notes", "sop drafting"],
        "templates": [
            "AI copilots for {focus}",
            "Practical AI workflow for {focus}",
            "AI rollout playbook: {focus}",
        ],
    },
    "it_hacks": {
        "defaults": ["device troubleshooting", "network stability", "windows productivity", "mac productivity"],
        "templates": [
            "IT quick wins for {focus}",
            "Daily troubleshooting guide: {focus}",
            "Time-saving IT routines for {focus}",
        ],
    },
    "o365_updates": {
        "defaults": ["teams coordination", "sharepoint knowledge", "power automate approvals", "copilot workflows"],
        "templates": [
            "O365 workflow pattern for {focus}",
            "Microsoft 365 setup for {focus}",
            "Copilot + O365 guide for {focus}",
        ],
    },
    "tech_discovery": {
        "defaults": ["digital twins pilots", "edge analytics platforms", "industrial ai copilots", "connected operations"],
        "templates": [
            "Emerging platform brief: {focus}",
            "Pilot candidate for BEST Group: {focus}",
            "New software angle in manufacturing: {focus}",
        ],
    },
    "tech_trends": {
        "defaults": ["resilient supply chains", "digital thread adoption", "predictive maintenance at scale", "smart factory ops"],
        "templates": [
            "Manufacturing trend watch: {focus}",
            "Business impact trend brief: {focus}",
            "Where the market is moving: {focus}",
        ],
    },
}

_TITLE_DOMAIN_NOUNS = {
    "ai",
    "automation",
    "copilot",
    "copilots",
    "discovery",
    "factory",
    "industrial",
    "it",
    "maintenance",
    "manufacturing",
    "market",
    "o365",
    "operations",
    "platform",
    "productivity",
    "quality",
    "software",
    "trend",
    "trends",
    "workflow",
}

_AMBIGUOUS_SINGLETON_FOCUS = {
    "dark",
    "factory",
    "factories",
    "platform",
    "trend",
    "trends",
}

_SINGLE_TERM_FOCUS_REWRITE = {
    "dark": "lights out manufacturing operations",
    "factory": "factory operations",
    "factories": "manufacturing operations",
    "copilot": "ai copilot workflows",
    "copilots": "ai copilot workflows",
    "o365": "microsoft 365 workflows",
    "it": "it operations",
}

_FOCUS_PATTERN_REWRITES = (
    (re.compile(r"\bdark\s+factories?\b", re.IGNORECASE), "lights out manufacturing operations"),
    (re.compile(r"\bdark\s+manufacturing\b", re.IGNORECASE), "lights out manufacturing operations"),
)


@dataclass
class TopicHistory:
    repeat_count: int = 0
    recency_penalty: float = 0.0
    sent_count: int = 0
    success_signal: float = 0.0
    failure_signal: float = 0.0
    preference_signal: float = 0.0


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t and t not in _STOPWORDS]


def _confidence(score: float) -> str:
    if score >= 1.3:
        return "High"
    if score >= 1.0:
        return "Medium"
    return "Low"


def _canonical_slug(raw: str | None) -> str:
    return (raw or "").strip().lower().replace("-", "_")


def _anchor_slug(raw: str | None) -> str:
    slug = _canonical_slug(raw)
    if "__" in slug:
        return slug.split("__", 1)[0]
    return slug


def _normalize_text(raw: str | None) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def _dedupe_preserve(values: list[str], *, max_items: int | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = _normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if max_items and len(out) >= max_items:
            break
    return out


def _query_phrases(search_queries: list[str], max_terms: int = 6) -> list[str]:
    phrases: list[str] = []
    for query in search_queries or []:
        cleaned = re.sub(r"\s+", " ", (query or "").strip())
        if not cleaned:
            continue
        for part in re.split(r"[|;/]+", cleaned):
            phrase = part.strip(" ,.-")
            if len(phrase) >= 3:
                phrases.append(phrase)

    seen: set[str] = set()
    ordered: list[str] = []
    for phrase in phrases:
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(phrase)
        if len(ordered) >= max_terms:
            break
    return ordered


def _query_focus_candidates(search_queries: list[str], max_terms: int = 8) -> list[str]:
    candidates: list[str] = []
    for phrase in _query_phrases(search_queries, max_terms=max_terms):
        tokens = _tokenize(phrase)
        if not tokens:
            continue
        candidates.append(" ".join(tokens))

        token_len = len(tokens)
        for n in (4, 3, 2):
            if token_len < n:
                continue
            for index in range(0, token_len - n + 1):
                gram = tokens[index : index + n]
                candidates.append(" ".join(gram))

    return _dedupe_preserve(candidates, max_items=max_terms)


def _normalize_focus_phrase(raw: str | None) -> str:
    text = _normalize_text(raw)
    if not text:
        return ""

    for pattern, replacement in _FOCUS_PATTERN_REWRITES:
        text = pattern.sub(replacement, text)

    tokens = _tokenize(text)
    if len(tokens) == 1:
        replacement = _SINGLE_TERM_FOCUS_REWRITE.get(tokens[0])
        if replacement:
            tokens = _tokenize(replacement)

    if len(tokens) < 2:
        return ""
    if len(tokens) > 6:
        tokens = tokens[:6]

    return " ".join(tokens)


def _humanize_focus(raw: str) -> str:
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", (raw or "").strip()) if w]
    if not words:
        return "Manufacturing Operations"
    out = []
    for word in words:
        lw = word.lower()
        out.append(_ACRONYM_MAP.get(lw, word.capitalize()))
    return " ".join(out)


def _build_dynamic_topic_slug(base_slug: str, title: str) -> str:
    digest = hashlib.sha1(f"{base_slug}|{title.lower()}".encode("utf-8")).hexdigest()[:10]
    return f"{base_slug}__{digest}"


def _focus_pool(base_slug: str, search_queries: list[str], max_terms: int = 8) -> list[str]:
    cfg = _DYNAMIC_TOPIC_RULES.get(base_slug, {})
    pool = [
        *(_query_focus_candidates(search_queries, max_terms=8)),
        *(cfg.get("defaults", [])),
    ]
    ordered = []
    seen: set[str] = set()
    for raw in pool:
        normalized = _normalize_focus_phrase(raw)
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
        if len(ordered) >= max_terms:
            break
    return ordered


def _template_family_key(template: str) -> str:
    key = re.sub(r"\{focus\}", "focus", _normalize_text(template))
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return key or "template"


def _title_has_domain_noun(title: str) -> bool:
    return bool(set(_tokenize(title)).intersection(_TITLE_DOMAIN_NOUNS))


def _is_ambiguous_focus_phrase(focus: str) -> bool:
    terms = _tokenize(focus)
    return len(terms) == 1 and terms[0] in _AMBIGUOUS_SINGLETON_FOCUS


def _passes_title_guardrails(title: str, focus: str) -> bool:
    if not _title_has_domain_noun(title):
        return False
    if _is_ambiguous_focus_phrase(focus):
        return False
    return True


def _build_dynamic_title_candidates(
    base_slug: str,
    base_label: str,
    search_queries: list[str],
    max_titles: int = 6,
) -> list[dict[str, str]]:
    cfg = _DYNAMIC_TOPIC_RULES.get(base_slug, {})
    templates = cfg.get("templates") or ["Practical focus for {focus}"]
    focuses = _focus_pool(base_slug, search_queries)
    if not focuses:
        fallback = _normalize_focus_phrase(base_label)
        focuses = [fallback] if fallback else ["manufacturing operations"]

    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    base_key = _normalize_text(base_label)
    for template in templates:
        template_family = _template_family_key(template)
        for focus in focuses:
            rendered = re.sub(r"\s+", " ", template.format(focus=_humanize_focus(focus))).strip()
            if not rendered:
                continue
            if not _passes_title_guardrails(rendered, focus):
                continue
            key = _normalize_text(rendered)
            if key == base_key:
                continue
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "title": rendered,
                    "template_family": template_family,
                    "focus_key": _normalize_text(focus),
                }
            )
            if len(candidates) >= max_titles:
                return candidates
    return candidates


def _build_dynamic_titles(base_slug: str, base_label: str, search_queries: list[str], max_titles: int = 4) -> list[str]:
    return [
        item["title"]
        for item in _build_dynamic_title_candidates(
            base_slug,
            base_label,
            search_queries,
            max_titles=max_titles,
        )
    ]


def _query_keywords(search_queries: list[str], max_terms: int = 8) -> list[str]:
    terms = []
    for query in search_queries or []:
        terms.extend(_tokenize(query))
    counts = Counter(terms)
    return [term for term, _ in counts.most_common(max_terms)]


def _is_near_duplicate_to_query(title: str, latest_query: str) -> bool:
    title_norm = _normalize_text(title)
    query_norm = _normalize_text(latest_query)
    if not title_norm or not query_norm:
        return False

    if title_norm == query_norm:
        return True
    if len(query_norm) >= 8 and query_norm in title_norm:
        return True

    title_terms = set(_tokenize(title_norm))
    query_terms = set(_tokenize(query_norm))
    if not title_terms or not query_terms:
        return False

    overlap = len(title_terms.intersection(query_terms))
    containment = overlap / max(1, len(query_terms))
    jaccard = overlap / max(1, len(title_terms.union(query_terms)))
    return containment >= 0.95 or jaccard >= 0.82


def _title_similarity(left: str, right: str) -> float:
    left_terms = set(_tokenize(left))
    right_terms = set(_tokenize(right))
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms.intersection(right_terms))
    jaccard = overlap / max(1, len(left_terms.union(right_terms)))
    containment = max(
        overlap / max(1, len(left_terms)),
        overlap / max(1, len(right_terms)),
    )
    return max(jaccard, containment)


def _titles_too_similar(left: str, right: str) -> bool:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if len(left_norm) >= 16 and left_norm in right_norm:
        return True
    if len(right_norm) >= 16 and right_norm in left_norm:
        return True
    return _title_similarity(left_norm, right_norm) >= 0.62


def _source_pool_for_row(row: dict[str, Any]) -> str:
    if row.get("latest_hits"):
        return "latest"
    if row.get("history_affinity") or row.get("memory_hits"):
        return "historical"
    return "exploratory"


def _pool_targets(max_count: int, *, latest_available: bool) -> dict[str, int]:
    targets = {
        "latest": min(2, max_count) if latest_available else 0,
        "historical": min(2, max_count),
        "exploratory": min(1, max_count),
    }
    total = sum(targets.values())
    while total > max_count:
        for key in ("latest", "historical", "exploratory"):
            if total <= max_count:
                break
            if targets[key] <= 0:
                continue
            targets[key] -= 1
            total -= 1
    return targets


def _build_ui_rationale(
    *,
    base_label: str,
    latest_hits: list[str],
    live_hits: list[str],
    effective_hits: list[str],
    memory_hits: list[str],
    base_history: TopicHistory,
    exact_history: TopicHistory,
) -> str:
    anchor = (base_label or "").strip() or "this section"

    if latest_hits:
        lead = f"Suggested from your latest search and your interest in {anchor}."
    elif live_hits or effective_hits:
        lead = f"Suggested from your recent searches and your interest in {anchor}."
    elif memory_hits or base_history.repeat_count:
        lead = f"Suggested based on your past interest in {anchor}."
    else:
        lead = f"Suggested as a relevant angle within {anchor}."

    if exact_history.repeat_count:
        tail = "Refined to avoid repeating recent runs."
    elif base_history.success_signal > (base_history.failure_signal + 0.05):
        tail = "Similar themes performed well in prior runs."
    elif base_history.failure_signal > (base_history.success_signal + 0.1):
        tail = "Adjusted away from weaker prior outcomes."
    else:
        tail = "Fresh angle compared with your recent runs."

    return f"{lead} {tail}"


def _load_recent_search_memory(
    *,
    window_days: int = 90,
    max_phrases: int = 8,
    max_terms: int = 12,
) -> tuple[list[str], dict[str, float]]:
    cutoff = time.time() - (window_days * 24 * 60 * 60)
    try:
        with get_memory_conn() as conn:
            rows = conn.execute(
                """
                SELECT created_at, query_text, event_type, outcome, results_json
                FROM search_events
                WHERE created_at >= ?
                ORDER BY created_at DESC
                LIMIT 2000
                """,
                (cutoff,),
            ).fetchall()
    except Exception:
        return [], {}

    phrase_scores: dict[str, float] = {}
    term_scores: dict[str, float] = {}
    for row in rows:
        query = re.sub(r"\s+", " ", str(row["query_text"] or "").strip())
        if not query:
            continue

        event_type = str(row["event_type"] or "").strip().lower()
        outcome = str(row["outcome"] or "").strip().lower()
        decay = _decay_factor(row["created_at"], half_life_days=35.0)

        if event_type == "search":
            base = 0.2
        elif event_type == "used_in_run":
            base = 0.4
        elif event_type == "run":
            base = 0.45 if outcome == "success" else -0.2
        elif event_type == "delivery":
            if outcome in {"sent", "success"}:
                base = 0.95
            elif outcome in {"send_failed", "failed"}:
                base = -0.35
            else:
                base = -0.12
        else:
            base = 0.05

        weighted = base * decay
        phrase_scores[query] = phrase_scores.get(query, 0.0) + weighted
        for term in _tokenize(query):
            term_scores[term] = term_scores.get(term, 0.0) + weighted

        raw_results = row["results_json"]
        if not raw_results:
            continue
        try:
            parsed = json.loads(raw_results)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            for term in _tokenize(title):
                term_scores[term] = term_scores.get(term, 0.0) + (0.05 * decay)

    ranked_phrases = [
        phrase for phrase, score in sorted(phrase_scores.items(), key=lambda kv: kv[1], reverse=True) if score > 0.05
    ]
    ranked_terms = {
        term: score
        for term, score in sorted(term_scores.items(), key=lambda kv: kv[1], reverse=True)
        if score > 0.04
    }
    return ranked_phrases[:max_phrases], dict(list(ranked_terms.items())[:max_terms])


def _decay_factor(created_at: float, *, half_life_days: float = 45.0) -> float:
    age_sec = max(0.0, time.time() - float(created_at or 0.0))
    if age_sec <= 0:
        return 1.0
    half_life_sec = max(1.0, half_life_days * 24 * 60 * 60)
    return 0.5 ** (age_sec / half_life_sec)


def _history_targets(topic_slug: str, base_slug: str | None = None) -> list[str]:
    canonical_topic = _canonical_slug(topic_slug)
    canonical_base = _canonical_slug(base_slug or _anchor_slug(canonical_topic))
    targets = []
    if canonical_topic:
        targets.append(canonical_topic)
    if canonical_base and canonical_base not in targets:
        targets.append(canonical_base)
    return targets


def load_recent_topic_history(
    window_episodes: int = DEFAULT_RECENT_EPISODES,
    window_days: int = DEFAULT_RECENT_DAYS,
) -> dict[str, TopicHistory]:
    cutoff = time.time() - (window_days * 24 * 60 * 60)
    history: dict[str, TopicHistory] = {}

    # Primary learning source: explicit topic events (selection, execution, delivery).
    try:
        with get_memory_conn() as conn:
            event_rows = conn.execute(
                """
                SELECT created_at, topic_slug, base_slug, event_type, outcome
                FROM topic_events
                WHERE created_at >= ?
                ORDER BY created_at DESC
                LIMIT 1200
                """,
                (cutoff,),
            ).fetchall()
    except Exception:
        event_rows = []

    for row in event_rows:
        topic_slug = _canonical_slug(row["topic_slug"])
        base_slug = _canonical_slug(row["base_slug"])
        if not topic_slug:
            continue
        decay = _decay_factor(row["created_at"])
        event_type = str(row["event_type"] or "").strip().lower()
        outcome = str(row["outcome"] or "").strip().lower()

        for target in _history_targets(topic_slug, base_slug):
            entry = history.setdefault(target, TopicHistory())
            if outcome in {"approved", "selected", "sent", "success"}:
                entry.repeat_count += 1

            if event_type == "selection":
                if outcome == "approved":
                    entry.preference_signal += 0.55 * decay
                    entry.recency_penalty += 0.35 * decay
                elif outcome in {"skipped", "rejected"}:
                    entry.preference_signal -= 0.22 * decay
            elif event_type == "execution":
                if outcome == "success":
                    entry.success_signal += 0.65 * decay
                    entry.recency_penalty += 0.28 * decay
                elif outcome in {"failed", "missing", "aborted"}:
                    entry.failure_signal += 0.75 * decay
            elif event_type == "delivery":
                if outcome == "sent":
                    entry.sent_count += 1
                    entry.success_signal += 0.9 * decay
                    entry.recency_penalty += 0.85 * decay
                elif outcome == "send_failed":
                    entry.failure_signal += 0.45 * decay
                elif outcome == "send_blocked":
                    entry.preference_signal -= 0.05 * decay

    if history:
        return history

    # Fallback for older data before topic_events existed.
    rows: list[Any] = []
    try:
        with get_memory_conn() as conn:
            rows = conn.execute(
                """
                SELECT selected_topics_json, started_at
                FROM episodes
                WHERE started_at >= ?
                ORDER BY started_at DESC
                LIMIT 50
                """,
                (cutoff,),
            ).fetchall()
    except Exception:
        return {}

    rows = rows[:window_episodes]
    counts: Counter[str] = Counter()
    penalties: Counter[str] = Counter()
    for index, row in enumerate(rows):
        try:
            selected = json.loads(row["selected_topics_json"] or "[]")
        except json.JSONDecodeError:
            selected = []
        if not isinstance(selected, list):
            continue

        # More recent runs apply a stronger soft demotion.
        recency_weight = max(0.1, 1.0 - (index * 0.15))
        for raw_slug in selected:
            slug = _canonical_slug(raw_slug if isinstance(raw_slug, str) else "")
            if not slug:
                continue
            counts[slug] += 1
            penalties[slug] += recency_weight
            anchor = _anchor_slug(slug)
            if anchor and anchor != slug:
                counts[anchor] += 1
                penalties[anchor] += recency_weight

    history = {}
    for slug, count in counts.items():
        history[slug] = TopicHistory(repeat_count=int(count), recency_penalty=float(penalties[slug]))
    return history


def build_freeform_topic_label(search_queries: list[str]) -> str:
    keywords = _query_keywords(search_queries, max_terms=4)
    if not keywords:
        return "Emerging Manufacturing Innovation"
    return " ".join(k.capitalize() for k in keywords)


def build_freeform_topic_slug(search_queries: list[str]) -> str:
    seed = " ".join(search_queries or []).strip().lower()
    if not seed:
        seed = "freeform-default"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"freeform_{digest}"


def build_freeform_definition(topic_label: str, search_queries: list[str]) -> dict[str, str]:
    queries_block = "\n".join(f"- {q}" for q in (search_queries or []))
    context_line = (
        f"Use these real user search cues as context:\n{queries_block}\n\n"
        if queries_block
        else "No user-provided search context was supplied.\n\n"
    )
    research_brief = (
        f'Topic: "{topic_label}"\n'
        "Your task is to research this custom, forward-looking topic for BEST Group (manufacturing).\n\n"
        f"{context_line}"
        "1. Identify one concrete angle within this topic with near-term impact.\n"
        "2. Summarize what it is in simple terms.\n"
        "3. Provide 3-5 practical applications for BEST Group.\n"
        "4. Provide 3-5 reliable sources with links.\n\n"
        "RULES:\n"
        "- All factual claims must be source-backed.\n"
        "- Prefer official docs, product pages, and reputable industry sources.\n"
    )
    research_schema = """
{
  "topic": "{topic}",
  "selected_focus": "<specific focus area>",
  "overview": "<100-150 words plain-language summary>",
  "recent_updates": [
    {"date": "<YYYY-MM>", "update": "<what changed>", "source": "<url>"}
  ],
  "applications_for_best_group": ["...", "...", "..."],
  "value_summary": "<why this matters for manufacturing>",
  "sources": [
    {"title": "<source title>", "url": "<url>"}
  ]
}
"""
    writer_brief = (
        "Using the research output JSON, write an executive-ready article focused on the selected focus area.\n\n"
        "REQUIREMENTS:\n"
        "- Keep language practical and business-focused.\n"
        "- Use these exact headings in this order:\n"
        "  1) Introducing [Selected Focus]\n"
        "  2) What's New\n"
        "  3) Why This Matters\n"
        "  4) How BEST Group Could Use It\n"
        "  5) Where to Learn More\n"
    )
    writer_schema = """
## Introducing [Selected Focus]
...
## What's New
...
## Why This Matters
...
## How BEST Group Could Use It
...
## Where to Learn More
...
"""
    editor_brief = (
        "Review and edit the article to ensure clarity and business readability.\n\n"
        "RULES:\n"
        "- Keep the five exact headings unchanged.\n"
        "- Simplify jargon and keep actionability high.\n"
        "- Preserve factual claims and links.\n"
        "- Keep output under 700 words.\n"
    )

    return {
        "topic": topic_label,
        "topic_slug": build_freeform_topic_slug(search_queries),
        "research_brief": research_brief,
        "research_output_schema": f"Your final output MUST be a JSON object matching this exact schema:\n{research_schema}",
        "writer_brief": writer_brief,
        "writer_output_schema": f"Your final output MUST be a Markdown article matching this structure:\n{writer_schema}",
        "editor_brief": editor_brief,
    }


def propose_topics(
    candidate_topics: list[dict[str, Any]],
    search_queries: list[str],
    *,
    include_freeform: bool = False,
    max_template_proposals: int = DEFAULT_TEMPLATE_PROPOSAL_COUNT,
) -> tuple[list[dict[str, Any]], str | None]:
    search_queries = list(search_queries or [])
    latest_query = _normalize_text(search_queries[-1] if search_queries else "")
    latest_query_terms = set(_tokenize(latest_query))
    memory_phrases, memory_term_scores = _load_recent_search_memory()

    # Use current user context first; augment with learned signals from prior searches.
    effective_queries = list(search_queries)
    if not effective_queries and memory_phrases:
        effective_queries = memory_phrases[:4]
    elif memory_phrases:
        existing = {q.strip().lower() for q in effective_queries if q and q.strip()}
        for phrase in memory_phrases[:2]:
            key = phrase.strip().lower()
            if key and key not in existing:
                effective_queries.append(phrase)

    live_query_terms = set(_query_keywords(search_queries))
    effective_query_terms = set(_query_keywords(effective_queries))
    memory_query_terms = set(memory_term_scores.keys())
    history = load_recent_topic_history()
    ranked: list[dict[str, Any]] = []

    for topic in candidate_topics:
        base_slug = _canonical_slug(topic.get("slug"))
        if not base_slug:
            continue
        base_label = str(topic.get("label") or topic.get("topic") or base_slug.replace("_", " ").title())
        base_icon = topic.get("icon", "*")
        base_history = history.get(base_slug, TopicHistory(repeat_count=0, recency_penalty=0.0))

        for title_candidate in _build_dynamic_title_candidates(base_slug, base_label, effective_queries, max_titles=8):
            title = title_candidate["title"]
            if latest_query and _is_near_duplicate_to_query(title, latest_query):
                continue

            candidate_slug = _build_dynamic_topic_slug(base_slug, title)
            exact_history = history.get(candidate_slug, TopicHistory())
            title_terms = set(_tokenize(title))
            latest_hits = sorted(latest_query_terms.intersection(title_terms))
            live_hits = sorted(live_query_terms.intersection(title_terms))
            effective_hits = sorted(effective_query_terms.intersection(title_terms))
            memory_hits = sorted((memory_query_terms - set(effective_hits)).intersection(title_terms))
            history_affinity = bool(
                base_history.repeat_count
                or base_history.sent_count
                or base_history.preference_signal > 0.05
                or base_history.success_signal > 0.05
            )
            history_success = bool(
                base_history.repeat_count
                or base_history.sent_count
                or base_history.success_signal > (base_history.failure_signal * 0.9)
            )

            relevance = min(0.9, 0.24 * len(effective_hits))
            memory_boost = min(0.18, sum(memory_term_scores.get(term, 0.0) for term in memory_hits) * 0.12)
            interest_boost = min(0.45, base_history.repeat_count * 0.09)
            preference_boost = max(-0.35, min(0.55, base_history.preference_signal * 0.55))
            success_boost = max(
                -0.25, min(0.45, (base_history.success_signal - (base_history.failure_signal * 0.65)) * 0.5)
            )
            exact_repeat_penalty = min(0.55, exact_history.recency_penalty * 0.3)
            sent_repeat_penalty = min(0.5, (exact_history.sent_count * 0.12) + (exact_history.recency_penalty * 0.08))
            failure_drag = min(0.45, (exact_history.failure_signal + base_history.failure_signal) * 0.25)
            freshness_bonus = 0.12 if exact_history.repeat_count == 0 else 0.0
            score = (
                1.0
                + relevance
                + memory_boost
                + interest_boost
                + preference_boost
                + success_boost
                + freshness_bonus
                - exact_repeat_penalty
                - sent_repeat_penalty
                - failure_drag
            )

            rationale_parts = [f"Anchored to {base_label} but focused on a specific, current angle."]
            if latest_hits:
                rationale_parts.append("Related to your latest search: " + ", ".join(latest_hits[:4]))
            elif live_hits:
                rationale_parts.append("Matches current search context: " + ", ".join(live_hits[:4]))
            elif effective_hits:
                rationale_parts.append("Guided by your recent search interests: " + ", ".join(effective_hits[:4]))
            if memory_hits:
                rationale_parts.append("Also aligned with past search behavior: " + ", ".join(memory_hits[:3]))
            if base_history.repeat_count:
                rationale_parts.append(
                    f"Personalized from your recent interest in {base_label} ({base_history.repeat_count} run(s))."
                )
            if base_history.sent_count:
                rationale_parts.append(
                    f"Previously sent under this theme {base_history.sent_count} time(s); balancing freshness."
                )
            if base_history.success_signal > base_history.failure_signal:
                rationale_parts.append("Boosted because related topics performed well in prior runs.")
            elif base_history.failure_signal > base_history.success_signal:
                rationale_parts.append("Slightly demoted due to weaker outcomes on related topics.")
            if exact_history.repeat_count:
                rationale_parts.append(
                    f"Soft-demoted because this exact angle appeared in {exact_history.repeat_count} recent run(s)."
                )
            else:
                rationale_parts.append("Fresh angle not used in recent runs.")

            ranked.append(
                {
                    "proposal_id": candidate_slug,
                    "slug": candidate_slug,
                    "type": "dynamic_template",
                    "base_slug": base_slug,
                    "anchor_label": base_label,
                    "topic": title,
                    "label": title,
                    "icon": base_icon,
                    "score": round(score, 3),
                    "confidence": _confidence(score),
                    "rationale": " ".join(rationale_parts),
                    "ui_rationale": _build_ui_rationale(
                        base_label=base_label,
                        latest_hits=latest_hits,
                        live_hits=live_hits,
                        effective_hits=effective_hits,
                        memory_hits=memory_hits,
                        base_history=base_history,
                        exact_history=exact_history,
                    ),
                    "matched_terms": latest_hits or effective_hits or memory_hits,
                    "repeat_count": exact_history.repeat_count,
                    "system_suggested": True,
                    "latest_hits": latest_hits,
                    "memory_hits": memory_hits,
                    "history_affinity": history_affinity,
                    "history_success": history_success,
                    "template_family": title_candidate["template_family"],
                    "focus_key": title_candidate["focus_key"],
                }
            )

    ranked.sort(key=lambda row: row["score"], reverse=True)
    for row in ranked:
        row["source_pool"] = _source_pool_for_row(row)

    max_template_proposals = max(1, max_template_proposals)
    template_proposals: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    base_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    pool_counts: Counter[str] = Counter()
    selected_focuses: set[str] = set()
    max_per_base = max_template_proposals if len(candidate_topics) <= 1 else 2
    latest_related_cap = min(MAX_LATEST_QUERY_PROPOSALS, max_template_proposals) if latest_query_terms else 0
    available_families = {str(row.get("template_family") or "") for row in ranked if row.get("template_family")}
    allow_family_relax = len(candidate_topics) <= 1 or len(available_families) < max_template_proposals

    def _try_add(
        row: dict[str, Any],
        *,
        enforce_latest_cap: bool = True,
        enforce_base_cap: bool = True,
        enforce_family_diversity: bool = True,
        enforce_focus_diversity: bool = True,
        enforce_similarity: bool = True,
    ) -> bool:
        if len(template_proposals) >= max_template_proposals:
            return False
        proposal_id = str(row.get("proposal_id") or row.get("slug"))
        if proposal_id in selected_ids:
            return False
        base_slug = str(row.get("base_slug") or "")
        if enforce_base_cap and base_slug and base_counts[base_slug] >= max_per_base:
            return False

        source_pool = str(row.get("source_pool") or "exploratory")
        if (
            latest_query_terms
            and enforce_latest_cap
            and source_pool == "latest"
            and pool_counts["latest"] >= latest_related_cap
        ):
            return False

        template_family = str(row.get("template_family") or "")
        if enforce_family_diversity and template_family and family_counts[template_family] >= 1:
            return False

        focus_key = _normalize_text(row.get("focus_key"))
        if enforce_focus_diversity and focus_key and focus_key in selected_focuses:
            return False

        if enforce_similarity:
            for existing in template_proposals:
                if _titles_too_similar(str(row.get("label") or ""), str(existing.get("label") or "")):
                    return False

        row_copy = dict(row)
        template_proposals.append(row_copy)
        selected_ids.add(proposal_id)
        if base_slug:
            base_counts[base_slug] += 1
        if template_family:
            family_counts[template_family] += 1
        if focus_key:
            selected_focuses.add(focus_key)
        pool_counts[source_pool] += 1
        return True

    def _fill_quota(pool_name: str, target: int) -> None:
        while pool_counts[pool_name] < target and len(template_proposals) < max_template_proposals:
            added = False
            for row in ranked:
                if row.get("source_pool") != pool_name:
                    continue
                if _try_add(row):
                    added = True
                    break
            if not added:
                for row in ranked:
                    if row.get("source_pool") != pool_name:
                        continue
                    if _try_add(
                        row,
                        enforce_family_diversity=not allow_family_relax,
                        enforce_focus_diversity=False,
                    ):
                        added = True
                        break
            if not added:
                break

    targets = _pool_targets(max_template_proposals, latest_available=bool(latest_query_terms))
    available_pools = {str(row.get("source_pool") or "exploratory") for row in ranked}
    for pool_name in ("latest", "historical", "exploratory"):
        if pool_name not in available_pools:
            targets[pool_name] = 0

    if len(available_pools) >= 2 and max_template_proposals >= 2:
        active_pools = [pool for pool, value in targets.items() if value > 0]
        if len(active_pools) < 2:
            for pool_name in ("historical", "exploratory", "latest"):
                if pool_name in available_pools and targets[pool_name] == 0:
                    targets[pool_name] = 1
                    break
            while sum(targets.values()) > max_template_proposals:
                for pool_name in ("latest", "historical", "exploratory"):
                    if sum(targets.values()) <= max_template_proposals:
                        break
                    if targets[pool_name] > 0:
                        targets[pool_name] -= 1

    for pool_name in ("historical", "exploratory", "latest"):
        _fill_quota(pool_name, targets.get(pool_name, 0))

    if len(template_proposals) < max_template_proposals:
        for row in ranked:
            if len(template_proposals) >= max_template_proposals:
                break
            _try_add(row)

    if len(template_proposals) < max_template_proposals:
        for row in ranked:
            if len(template_proposals) >= max_template_proposals:
                break
            _try_add(
                row,
                enforce_family_diversity=not allow_family_relax,
                enforce_focus_diversity=False,
            )

    if len(template_proposals) < max_template_proposals:
        for row in ranked:
            if len(template_proposals) >= max_template_proposals:
                break
            _try_add(
                row,
                enforce_base_cap=False,
                enforce_family_diversity=not allow_family_relax,
                enforce_focus_diversity=False,
                enforce_similarity=False,
            )

    selected_pools = {str(row.get("source_pool") or "exploratory") for row in template_proposals}
    if len(selected_pools) < 2 and len(available_pools) >= 2 and len(template_proposals) >= 1:
        missing_pools = [
            pool
            for pool in ("historical", "exploratory", "latest")
            if pool in available_pools and pool not in selected_pools
        ]
        for missing_pool in missing_pools:
            replacement = next(
                (
                    row
                    for row in ranked
                    if row.get("source_pool") == missing_pool
                    and str(row.get("proposal_id") or row.get("slug")) not in selected_ids
                ),
                None,
            )
            if not replacement:
                continue

            removable = None
            for row in sorted(template_proposals, key=lambda item: float(item.get("score") or 0.0)):
                if pool_counts[str(row.get("source_pool") or "exploratory")] > 1:
                    removable = row
                    break
            if not removable:
                continue

            template_proposals.remove(removable)
            remove_id = str(removable.get("proposal_id") or removable.get("slug"))
            selected_ids.discard(remove_id)
            remove_base = str(removable.get("base_slug") or "")
            if remove_base:
                base_counts[remove_base] = max(0, base_counts[remove_base] - 1)
            remove_family = str(removable.get("template_family") or "")
            if remove_family:
                family_counts[remove_family] = max(0, family_counts[remove_family] - 1)
            remove_focus = _normalize_text(removable.get("focus_key"))
            if remove_focus:
                selected_focuses.discard(remove_focus)
            remove_pool = str(removable.get("source_pool") or "exploratory")
            pool_counts[remove_pool] = max(0, pool_counts[remove_pool] - 1)

            if not _try_add(
                replacement,
                enforce_base_cap=False,
                enforce_family_diversity=not allow_family_relax,
                enforce_focus_diversity=False,
                enforce_similarity=False,
            ):
                _try_add(
                    removable,
                    enforce_base_cap=False,
                    enforce_family_diversity=not allow_family_relax,
                    enforce_focus_diversity=False,
                    enforce_similarity=False,
                )
                continue
            break

    for idx, row in enumerate(template_proposals):
        row.pop("latest_hits", None)
        row.pop("memory_hits", None)
        row.pop("history_affinity", None)
        row.pop("history_success", None)
        row.pop("source_pool", None)
        row.pop("template_family", None)
        row.pop("focus_key", None)
        row["selected"] = idx < min(3, len(template_proposals))

    planner_warning = None
    if include_freeform:
        try:
            freeform_label = build_freeform_topic_label(effective_queries)
            freeform_slug = build_freeform_topic_slug(effective_queries)
            template_proposals.append(
                {
                    "proposal_id": freeform_slug,
                    "slug": freeform_slug,
                    "type": "freeform",
                    "topic": freeform_label,
                    "label": freeform_label,
                    "icon": "*",
                    "score": 1.0,
                    "confidence": "Medium",
                    "rationale": "Optional custom topic generated from the provided search context.",
                    "matched_terms": _query_keywords(effective_queries, max_terms=5),
                    "repeat_count": 0,
                    "system_suggested": True,
                    "selected": False,
                }
            )
        except Exception:
            planner_warning = (
                "Optional free-form proposal could not be generated. You can continue with template topics."
            )

    return template_proposals, planner_warning
