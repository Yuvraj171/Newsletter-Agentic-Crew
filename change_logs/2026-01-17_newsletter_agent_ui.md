# Change Log - 2026-01-17

Context: Newsletter agent UI (Flask/HTMX).

Checklist outcomes:
- Notification banner appears for each action and updates live.
- Buttons behave consistently and do not appear broken.

Summary of changes:
- Added a global notice banner fragment and live out-of-band updates for status/email actions.
- Centralized job readiness + notice logic to keep email and review states consistent.
- Refined review action messaging and reset email errors after approval.
- Fixed chip disabled styling and standardized disabled button styling.
- Regenerated January output HTML files for the latest run.

Files updated:
- src/research_crew/web/routes.py
- src/research_crew/web/templates/home.html
- src/research_crew/web/templates/fragments/notice.html (new)
- src/research_crew/web/templates/fragments/status.html
- src/research_crew/web/templates/fragments/review_section.html
- src/research_crew/web/templates/fragments/email_section.html
- src/research_crew/web/templates/fragments/topics.html
- src/research_crew/web/static/css/app.css
- outputs/it_hacks_January.html
- outputs/o365_updates_January.html
- outputs/tech_discovery_January.html
- outputs/tech_trends_January.html
- outputs/newsletter_email_January.html
