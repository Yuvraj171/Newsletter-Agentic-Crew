# UI Migration Notes (Streamlit → Flask + Jinja + HTMX)

This file documents the 5-step UI transition, the before/after changes, and technical details for future updates. All subsequent UI changes should be noted here.

## 0) Original (Streamlit)
- Single-page Streamlit app in `src/research_crew/app.py`.
- Inline CSS, Streamlit widgets (multiselect, buttons, download buttons) for topic selection, status, preview, and send.
- No separation of layout/presentation; limited control over live async updates.

## 1) Scaffold Flask + Jinja + HTMX
- Added Flask app structure under `src/research_crew/web/` with `create_app` (app factory), blueprint, templates, static assets.
- Entry: `run_flask.py`.
- Base template: `templates/base.html` with CSS/JS includes and HTMX.
- Static: `static/css/app.css`, `static/js/app.js` (currently unused).

## 2) Base Layout + Design Tokens
- Dark theme tokens, cards, buttons, chips, progress, skeletons defined in `static/css/app.css`.
- Shell/grid in `home.html` using `{% extends "base.html" %}` and sections.
- Design goals: readable inactive states, softened glows, consistent spacing/typography.

## 3) Step 1 – Topic Chips + CTA (HTMX)
- Fragment: `templates/fragments/topics.html`.
- HTMX form posts to `/topics/toggle`; server returns updated fragment (chips toggle, CTA enables when selection non-empty).
- Data in `routes.py`: `TOPICS`, toggle endpoint, selection parsing with guard against unknown slugs.

## 4) Step 2 – Live Status Zone
- Fragment: `templates/fragments/status.html`.
- HTMX polling: `hx-get="/status"` with `hx-trigger="load, every 3s"` that stops when ready.
- Backend state: `STATUS` dict with `state` + `message`; `percent_complete` helper.
- Readiness derived from real output file: `outputs/newsletter_email_<Month>.html`. When present, `READY["html_ready"]` is set and non-failed topics marked `done`.
- Error/resilience: unknown topic slugs ignored; polling stops when ready. (Future: hook to real async job status and handle failures/retries explicitly.)

## 5) Step 3 – Review & Finalize (Gated)
- Fragment: `templates/fragments/review.html`.
- HTMX polling until ready; gated on `READY["html_ready"]` + all topics `done`.
- Shows skeleton when not ready; when ready, renders a snippet of the actual HTML (first ~1200 chars) and a download link.
- Toggle “I’ve reviewed the draft” enables Approve; Approve is a placeholder endpoint to wire to real send/publish logic.
- Download route: `/download/newsletter` serving the consolidated HTML.

## Current File Map (UI)
- `templates/base.html` – shell and static includes.
- `templates/home.html` – assembles topics, status, review fragments.
- `templates/fragments/topics.html` – Step 1 chips/CTA (HTMX).
- `templates/fragments/status.html` – Step 2 status/progress (HTMX polling).
- `templates/fragments/review.html` – Step 3 review/approve gating (HTMX polling).
- `static/css/app.css` – design tokens and components.
- `static/js/app.js` – empty; reserve for future JS beyond HTMX.
- `web/routes.py` – TOPICS, STATUS, READY state; endpoints for topics, status, review, download.

## Open TODOs / Future Enhancements
- Replace mock progress/state with real async job updates (hook to actual agent runs; handle failed/retry states and per-agent errors).
- Wire Approve to the real action (send/publish) and persist approval state.
- Add “no topics selected” guard on start of a real run.
- Optional UX: brief success state on Approve; retry/reset control to start a new run; richer preview (rendered iframe) once ready.
