# Research Crew — Local, Multi‑Agent Newsletter Generator

This project uses CrewAI to research topics, draft content, edit it into production‑ready HTML, and optionally email a consolidated newsletter. It supports running fully locally with Ollama models.

Use this README when sharing the project as a ZIP. It walks through creating a fresh virtualenv, installing dependencies, configuring the `.env`, setting up Ollama, and running the crew.

---

**Quick Start**
- Create venv and activate it
- Install deps: `pip install -e .` (or `pip install "crewai[tools]==1.2.1" python-dotenv beautifulsoup4`)
- Install Ollama and pull models: `ollama pull phi3`, `ollama pull mistral`
- Copy `.env` values (see example below) and set Gmail App Password if you’ll send mail
- Confirm Ollama API: `Invoke-RestMethod http://127.0.0.1:11434/api/version`
- Run: Windows `set PYTHONPATH=src && python -m research_crew.main` (PowerShell: `$env:PYTHONPATH='src'; python -m research_crew.main`), macOS/Linux `PYTHONPATH=src python -m research_crew.main`
- Edit recipients in `src/research_crew/main.py` if needed

---

## 1) Prerequisites

- Python 3.10–3.13 (64‑bit)
- Git (optional, only if you plan to install with `pip install -e .`)
- Ollama installed and running locally (default port 11434)
  - Download: https://ollama.com
  - After install, ensure the service is running. On Windows the Ollama service runs automatically.
  - Pull the models used by this project:
    - `ollama pull phi3`
    - `ollama pull mistral`
- Internet access for web search (Serper API) and optional CrewAI tracing

Optional hosted LLMs (instead of Ollama) are supported via LiteLLM (e.g., OpenAI, Groq, Anthropic). See “Use a Hosted LLM” below.

---

## 2) Create And Activate A Virtualenv

From the unzipped project root:

Windows (PowerShell):

```
py -3.12 -m venv .venv
./.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
```

macOS/Linux:

```
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

---

## 3) Install Dependencies

This project declares its dependencies in `pyproject.toml` and uses CrewAI’s tools bundle.

Option A — Install this project (recommended):

```
pip install -e .
```

Option B — Minimal direct install (without installing the package):

```
pip install "crewai[tools]==1.2.1" python-dotenv beautifulsoup4
```

Note: Option A sets up console scripts and ensures all declared extras are installed. Option B is sufficient for running via `python -m` as shown below.

---

## 4) Configure Ollama

Make sure the API responds at `http://127.0.0.1:11434`.

Windows (PowerShell):

```
Invoke-RestMethod http://127.0.0.1:11434/api/version
ollama list
```

If the port is occupied, another Ollama instance/service is already running (expected on Windows). You do not need to run `ollama serve` manually.

---

## 5) Create And Fill Your .env

Copy `.env` (in the repo root) and set values. These are the important keys the project uses:

LLM and CrewAI
- `OLLAMA_API_BASE` — Base URL for the local Ollama server (recommended): `http://127.0.0.1:11434`
- `WRITER_MODEL` — Model for the email/writer agent, e.g. `ollama/mistral`
- `CREWAI_TRACING_ENABLED` — `true` or `false`. If true and not logged in to CrewAI+, you may see a 401 warning; runs still proceed.
- `LITELLM_TIMEOUT` — Global default timeout in seconds for LiteLLM. Some code paths set explicit timeouts.

Web Search
- `SERPER_API_KEY` — API key from https://serper.dev (required for the research agent’s web search).

Email Sending (optional)
- `GMAIL_EMAIL` — Your Gmail address (sender)
- `GMAIL_APP_PASSWORD` — Gmail App Password (not your regular password). Requires 2‑Step Verification.
- `GMAIL_SMTP_SERVER` — `smtp.gmail.com`
- `GMAIL_SMTP_PORT` — `587`

Security tips
- Do not commit `.env` to version control.
- Treat keys and app passwords as secrets; rotate if shared accidentally.

Example `.env`

```
# LLM + CrewAI
OLLAMA_API_BASE=http://127.0.0.1:11434
WRITER_MODEL=ollama/llama3.2:1b
RESEARCH_LLM=ollama/phi3
CREWAI_TRACING_ENABLED=false
LITELLM_TIMEOUT=180

# Web Search
SERPER_API_KEY=your_serper_key_here

# Gmail (for sending)
GMAIL_EMAIL=your.name@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
GMAIL_SMTP_SERVER=smtp.gmail.com
GMAIL_SMTP_PORT=587
```

---

## 6) Run The Crew

From the project root, with the virtualenv active:

Windows (PowerShell):

```
$env:PYTHONPATH = "src"
python -m research_crew.main
```

macOS/Linux:

```
PYTHONPATH=src python -m research_crew.main
```

What it does
- Iterates a set of topics (e.g., AI at Work, IT Hacks, O365 Updates)
- For each topic, the crew researches, writes, and edits a fully styled HTML page in `outputs/`
- Builds a consolidated email‑safe HTML at `outputs/newsletter_email_<Month>.html`
- Attempts to email the consolidated newsletter via Gmail if configured

Where to edit recipients
- Update the hard‑coded recipients and subject in `src/research_crew/main.py` (search for `recipients = [` and `subject =`).

Email sending notes
- Use a Gmail App Password (not your normal password) with 2‑Step Verification enabled.
- If you leave placeholder addresses like `example.com`, Gmail will bounce with “Address not found.” Replace with real emails.
- The email task sends both HTML and a plain‑text fallback for wide client compatibility.

---

## 7) Troubleshooting

Common issues and fixes

- Ollama connection refused (WinError 10061)
  - Ollama not running or bound to IPv4 only. Ensure the service is up and set `OLLAMA_API_BASE=http://127.0.0.1:11434`.
- Port 11434 already in use when running `ollama serve`
  - The service is already running (normal on Windows). Don’t start another instance.
- Long LLM calls or timeouts
  - Local models can be slow for large outputs. This project enables streaming and higher timeouts for the editor. If needed, reduce output size, lower `max_tokens`, or switch some agents to a smaller/faster model (e.g., `ollama/llama3.2:1b`).
- Tracing 401: “Trace batch initialization returned status 401. Continuing without tracing.”
  - You’re not logged in to CrewAI+ or the token is stale. Either set `CREWAI_TRACING_ENABLED=false` or run `crewai login`.
- Serper errors
  - Ensure `SERPER_API_KEY` is set and valid; check network/proxy settings if requests fail.
- Gmail send fails
  - Use a Gmail App Password (requires 2‑Step Verification). Ensure SMTP 587/TLS is allowed on your network.

Performance knobs
- Writer and editor are configured to stream with timeouts and token caps to avoid long stalls on local CPUs.
- If runs are still slow, try `WRITER_MODEL=ollama/llama3.2:1b` and lower `max_tokens` in `src/research_crew/crew.py`.

Useful checks
- `Invoke-RestMethod http://127.0.0.1:11434/api/version` (Windows) or `curl http://127.0.0.1:11434/api/version`
- `ollama list` to verify pulled models

---

**Run Each Month**
- Update recipients and subject (if needed) in `src/research_crew/main.py`.
- Activate venv and run the main module.
- Share the generated pages in `outputs/` and the consolidated `outputs/newsletter_email_<Month>.html`.
- Confirm the sent email in your Gmail “Sent” folder or watch console output for delivery logs.

---

## 8) Use A Hosted LLM (Optional)

If you prefer a hosted model instead of Ollama, set models to a provider supported by LiteLLM and add the provider’s API key. Examples:

OpenAI (example)

```
# .env
WRITER_MODEL=openai/gpt-4o-mini
OPENAI_API_KEY=sk-...
CREWAI_TRACING_ENABLED=false
```

You may also update `src/research_crew/config/agents.yaml` to use provider/model strings like `openai/gpt-4o-mini` for each agent.

---

## 9) Project Structure

- `src/research_crew/config/agents.yaml` — Agent roles and default LLMs
- `src/research_crew/config/tasks.yaml` — Task descriptions and expected output format
- `src/research_crew/crew.py` — Crew wiring and agent/task assembly
- `src/research_crew/main.py` — Orchestrates per‑topic runs and email consolidation
- `outputs/` — Generated HTML (per topic) and the consolidated newsletter

---

## 10) FAQ

Q: Do I need UV?
- No. You can use plain `pip` as shown above. If you prefer UV, it’s compatible with this project.

Q: Do I need to install the package with `pip install -e .`?
- It’s recommended but not mandatory. You can also just `pip install "crewai[tools]==1.2.1" python-dotenv` and run with `python -m research_crew.main` using `PYTHONPATH=src`.

Q: Can I disable the email step?
- Yes. Comment out the email block in `src/research_crew/main.py` or simply ignore the output email file and don’t configure Gmail.

---

Happy building! If you hit issues, check the Troubleshooting section first, then review your `.env` and Ollama setup.
---

## 11) Gemini / CrewAI Empty-Response Error

If you see errors like:

`Invalid response from LLM call - None or empty`

during a Gemini run (especially on the research task), it means CrewAI received an empty string back from an LLM call and treated it as a hard failure. With Gemini models this can occasionally happen even when the API itself is healthy.

This project works around it by patching CrewAI’s helper `get_llm_response` **inside the virtualenv** so it retries once with a simplified follow-up prompt for Gemini:

- File to patch: `.venv/Lib/site-packages/crewai/utilities/agent_utils.py`
- Function: `get_llm_response(...)`
- Before the final line that raises `ValueError("Invalid response from LLM call - None or empty.")`, add a Gemini-specific fallback that:
  - Logs a yellow warning to the console.
  - Checks `getattr(llm, "provider", "")` and, if it is `"gemini"` or `"google"`, appends a clarifying message like  
    `The previous response was empty. Please now provide the full final answer for the task above as plain text.`  
    to the `messages` list, then calls `llm.call(...)` once more.
  - Only if that second call is also empty should the function raise the same error.

Because this lives under `.venv`, if you recreate the environment or upgrade CrewAI you may need to re-apply this patch whenever you start seeing this specific error again.
