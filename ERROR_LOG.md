# Error Log and Fix Guide

This file documents the main errors we’ve hit with this project, along with their root causes and the exact changes required to fix them. It’s meant as a practical reference for anyone setting up or maintaining the repo.

---

## 1. Gemini / CrewAI: `Invalid response from LLM call - None or empty`

### Symptom

- During a run (often on the **IT Hacks** topic), the research task fails with logs similar to:

  - `Received None or empty response from LLM call.`
  - `Error details: Invalid response from LLM call - None or empty.`
  - Task status ends as ❌ Failed for `research_task`.

### Root Cause (Part A): Research agent `reasoning=True` with Gemini

- File: `src/research_crew/crew.py`
- Original researcher agent was configured as:

  ```python
  return Agent(
      config=self.agents_config['researcher'],
      verbose=True,
      tools=[SerperDevTool()],
      cache=True,
      reasoning=True,
      max_reasoning_attempts=1,
      max_retry_limit=2,
      llm=researcher_llm,
  )
  ```

- `reasoning=True` activates CrewAI’s **AgentReasoning** flow. This flow was originally designed and tuned around OpenAI-style models; with the Gemini native provider it can occasionally lead to integration quirks (including empty or invalid intermediate responses).

#### Fix (Part A)

- Disable reasoning mode for the research agent so it uses a simpler, more direct execution path:

  ```python
  return Agent(
      config=self.agents_config['researcher'],  # type: ignore[index]
      verbose=True,
      tools=[SerperDevTool()],
      cache=True,
      # reasoning-mode disabled for Gemini
      max_retry_limit=2,
      llm=researcher_llm,
  )
  ```

- This removes one major source of instability when using Gemini with CrewAI agents.

### Root Cause (Part B): CrewAI helper treating empty Gemini response as fatal

Even after disabling reasoning, the same error still appeared. That pointed to a second, deeper issue inside CrewAI’s own utilities.

- File (inside venv): `.venv/Lib/site-packages/crewai/utilities/agent_utils.py`
- Function: `get_llm_response(...)`

Original implementation (simplified):

```python
def get_llm_response(... ) -> str:
    ...
    try:
        answer = llm.call(
            messages,
            callbacks=callbacks,
            from_task=from_task,
            from_agent=from_agent,
        )
    except Exception as e:
        raise e
    if not answer:
        printer.print(
            content="Received None or empty response from LLM call.",
            color="red",
        )
        raise ValueError("Invalid response from LLM call - None or empty.")

    return answer
```

- For some prompts, the Gemini native provider can return an **empty string** to this helper (even though a direct call with the same prompt works fine).
- `get_llm_response` then immediately raises `ValueError("Invalid response from LLM call - None or empty.")`, which is exactly what you see in the logs.

#### Fix (Part B): Gemini-specific fallback + retry

We patched `get_llm_response` to be tolerant of this edge case for Gemini/Google providers and perform **one extra, explicit retry** with a simplified follow-up prompt.

Key changes (conceptually):

```python
def get_llm_response(... ) -> str:
    ...
    try:
        answer = llm.call(
            messages,
            callbacks=callbacks,
            from_task=from_task,
            from_agent=from_agent,
        )
    except Exception as e:
        raise e

    # NEW: Gemini-specific fallback when answer is empty
    if not answer:
        printer.print(
            content=(
                "Received None or empty response from LLM call. "
                "Retrying once with a simplified prompt..."
            ),
            color="yellow",
        )

        provider = getattr(llm, "provider", "")
        if provider in ("gemini", "google"):
            fallback_messages = list(messages)
            fallback_messages.append(
                format_message_for_llm(
                    "The previous response was empty. Please now provide the full "
                    "final answer for the task above as plain text.",
                    role="assistant",
                )
            )
            try:
                answer = llm.call(
                    fallback_messages,
                    callbacks=callbacks,
                    from_task=from_task,
                    from_agent=from_agent,
                )
            except Exception as e:
                raise e

    if not answer:
        printer.print(
            content="Received None or empty response from LLM call.",
            color="red",
        )
        raise ValueError("Invalid response from LLM call - None or empty.")

    return str(answer)
```

Effect:

- For **non-Gemini** providers, behavior is unchanged.
- For Gemini:
  - If the first `llm.call(...)` returns an empty string, the helper:
    - Logs a yellow warning.
    - Appends a clear follow-up instruction and calls `llm.call(...)` once more.
  - Only if that second call is still empty does it raise the same error as before.

Note: this patch lives inside `.venv`. If you recreate or upgrade the virtualenv, you may need to re-apply it whenever this specific error reappears.

---

## 2. Ollama Connection Refused (WinError 10061)

### Symptom

- When using Ollama models (as per the original README), you may see:
  - `Connection refused` / `WinError 10061` when trying to reach `http://127.0.0.1:11434`.

### Root Cause

- Ollama server is not running, or it’s not reachable at the base URL configured in `.env` (`OLLAMA_API_BASE`).

### Fix

- Ensure Ollama is installed and running:
  - On Windows, the Ollama service usually runs automatically.
  - On macOS/Linux, make sure `ollama serve` is running or the service is installed.
- Verify connectivity:

  ```powershell
  Invoke-RestMethod http://127.0.0.1:11434/api/version
  ```

- Set `.env`:

  ```env
  OLLAMA_API_BASE=http://127.0.0.1:11434
  ```

---

## 3. Port 11434 Already in Use

### Symptom

- Error when trying to start `ollama serve`: port 11434 already in use.

### Root Cause

- The Ollama service is **already** running (common on Windows where it runs as a background service).

### Fix

- Do **not** start another Ollama instance.
- Just keep `OLLAMA_API_BASE=http://127.0.0.1:11434` and call the API; the existing service will handle requests.

---

## 4. Tracing 401 (`Trace batch initialization returned status 401`)

### Symptom

- Console prints something like:

  - `Trace batch initialization returned status 401. Continuing without tracing.`

### Root Cause

- `CREWAI_TRACING_ENABLED=true` but:
  - You’re not logged in to CrewAI+, or
  - The tracing token is missing/invalid.

### Fix

- Either:
  - Disable tracing in `.env`:

    ```env
    CREWAI_TRACING_ENABLED=false
    ```

  - Or log in / configure tracing according to the CrewAI+ docs.

This warning does **not** stop runs; it just disables tracing.

---

## 5. Serper (Web Search) Errors

### Symptom

- Tool failures when using the web search tool, e.g.:
  - API key errors
  - HTTP failures for the Serper API

### Root Cause

- `SERPER_API_KEY` is missing, invalid, or blocked by network/proxy.

### Fix

- Ensure `.env` has:

  ```env
  SERPER_API_KEY=your_serper_key_here
  ```

- Confirm:
  - The key is valid at https://serper.dev.
  - Your network allows outbound HTTPS to Serper’s API.

---

## 6. Gmail Send Fails

### Symptom

- Email sending step fails with SMTP or authentication errors.

### Root Cause

- Common issues:
  - Using a normal Gmail password instead of an **App Password**.
  - 2‑Step Verification not enabled.
  - SMTP/TLS blocked on the network.

### Fix

- In `.env`:

  ```env
  GMAIL_EMAIL=your.name@gmail.com
  GMAIL_APP_PASSWORD=your_gmail_app_password  # App Password, not your normal password
  GMAIL_SMTP_SERVER=smtp.gmail.com
  GMAIL_SMTP_PORT=587
  ```

- Ensure:
  - 2‑Step Verification is enabled for the account.
  - An App Password was generated and used.
  - Port 587/TLS is allowed by your network.

---

If you encounter a new error not covered here, consider:

- Checking `README.md` (Troubleshooting section).
- Verifying `.env` values and network access.
- Adding a new section to this `ERROR_LOG.md` describing the symptom, root cause, and fix so future readers benefit. 

---

## 7. Hugging Face CLI Tests: `Invalid credentials in Authorization header`

### Symptom

- PowerShell `curl` tests against Hugging Face returned:
  - `{"error":"Invalid credentials in Authorization header"}`
- Or, when using the old endpoint:
  - `{"error":"https://api-inference.huggingface.co is no longer supported. Please use https://router.huggingface.co/hf-inference instead."}`

### Root Cause

- Two separate issues:
  1. **Wrong endpoint** – the legacy `https://api-inference.huggingface.co` URL is deprecated.
  2. **Token scope** – the token was created with only repository `WRITE` permissions, not with **Inference → Make calls to Inference Providers**. Hugging Face rejected the call even when the key was pasted correctly.

### Fix

- Create a **fine‑grained access token** with at least:
  - `Inference → Make calls to Inference Providers`
- Update `.env`:

  ```env
  HUGGINGFACE_API_KEY=hf_...
  ```

- Use the Router endpoint for tests:

  ```powershell
  curl.exe -s -X POST `
    "https://router.huggingface.co/hf-inference/models/HuggingFaceH4/zephyr-7b-beta" `
    -H "Authorization: Bearer $env:HUGGINGFACE_API_KEY" `
    -H "Content-Type: application/json" `
    -d "{\"inputs\":\"Say hello in one sentence\"}"
  ```

---

## 8. CrewAI + LLM Factory: `Completions.create() got an unexpected keyword argument 'custom_llm_provider'`

### Symptom

- After first wiring Gemini via the LLM factory, runs failed with:

  ```text
  OpenAI API call failed: Completions.create() got an unexpected keyword argument 'custom_llm_provider'
  ```

### Root Cause

- The local `make_llm` factory returned:

  ```python
  LLM(
      model=model,
      api_key=api_key,
      custom_llm_provider="google",
      ...
  )
  ```

- For this CrewAI version, the **native** path routes to the OpenAI client (`Completions.create`), which does **not** accept `custom_llm_provider` as a kwarg. That argument is specific to LiteLLM’s API, not the OpenAI Python client.

### Fix

- Remove `custom_llm_provider` entirely and configure provider via model prefix / env vars instead:

  ```python
  return LLM(
      model=model,
      api_key=api_key,
      stream=False,
      temperature=temperature,
      timeout=timeout,
      max_tokens=max_tokens,
  )
  ```

- Provider selection is then handled by CrewAI’s `LLM` class, which looks at the `model` prefix (e.g., `gemini/...`) to pick the native Gemini provider.

---

## 9. CrewAI + LLM Factory: `Completions.create() got an unexpected keyword argument 'num_ctx'`

### Symptom

- After fixing `custom_llm_provider`, runs failed with:

  ```text
  OpenAI API call failed: Completions.create() got an unexpected keyword argument 'num_ctx'
  ```

### Root Cause

- The LLM factory still passed a local‑provider‑style context argument:

  ```python
  LLM(
      ...,
      max_tokens=max_tokens,
      num_ctx=num_ctx,  # <-- invalid for OpenAI/Gemini client
  )
  ```

- The underlying client (`Completions.create`) doesn’t know about `num_ctx` and rejects it.

### Fix

- Remove `num_ctx` from both the factory signature and call sites:

  ```python
  def make_llm(..., max_tokens: int = 800, temperature: float = 0.2, timeout: int = 900) -> LLM:
      ...
      return LLM(
          model=model,
          api_key=api_key,
          stream=False,
          temperature=temperature,
          timeout=timeout,
          max_tokens=max_tokens,
      )
  ```

- And update all calls in `crew.py` / `email_agent.py` to stop passing `num_ctx=...`.

---

## 10. Gemini Native Provider Missing: `Google Gen AI native provider not available`

### Symptom

- When switching to Gemini’s native provider, CrewAI raised:

  ```text
  Generation failed: Google Gen AI native provider not available, to install: uv add "crewai[google-genai]"
  ```

### Root Cause

- The environment had `crewai` installed but **not** the optional `google-genai` extras, so:
  - `from google import genai` failed in `crewai.llms.providers.gemini.completion`.

### Fix

- Inside the project’s virtual environment, install the Gemini extras:

  ```bash
  pip install "crewai[google-genai]"
  # or, if using uv:
  uv add "crewai[google-genai]"
  ```

- After installation, restart the venv and rerun the app.

---

## 11. Gemini 404: `models/gemini-1.5-flash is not found for API version v1beta`

### Symptom

- After wiring Gemini via the OpenAI‑compatible endpoint, runs failed with:

  ```text
  404 NOT_FOUND. {'error': {'code': 404, 'message': 'models/gemini-1.5-flash is not found for API version v1beta, or is not supported for generateContent.'}}
  ```

### Root Cause

- We were calling the **OpenAI‑compatible** Gemini endpoint (`.../v1beta/openai`) with a **native** model id:
  - `model="gemini-1.5-flash"`
- On that endpoint, Google expects alias names like `gpt-4.1-mini`, not the raw `gemini-*` ids, so the API returned 404.

### Fix

- Stop using the OpenAI‑compatible base URL and instead use CrewAI’s **native Gemini provider**:
  - Use `model="gemini/gemini-2.0-flash"` style ids (prefix `gemini/` added by the factory).
  - Let `GeminiCompletion` manage the `google.genai` client and endpoints.

---

## 12. Gemini 503: `The model is overloaded. Please try again later.`

### Symptom

- During IT Hacks runs with Gemini, logs showed:

  ```text
  503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The model is overloaded. Please try again later.', 'status': 'UNAVAILABLE'}}
  Google Gemini API error: 503 - The model is overloaded. Please try again later.
  ```

### Root Cause

- This is a **service‑side capacity issue**:
  - The Gemini model (first `gemini-2.5-*`, later `gemini-2.0-flash`) was temporarily overloaded for the project.
  - Requests were valid and authenticated; Gemini simply had no capacity at that moment.

### Mitigations

- Switched to lighter, more stable models:
  - Researcher: `gemini-2.0-flash`
  - Writer: `gemini-2.0-flash`
  - Editor: `gemini-2.0-flash`
  - Email: `gemini-flash-lite-latest`
- Recommended coding pattern (not yet implemented in repo, but advisable):
  - Wrap crew `kickoff` calls in a small retry loop that:
    - Catches 503/overload errors.
    - Sleeps briefly (e.g., 2–5 seconds).
    - Retries 1–2 times before surfacing an error to the user.

---

## 13. Gemini Empty Response: `Invalid response from LLM call - None or empty`

### Symptom

- Occasionally, after successful Serper searches, CrewAI reported:

  ```text
  Received None or empty response from LLM call.
  Error details: Invalid response from LLM call - None or empty.
  ```

### Root Cause

- The native Gemini provider (`GeminiCompletion`) sometimes returned an empty `response.text` (no textual candidate, no tool call chosen) for certain prompts.
- CrewAI’s helper treated this as a fatal error and raised `ValueError("Invalid response from LLM call - None or empty.")`.

### Mitigations / Notes

- This is not a wiring or env issue; it’s a **Gemini behavior** plus strict validation in CrewAI.
- Practical mitigations (to be implemented if it recurs often):
  - Add retry/backoff around `ResearchCrew().crew().kickoff(...)` in `app.py` when the error message contains:
    - `"model is overloaded"` or
    - `"Invalid response from LLM call - None or empty"`.
  - Optionally simplify prompts or reduce tool complexity for the affected agent to reduce the chance of empty responses.

---

## 14. Streamlit Newsletter UI: `ModuleNotFoundError: No module named 'research_crew'`

### Symptom

- When running the Streamlit UI with `app.py` in the **project root**, you may see:

  ```text
  ModuleNotFoundError: No module named 'research_crew'
  ```

### Root Cause

- The `research_crew` package lives under the `src/` layout and is not on `PYTHONPATH` when you run:
  - `streamlit run app.py` from the repo root with no extra path configuration.

### Fix

- Recommended layout is either:
  - Place the UI at `src/research_crew/app.py` and run:

    ```bash
    streamlit run src/research_crew/app.py
    ```

    (Python will resolve `research_crew.*` imports via the package.)

  - **Or** keep `app.py` at the repo root, but install the package in editable mode so `research_crew` is importable:

    ```bash
    pip install -e .
    streamlit run app.py
    ```

- In the current repo, we chose the first option: `src/research_crew/app.py`.

---

## 15. Newsletter Email Sending: `Gmail credentials missing in .env`

### Symptom

- Clicking **Send Email** in the UI causes the run to fail with a log like:

  ```text
  RuntimeError: Gmail credentials missing in .env
  ```

  (This comes from `send_email(...)` in `src/research_crew/email_task.py`.)

### Root Cause

- Required Gmail SMTP secrets are not set in the environment:
  - `GMAIL_EMAIL`
  - `GMAIL_APP_PASSWORD`

### Fix

- Add the following to your `.env` file (or environment) with real values:

  ```env
  GMAIL_EMAIL=your_address@gmail.com
  GMAIL_APP_PASSWORD=your_app_password
  GMAIL_SMTP_SERVER=smtp.gmail.com
  GMAIL_SMTP_PORT=587
  ```

- Make sure the Google account has:
  - 2‑step verification enabled.
  - An **App Password** created for “Mail” on “Windows”/“Other”.
- Restart the Streamlit app so it picks up the updated environment.

---

## 16. Wrong Python Command: `python run .\src\research_crew\main.py`

### Symptom

- From the repo root, running:

  ```powershell
  python run .\src\research_crew\main.py
  ```

  produced:

  ```text
  can't open file '...\research_crew\run': [Errno 2] No such file or directory
  ```

### Root Cause

- The word `run` was interpreted by Python as the **script name**, so it looked for a file literally called `run` in the current directory.
- There is no such file, so Python stopped before even loading `main.py`.

### Fix

- Call Python with just the script or module, without the stray `run`:

  ```powershell
  # Direct script
  python .\src\research_crew\main.py

  # Or as a module (preferred when using src/ layout)
  $env:PYTHONPATH="src"; python -m research_crew.main
  ```

---

## 17. `ModuleNotFoundError: No module named 'email_agent'`

### Symptom

- Running the script directly:

  ```powershell
  python .\src\research_crew\main.py
  ```

  raised:

  ```text
  ModuleNotFoundError: No module named 'email_agent'
  ```

### Root Cause

- `src/research_crew/main.py` originally imported:

  ```python
  from email_agent import create_email_agent
  from email_task import build_email_task
  ```

- Those modules lived at the **repo root**, not inside the `research_crew` package, and when running `src/research_crew/main.py` directly, the repo root was not on `sys.path`.

### Fix

- Move the email files into the package and import them via the package name:

  - Files moved to:
    - `src/research_crew/email_agent.py`
    - `src/research_crew/email_task.py`
  - Imports updated in `src/research_crew/main.py`:

    ```python
    from research_crew.email_agent import create_email_agent
    from research_crew.email_task import build_email_task
    ```

- With this change, running either

  ```powershell
  python .\src\research_crew\main.py
  ```

  or

  ```powershell
  $env:PYTHONPATH="src"; python -m research_crew.main
  ```

  works without the import error.

---

## 18. CrewAI + Ollama: `ImportError: Fallback to LiteLLM is not available`

### Symptom

- On the first attempt to use an Ollama-backed model from CrewAI, the run failed early with logs like:

  ```text
  LiteLLM is not available, falling back to LiteLLM
  Error instantiating LLM from string: Fallback to LiteLLM is not available
  ImportError: Fallback to LiteLLM is not available
  ```

### Root Cause

- The agents in `src/research_crew/config/agents.yaml` used model strings like `ollama/deepseek-r1:7b`.
- For those strings, CrewAI chose the **LiteLLM-based** code path, which requires the `litellm` Python package.
- The environment had `crewai` installed but **not** `litellm`, so importing/using that path raised the `ImportError`.

### Fix

- Install LiteLLM inside the virtual environment:

  ```bash
  pip install -U litellm
  ```

- After installing, re-running the crew no longer crashed on import; subsequent errors (timeouts, model runner issues) were from the **Ollama server**, not from missing dependencies.

---

## 19. CrewAI + Ollama: `litellm.Timeout: Connection timed out after 600.0 seconds`

### Symptom

- During the **IT Hacks** research task, after a long pause (~10 minutes), the run failed with:

  ```text
  litellm.exceptions.Timeout: litellm.Timeout: Connection timed out after 600.0 seconds.
  httpx.ReadTimeout: timed out
  ```

### Root Cause

- The request from LiteLLM to Ollama (`http://localhost:11434/api/generate`) did not return within 600 seconds.
- Common contributing factors:
  - Ollama server busy or stalled (model still loading, or previous run hung).
  - Heavy local model (e.g., 7B) on limited CPU/RAM causing extremely slow generation.
  - Network issues when talking to the local service (rare on localhost, but possible if the process died mid-request).

### Fix / Mitigations

- Verify Ollama health from a separate terminal:

  ```powershell
  Invoke-WebRequest http://127.0.0.1:11434/api/tags
  ```

- Test a minimal prompt directly:

  ```powershell
  curl http://127.0.0.1:11434/api/generate `
    -H "Content-Type: application/json" `
    -d "{\"model\":\"mistral\",\"prompt\":\"Say hi\"}"
  ```

- Reduce load in the app:
  - Switch to a lighter model in `src/research_crew/config/agents.yaml` (e.g., `ollama/mistral` or `ollama/phi3`).
  - Turn off extra reasoning passes and lower retry limits in `src/research_crew/crew.py` for the researcher.

- Once Ollama responds quickly to the simple test, re-run the newsletter crew.

---

## 20. Ollama 500: `model runner has unexpectedly stopped`

### Symptom

- After fixing timeouts, a later run failed with:

  ```text
  {"error":"model runner has unexpectedly stopped, this may be due to resource limitations or an internal error, check ollama server logs for details"}
  httpx.HTTPStatusError: Server error '500 Internal Server Error' for url 'http://localhost:11434/api/generate'
  litellm.APIConnectionError: OllamaException - {"error":"model runner has unexpectedly stopped, this may be due to resource limitations or an internal error, check ollama server logs for details"}
  ```

### Root Cause

- The request **did** reach Ollama, but the underlying model process crashed during generation.
- Typical causes on a laptop:
  - Not enough RAM/VRAM for the chosen model and context length.
  - OS or Ollama killing the model runner due to resource pressure.
  - Occasional internal Ollama bug.

### Fix / Mitigations

- Check Ollama logs to confirm the reason:

  ```powershell
  ollama serve   # run in a separate terminal to see live logs
  ```

- Free system resources:
  - Close browsers/heavy apps.
  - Ensure sufficient RAM and disk for swap.

- Use lighter models for crew tasks:
  - In `agents.yaml`, prefer `ollama/phi3` or `ollama/mistral` over heavier 7B+ models.

- If the error persists even with small models, reinstall or update Ollama, then re-test with a simple `curl` request before running the full crew.

---

## 21. `tiktoken` / LiteLLM: `MemoryError` loading `cl100k_base`

### Symptom

- On a subsequent run, the app failed **before** executing any crew logic with:

  ```text
  MemoryError
  ValueError: Error parsing line b'IHJld2FyZHM= 21845' in https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
  ```

- The traceback pointed into `tiktoken` being imported by LiteLLM:
  - `tiktoken.get_encoding("cl100k_base")`

### Root Cause

- `tiktoken` downloads and parses the `cl100k_base.tiktoken` BPE file into memory.
- On this machine, available RAM was very low at that moment, causing a `MemoryError` while decoding the file, which `tiktoken` re-raised as a `ValueError` referencing the problematic line.

### Fix

- Free up memory:
  - Close heavy applications (browsers, IDE windows, other dev tools).
  - Optionally increase the system page file / swap size.
- Restart the Python process (or reboot if needed), then re-run once more memory is available.
- After freeing RAM, the `tiktoken` import succeeded and the error no longer appeared.

---
