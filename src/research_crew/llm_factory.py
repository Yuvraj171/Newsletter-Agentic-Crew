from crewai.llm import LLM
import os


def make_llm(
    env_var: str,
    default_model: str,
    *,
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout: int = 900,
) -> LLM:
    """Create a Gemini-backed LLM instance for CrewAI.

    This uses CrewAI's *native* Gemini provider (not the OpenAI-compatible
    endpoint), so calls go directly through the GeminiCompletion wrapper.

    - Reads the model name from ``env_var`` (falls back to ``default_model``).
    - Uses ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) for authentication.
    - Ensures the model string has a ``gemini/`` prefix so the provider is
      correctly detected.
    """

    model = os.getenv(env_var, default_model)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")

    # Ensure common env vars are populated for the native Gemini client
    os.environ.setdefault("GEMINI_API_KEY", api_key)
    os.environ.setdefault("GOOGLE_API_KEY", api_key)
    # For safety, clear any conflicting OpenAI base overrides
    os.environ.pop("OPENAI_BASE_URL", None)

    # Ensure model prefix selects the native Gemini provider.
    # CrewAI's LLM looks at the portion before "/" to choose provider.
    if not (model.startswith("gemini/") or model.startswith("google/")):
        model = f"gemini/{model}"

    return LLM(
        model=model,                # e.g. "gemini/gemini-1.5-flash"
        api_key=api_key,
        stream=False,
        temperature=temperature,
        timeout=timeout,
        max_tokens=max_tokens,
    )
