import logging
import os
from typing import Any

from crewai.llm import LLM

LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = [408, 429, 500, 502, 503, 504]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(
    name: str,
    default: int,
    *,
    min_value: int = 0,
    max_value: int | None = None,
) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r, using default=%s", name, raw, default)
        return default
    if value < min_value:
        return min_value
    if max_value is not None and value > max_value:
        return max_value
    return value


def _env_float(
    name: str,
    default: float,
    *,
    min_value: float = 0.0,
    max_value: float | None = None,
) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r, using default=%s", name, raw, default)
        return default
    if value < min_value:
        return min_value
    if max_value is not None and value > max_value:
        return max_value
    return value


def _normalize_model(model: str) -> str:
    if model.startswith("gemini/") or model.startswith("google/"):
        return model
    return f"gemini/{model}"


def _build_client_params(timeout_seconds: int) -> dict[str, Any]:
    timeout_ms = _env_int(
        "GEMINI_HTTP_TIMEOUT_MS",
        timeout_seconds * 1000,
        min_value=1000,
        max_value=600_000,
    )
    retry_enabled = _env_bool("GEMINI_HTTP_RETRY_ENABLED", True)
    http_options: dict[str, Any] = {"timeout": timeout_ms}

    if retry_enabled:
        http_options["retry_options"] = {
            "attempts": _env_int("GEMINI_HTTP_RETRY_ATTEMPTS", 5, min_value=1, max_value=10),
            "initial_delay": _env_float("GEMINI_HTTP_RETRY_INITIAL_DELAY", 1.0, min_value=0.1, max_value=30.0),
            "max_delay": _env_float("GEMINI_HTTP_RETRY_MAX_DELAY", 30.0, min_value=1.0, max_value=120.0),
            "exp_base": _env_float("GEMINI_HTTP_RETRY_EXP_BASE", 2.0, min_value=1.1, max_value=5.0),
            "jitter": _env_float("GEMINI_HTTP_RETRY_JITTER", 1.0, min_value=0.0, max_value=5.0),
            "http_status_codes": RETRYABLE_STATUS_CODES,
        }

    return {"http_options": http_options}


def make_llm(
    env_var: str,
    default_model: str,
    *,
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout: int = 900,
) -> LLM:
    """Create a Gemini-backed LLM instance with request-level backoff/retries."""
    model = _normalize_model(os.getenv(env_var, default_model))
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")

    # Ensure common env vars are populated for the native Gemini client
    os.environ.setdefault("GEMINI_API_KEY", api_key)
    os.environ.setdefault("GOOGLE_API_KEY", api_key)
    os.environ.pop("OPENAI_BASE_URL", None)

    client_params = _build_client_params(timeout)
    LOGGER.info(
        "LLM setup model=%s vertex=%s timeout_ms=%s retries=%s",
        model,
        os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {"1", "true", "yes", "on"},
        client_params["http_options"].get("timeout"),
        client_params["http_options"].get("retry_options", {}).get("attempts", 1),
    )

    return LLM(
        model=model,
        api_key=api_key,
        stream=False,
        temperature=temperature,
        timeout=timeout,
        max_output_tokens=max_tokens,
        client_params=client_params,
    )
