import importlib
import os
import re
import sys
import unittest
from pathlib import Path

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
TEMPLATE_ROOT = REPO_ROOT / "src" / "research_crew" / "web" / "templates"
STATIC_ROOT = REPO_ROOT / "src" / "research_crew" / "web" / "static"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def load_routes_module(*, dynamic_enabled: bool, freeform_enabled: bool, candidate_count: int = 5):
    os.environ["DYNAMIC_TOPICS_ENABLED"] = "true" if dynamic_enabled else "false"
    os.environ["FREEFORM_TOPIC_SLOT_ENABLED"] = "true" if freeform_enabled else "false"
    os.environ["DYNAMIC_TOPIC_CANDIDATES"] = str(candidate_count)
    os.environ["SEARCH_CONTEXT_IN_PROMPTS"] = "true"

    module_name = "research_crew.web.routes"
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


def build_test_app(routes_module):
    app = Flask(
        "research_crew.web",
        template_folder=str(TEMPLATE_ROOT),
        static_folder=str(STATIC_ROOT),
    )
    app.register_blueprint(routes_module.bp)
    return app


class DynamicTopicSuggestionTests(unittest.TestCase):
    def test_dynamic_propose_returns_five_dynamic_suggestions(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        app = build_test_app(routes)
        client = app.test_client()

        response = client.post(
            "/topics/propose",
            data={"selected": "", "search_queries": "digital twins, predictive maintenance"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(re.findall(r'class="proposal-card"', html)), 5)
        self.assertIn("System suggested", html)
        self.assertIn("Start Approved Topics", html)
        self.assertIn("Related to", html)

    def test_dynamic_proposals_are_variants_not_base_labels(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        catalog = routes.searchable_catalog([t["slug"] for t in routes.TOPICS])
        proposals, _ = routes.propose_topics(
            catalog,
            ["digital twins", "predictive maintenance"],
            include_freeform=False,
            max_template_proposals=5,
        )

        base_labels = {t["label"] for t in routes.TOPICS}
        self.assertEqual(len(proposals), 5)
        self.assertTrue(all(p.get("type") == "dynamic_template" for p in proposals))
        self.assertTrue(all(p.get("base_slug") in routes.TOPIC_SLUGS for p in proposals))
        self.assertTrue(all((p.get("label") or "").strip() not in base_labels for p in proposals))

    def test_latest_query_influence_is_capped_to_two_suggestions(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        planner = importlib.import_module("research_crew.topic_planner")

        original_memory_loader = planner._load_recent_search_memory
        original_history_loader = planner.load_recent_topic_history
        try:
            planner._load_recent_search_memory = lambda **_kwargs: (
                ["digital thread adoption", "predictive maintenance at scale"],
                {"digital": 0.8, "thread": 0.7, "predictive": 0.9, "maintenance": 0.85},
            )
            planner.load_recent_topic_history = lambda *args, **kwargs: {}
            catalog = routes.searchable_catalog([t["slug"] for t in routes.TOPICS])
            proposals, _ = planner.propose_topics(
                catalog,
                ["dark factory"],
                include_freeform=False,
                max_template_proposals=5,
            )
        finally:
            planner._load_recent_search_memory = original_memory_loader
            planner.load_recent_topic_history = original_history_loader

        latest_terms = set(planner._tokenize("dark factory"))
        latest_related = [
            p for p in proposals if set(planner._tokenize(str(p.get("label") or ""))).intersection(latest_terms)
        ]

        self.assertLessEqual(len(latest_related), 2)
        self.assertGreaterEqual(len(proposals), 3)

    def test_sent_and_failure_history_influence_ranking(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        planner = importlib.import_module("research_crew.topic_planner")

        catalog = routes.searchable_catalog(["tech_discovery"])
        titles = planner._build_dynamic_titles("tech_discovery", "Tech Discovery", ["digital twins"], max_titles=2)
        self.assertGreaterEqual(len(titles), 2)

        first_slug = planner._build_dynamic_topic_slug("tech_discovery", titles[0])
        original_loader = planner.load_recent_topic_history
        try:
            planner.load_recent_topic_history = lambda *args, **kwargs: {
                "tech_discovery": planner.TopicHistory(
                    repeat_count=5,
                    recency_penalty=0.8,
                    sent_count=4,
                    success_signal=0.2,
                    failure_signal=1.3,
                    preference_signal=0.3,
                ),
                first_slug: planner.TopicHistory(
                    repeat_count=3,
                    recency_penalty=2.2,
                    sent_count=5,
                    success_signal=0.0,
                    failure_signal=1.4,
                    preference_signal=0.0,
                ),
            }
            proposals, _ = planner.propose_topics(
                catalog,
                ["digital twins"],
                include_freeform=False,
                max_template_proposals=2,
            )
        finally:
            planner.load_recent_topic_history = original_loader

        self.assertEqual(len(proposals), 2)
        self.assertNotEqual(proposals[0]["slug"], first_slug)
        rationale_text = " ".join(p.get("rationale", "") for p in proposals)
        self.assertIn("Previously sent", rationale_text)
        self.assertIn("weaker outcomes", rationale_text)

    def test_guardrails_avoid_ambiguous_single_word_focus_labels(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        planner = importlib.import_module("research_crew.topic_planner")

        catalog = routes.searchable_catalog([t["slug"] for t in routes.TOPICS])
        proposals, _ = planner.propose_topics(
            catalog,
            ["dark factory"],
            include_freeform=False,
            max_template_proposals=5,
        )

        labels = [str(p.get("label") or "") for p in proposals]
        bad_suffix_patterns = [
            r"\bfor dark$",
            r"\bfor factories$",
            r"\bdark$",
            r"\bfactories$",
        ]
        for label in labels:
            lowered = label.strip().lower()
            for pattern in bad_suffix_patterns:
                self.assertIsNone(re.search(pattern, lowered))

    def test_proposals_reduce_template_family_repetition(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        planner = importlib.import_module("research_crew.topic_planner")

        catalog = routes.searchable_catalog([t["slug"] for t in routes.TOPICS])
        proposals, _ = planner.propose_topics(
            catalog,
            ["predictive maintenance trends in manufacturing"],
            include_freeform=False,
            max_template_proposals=5,
        )

        def family_signature(title: str) -> str:
            lowered = title.lower()
            if " for " in lowered:
                return lowered.split(" for ", 1)[0].strip()
            if ":" in lowered:
                return lowered.split(":", 1)[0].strip()
            return lowered

        families = [family_signature(str(p.get("label") or "")) for p in proposals]
        self.assertEqual(len(families), len(set(families)))

    def test_topics_propose_records_impression_telemetry(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        calls = []

        routes.record_proposal_impressions = lambda **kwargs: calls.append(kwargs) or 1

        app = build_test_app(routes)
        client = app.test_client()
        response = client.post(
            "/topics/propose",
            data={"selected": "", "search_queries": "digital twins, predictive maintenance"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(calls)
        first = calls[0]
        self.assertIn("proposed_topics", first)
        self.assertGreaterEqual(len(first.get("proposed_topics") or []), 1)
        self.assertEqual((first.get("metadata") or {}).get("source"), "topics_propose")

    def test_dynamic_run_requires_approved_topics(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        routes.save_job = lambda *_args, **_kwargs: None
        routes.create_episode = lambda *_args, **_kwargs: "ep_test"
        routes.enqueue_job = lambda *_args, **_kwargs: None

        app = build_test_app(routes)
        client = app.test_client()

        response = client.post(
            "/run",
            data={
                "selected_csv": "tech_discovery",
                "topic_scope": "dynamic_templates",
                "proposed_topics_version": "1730000000",
                "proposed_topics_json": (
                    '[{"slug":"tech_discovery__abc123","type":"dynamic_template","base_slug":"tech_discovery",'
                    '"anchor_label":"Tech Discovery","label":"Emerging platform brief: Digital Twins",'
                    '"topic":"Emerging platform brief: Digital Twins","icon":"*","system_suggested":true}]'
                ),
                "approved_topics_json": "[]",
                "search_queries": "digital twins",
            },
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Approve at least one proposed topic before running.", html)

    def test_dynamic_run_accepts_approved_topics(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        routes.save_job = lambda *_args, **_kwargs: None
        routes.create_episode = lambda *_args, **_kwargs: "ep_test"
        routes.enqueue_job = lambda *_args, **_kwargs: None

        app = build_test_app(routes)
        client = app.test_client()

        response = client.post(
            "/run",
            data={
                "selected_csv": "tech_discovery",
                "topic_scope": "dynamic_templates",
                "proposed_topics_version": "1730000001",
                "proposed_topics_json": (
                    '[{"slug":"tech_discovery__abc123","type":"dynamic_template","base_slug":"tech_discovery",'
                    '"anchor_label":"Tech Discovery","label":"Emerging platform brief: Digital Twins",'
                    '"topic":"Emerging platform brief: Digital Twins","icon":"*","system_suggested":true}]'
                ),
                "approved_topics_json": (
                    '[{"slug":"tech_discovery__abc123","type":"dynamic_template","base_slug":"tech_discovery",'
                    '"anchor_label":"Tech Discovery","label":"Emerging platform brief: Digital Twins",'
                    '"topic":"Emerging platform brief: Digital Twins","icon":"*","system_suggested":true}]'
                ),
                "search_queries": "digital twins",
            },
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("AI Specialists are working", html)
        self.assertTrue(
            any(
                job.get("topic_scope") == "dynamic_templates"
                and any(a.get("base_slug") == "tech_discovery" for a in job.get("approved_topics", []))
                for job in routes.JOBS.values()
            )
        )

    def test_static_mode_hides_propose_button(self):
        routes = load_routes_module(dynamic_enabled=False, freeform_enabled=False, candidate_count=5)
        routes.load_latest_job = lambda since=None: None

        app = build_test_app(routes)
        client = app.test_client()

        response = client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Propose Topics", html)
        self.assertIn("Start Research and Drafting", html)

    def test_topics_search_auto_starts_multi_section_run_for_broad_query(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        routes.serper_google_search = lambda query, top_k=5: [
            {
                "title": f"Result {idx} for {query}",
                "link": f"https://example.com/{idx}",
                "snippet": f"Snippet {idx}",
                "domain": "example.com",
            }
            for idx in range(1, 7)
        ][:top_k]
        routes.record_search_event = lambda **_kwargs: True
        routes.save_job = lambda *_args, **_kwargs: None
        routes.create_episode = lambda *_args, **_kwargs: "ep_search"
        routes.enqueue_job = lambda *_args, **_kwargs: None

        app = build_test_app(routes)
        client = app.test_client()

        response = client.post(
            "/topics/search",
            data={
                "selected": "",
                "search_queries": "",
                "search_query": "predictive maintenance trends in manufacturing",
                "search_results_json": "[]",
            },
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("AI Specialists are working", html)
        self.assertNotIn("Search Results (Top 5)", html)
        self.assertTrue(
            any(
                job.get("topic_scope") == "search_auto_multi"
                and len(job.get("approved_topics", [])) <= 3
                and len(job.get("approved_topics", [])) >= 1
                and job.get("search_queries") == ["predictive maintenance trends in manufacturing"]
                for job in routes.JOBS.values()
            )
        )

    def test_topics_search_specific_query_runs_single_freeform_topic(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        routes.serper_google_search = lambda query, top_k=5: [
            {
                "title": f"Result {idx} for {query}",
                "link": f"https://example.com/{idx}",
                "snippet": f"Snippet {idx}",
                "domain": "example.com",
            }
            for idx in range(1, 7)
        ][:top_k]
        routes.record_search_event = lambda **_kwargs: True
        routes.save_job = lambda *_args, **_kwargs: None
        routes.create_episode = lambda *_args, **_kwargs: "ep_search_single"
        routes.enqueue_job = lambda *_args, **_kwargs: None

        app = build_test_app(routes)
        client = app.test_client()

        response = client.post(
            "/topics/search",
            data={
                "selected": "",
                "search_queries": "",
                "search_query": "notion ai pricing",
                "search_results_json": "[]",
            },
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("AI Specialists are working", html)
        self.assertTrue(
            any(
                job.get("topic_scope") == "search_auto_single"
                and len(job.get("approved_topics", [])) == 1
                and job.get("approved_topics", [])[0].get("type") == "freeform"
                and job.get("search_queries") == ["notion ai pricing"]
                for job in routes.JOBS.values()
            )
        )

    def test_propose_uses_learned_search_memory_without_live_queries(self):
        routes = load_routes_module(dynamic_enabled=True, freeform_enabled=False, candidate_count=5)
        planner = importlib.import_module("research_crew.topic_planner")

        original_loader = planner._load_recent_search_memory
        original_history_loader = planner.load_recent_topic_history
        try:
            planner._load_recent_search_memory = lambda **_kwargs: (
                ["robotic welding optimization"],
                {"robotic": 1.0, "welding": 0.95, "optimization": 0.9},
            )
            planner.load_recent_topic_history = lambda *args, **kwargs: {}
            catalog = routes.searchable_catalog(["tech_trends", "tech_discovery"])
            proposals, _ = planner.propose_topics(
                catalog,
                [],
                include_freeform=False,
                max_template_proposals=5,
            )
        finally:
            planner._load_recent_search_memory = original_loader
            planner.load_recent_topic_history = original_history_loader

        self.assertEqual(len(proposals), 5)
        rationale_text = " ".join(p.get("rationale", "") for p in proposals)
        self.assertTrue(
            "recent search interests" in rationale_text.lower()
            or "past search behavior" in rationale_text.lower()
        )


if __name__ == "__main__":
    unittest.main()
