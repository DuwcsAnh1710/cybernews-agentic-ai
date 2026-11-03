"""LLM client interface, Gemini integration, and Fake client for CyberPulse AI."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import re
import time
from typing import Any, Callable, Generic, Literal, Protocol, TypeVar
import httpx
from pydantic import BaseModel, ValidationError

from cyberpulse.config import LLMConfig, Settings

try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover
    genai = None  # type: ignore[assignment]
    genai_errors = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("cyberpulse.llm")


# ==============================================================================
# Exceptions
# ==============================================================================


class LLMError(Exception):
    """Base exception for all LLM errors."""


class LLMConfigError(LLMError):
    """Missing API key, unknown provider, or configuration error. Not retried."""


class LLMAuthError(LLMError):
    """401/403 or invalid API key credentials. Not retried."""


class LLMRateLimitError(LLMError):
    """429 or quota exhausted. Retried with backoff; raised after retries exhausted."""

    def __init__(self, message: str, retry_after_sec: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class LLMTransientError(LLMError):
    """Timeout, connection error, or 5xx server error. Retried with backoff."""


class LLMBlockedError(LLMError):
    """Response blocked by safety filters or empty candidate. Not retried."""


class LLMResponseError(LLMError):
    """Output is not valid JSON or fails Pydantic validation after schema_retries."""

    def __init__(self, message: str, last_error: str, raw_text_preview: str) -> None:
        super().__init__(message)
        self.last_error = last_error
        self.raw_text_preview = raw_text_preview


# ==============================================================================
# Data Structures & Protocol
# ==============================================================================


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    """Result of a validated structured LLM generation call."""

    data: T
    raw_text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    attempts: int
    latency_ms: int


class LLMClient(Protocol):
    """Protocol for structured JSON generation with an LLM."""

    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        task_name: str,
    ) -> LLMResult[T]:
        """Generate structured output validated against a Pydantic schema."""
        ...


def mask_api_key(key: str) -> str:
    """Mask API key for safe display (e.g. AIza...4a1b)."""
    if not key or len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def parse_api_keys(
    api_key: str | None = None,
    api_keys: list[str] | str | None = None,
) -> list[str]:
    """Extract ordered unique non-empty API keys from parameters and environment variables.

    Lookup priority:
    1. Explicit `api_keys` (list or comma/newline/semicolon delimited string)
    2. Explicit `api_key` (single or comma-delimited string)
    3. Environment variable `GEMINI_API_KEYS`
    4. Environment variable `GEMINI_API_KEY`
    5. Environment variable `GEMINI_API_KEY_FALLBACK`
    """
    raw: list[str] = []

    if api_keys is not None:
        if isinstance(api_keys, str):
            raw.extend(re.split(r"[,;\n\r]+", api_keys))
        elif isinstance(api_keys, (list, tuple)):
            for item in api_keys:
                if isinstance(item, str):
                    raw.extend(re.split(r"[,;\n\r]+", item))

    if api_key is not None:
        raw.extend(re.split(r"[,;\n\r]+", api_key))

    for env_name in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GEMINI_API_KEY_FALLBACK"):
        val = os.environ.get(env_name)
        if val and val.strip():
            raw.extend(re.split(r"[,;\n\r]+", val))

    seen: set[str] = set()
    cleaned: list[str] = []
    for k in raw:
        stripped = k.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            cleaned.append(stripped)

    return cleaned


def _sanitize(text: str, key: str | list[str] | tuple[str, ...] | None) -> str:
    """Scrub sensitive API key(s) from strings."""
    if not text or not key:
        return text
    keys = [key] if isinstance(key, str) else list(key)
    result = text
    for k in keys:
        if k and k in result:
            result = result.replace(k, "***")
    return result


# ==============================================================================
# Core Shared Execution Logic
# ==============================================================================


def _run_with_retry_and_validation(
    *,
    call_provider: Callable[[str], tuple[str, str, int | None, int | None]],
    system: str,
    prompt: str,
    schema: type[T],
    task_name: str,
    cfg: LLMConfig,
    sleep: Callable[[float], None],
    api_key: str | list[str] | tuple[str, ...] | None,
) -> LLMResult[T]:
    """Shared execution loop supporting transient retries and schema correction retries.

    call_provider(effective_prompt) -> (raw_text, model_name, in_tokens, out_tokens)
    """
    total_attempts = 0
    remaining_schema_retries = cfg.schema_retries
    current_prompt = prompt
    start_time = time.perf_counter()

    if logger.isEnabledFor(logging.DEBUG):
        preview_prompt = _sanitize(prompt[:300], api_key)
        logger.debug("[%s] Request prompt preview: %s", task_name, preview_prompt)

    while True:
        # Transient / Rate-limit retry loop for the current prompt
        transient_attempt = 0
        raw_text: str = ""
        model_name: str = ""
        in_tokens: int | None = None
        out_tokens: int | None = None

        while True:
            transient_attempt += 1
            total_attempts += 1

            try:
                raw_text, model_name, in_tokens, out_tokens = call_provider(current_prompt)
                break  # Call succeeded
            except (LLMRateLimitError, LLMTransientError) as exc:
                if transient_attempt <= cfg.max_retries:
                    delay = cfg.backoff_base_sec * (2 ** (transient_attempt - 1))
                    if isinstance(exc, LLMRateLimitError) and exc.retry_after_sec is not None:
                        if exc.retry_after_sec <= 60.0:
                            delay = max(delay, exc.retry_after_sec)
                    logger.warning(
                        "[%s] Transient error on attempt %d (%s). Backing off for %.2fs.",
                        task_name,
                        total_attempts,
                        exc.__class__.__name__,
                        delay,
                    )
                    sleep(delay)
                    continue
                else:
                    latency_ms = int((time.perf_counter() - start_time) * 1000)
                    logger.info(
                        "task_name=%s model=%s attempts=%d in_tokens=%s out_tokens=%s latency_ms=%d outcome=failed",
                        task_name,
                        model_name or getattr(cfg, "model", "unknown"),
                        total_attempts,
                        "unknown",
                        "unknown",
                        latency_ms,
                    )
                    raise

            except Exception:
                # Auth, Blocked, Config, or unexpected non-retryable errors
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                logger.info(
                    "task_name=%s model=%s attempts=%d in_tokens=%s out_tokens=%s latency_ms=%d outcome=failed",
                    task_name,
                    model_name or getattr(cfg, "model", "unknown"),
                    total_attempts,
                    "unknown",
                    "unknown",
                    latency_ms,
                )
                raise

        if logger.isEnabledFor(logging.DEBUG):
            preview_response = _sanitize(raw_text[:300], api_key)
            logger.debug("[%s] Response raw text preview: %s", task_name, preview_response)

        # Validate structured output
        try:
            validated_data = schema.model_validate_json(raw_text)
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.info(
                "task_name=%s model=%s attempts=%d in_tokens=%s out_tokens=%s latency_ms=%d outcome=ok",
                task_name,
                model_name,
                total_attempts,
                in_tokens if in_tokens is not None else "unknown",
                out_tokens if out_tokens is not None else "unknown",
                latency_ms,
            )
            return LLMResult(
                data=validated_data,
                raw_text=raw_text,
                model=model_name,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                attempts=total_attempts,
                latency_ms=latency_ms,
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as val_err:
            val_msg = _sanitize(str(val_err), api_key)
            if remaining_schema_retries > 0:
                remaining_schema_retries -= 1
                logger.warning(
                    "[%s] Schema validation failed on attempt %d: %s. Retrying with feedback note.",
                    task_name,
                    total_attempts,
                    val_msg[:200],
                )
                current_prompt = (
                    f"{prompt}\n\nYour previous output was invalid: {val_msg[:500]}. "
                    "Return ONLY JSON that matches the schema."
                )
                continue
            else:
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                logger.info(
                    "task_name=%s model=%s attempts=%d in_tokens=%s out_tokens=%s latency_ms=%d outcome=failed",
                    task_name,
                    model_name,
                    total_attempts,
                    in_tokens if in_tokens is not None else "unknown",
                    out_tokens if out_tokens is not None else "unknown",
                    latency_ms,
                )
                raise LLMResponseError(
                    message=f"Model output failed schema validation: {val_msg[:200]}",
                    last_error=val_msg,
                    raw_text_preview=raw_text[:500],
                ) from val_err


# ==============================================================================
# Gemini Client
# ==============================================================================


class GeminiClient:
    """Official Google Gen AI SDK client implementation with multi-key failover."""

    def __init__(
        self,
        cfg: LLMConfig,
        api_key: str | None = None,
        api_keys: list[str] | str | None = None,
        sdk_client: object | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.sleep = sleep
        self._sdk_client_override: object | None = None
        self._clients: dict[str, Any] = {}

        if sdk_client is not None:
            if isinstance(sdk_client, dict):
                self._clients = dict(sdk_client)
            else:
                self._sdk_client_override = sdk_client

        self.api_keys = parse_api_keys(api_key=api_key, api_keys=api_keys)
        if not self.api_keys:
            raise LLMConfigError("GEMINI_API_KEY is not set")

        self._active_key_index = 0

        env_model = os.environ.get("GEMINI_MODEL")
        self.model = env_model.strip() if env_model and env_model.strip() else cfg.model

    @property
    def api_key(self) -> str:
        """Currently active API key."""
        return self.api_keys[self._active_key_index]

    @property
    def active_key_index(self) -> int:
        """Zero-based index of currently active key."""
        return self._active_key_index

    def rotate_key(self) -> str:
        """Rotate to next available API key in the pool and return the new key."""
        old_masked = mask_api_key(self.api_key)
        self._active_key_index = (self._active_key_index + 1) % len(self.api_keys)
        new_masked = mask_api_key(self.api_key)
        logger.warning(
            "[GeminiClient] Rotated API key from %s to %s (key %d/%d in pool)",
            old_masked,
            new_masked,
            self._active_key_index + 1,
            len(self.api_keys),
        )
        return self.api_key

    def _get_client(self, key: str) -> Any:
        if self._sdk_client_override is not None:
            return self._sdk_client_override
        if key in self._clients:
            return self._clients[key]

        if genai is None:  # pragma: no cover
            raise LLMConfigError("google-genai package is not installed")

        new_client = genai.Client(
            api_key=key,
            http_options=genai_types.HttpOptions(
                timeout=self.cfg.timeout_sec * 1000
            ),
        )
        self._clients[key] = new_client
        return new_client

    def _call_gemini(
        self, prompt: str, system: str, schema: type[T]
    ) -> tuple[str, str, int | None, int | None]:
        keys_pool_size = len(self.api_keys)
        last_error: Exception | None = None
        response: Any = None

        for attempt_key_idx in range(keys_pool_size):
            active_key = self.api_key
            client = self._get_client(active_key)

            try:
                config = genai_types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=self.cfg.max_output_tokens,
                    thinking_config=genai_types.ThinkingConfig(
                        thinking_level=self.cfg.thinking_level
                    ),
                )
                response = client.models.generate_content(  # type: ignore[union-attr]
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                break
            except Exception as exc:
                try:
                    self._handle_sdk_exception(exc)
                except (LLMRateLimitError, LLMAuthError) as mapped_err:
                    last_error = mapped_err
                    if keys_pool_size > 1 and attempt_key_idx < keys_pool_size - 1:
                        self.rotate_key()
                        continue
                    raise mapped_err
                except Exception as other_err:
                    raise other_err
        else:
            if last_error:
                raise last_error

        # Check candidate safety and presence
        candidates = getattr(response, "candidates", None)
        if not candidates or len(candidates) == 0:
            raise LLMBlockedError("Response was empty: no candidates returned")

        first_cand = candidates[0]
        finish_reason = getattr(first_cand, "finish_reason", None)
        finish_reason_str = str(finish_reason).upper() if finish_reason else ""
        if "SAFETY" in finish_reason_str:
            raise LLMBlockedError("Response was blocked by safety filters")

        raw_text = getattr(response, "text", None)
        if raw_text is None:
            raise LLMBlockedError("Response contains no text content")

        usage = getattr(response, "usage_metadata", None)
        in_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        out_tokens = getattr(usage, "candidates_token_count", None) if usage else None

        return raw_text, self.model, in_tokens, out_tokens

    def _handle_sdk_exception(self, exc: Exception) -> None:
        """Map SDK errors to CyberPulse typed LLM exceptions."""
        # Check for APIError status code
        code = getattr(exc, "code", None)
        if code is None and hasattr(exc, "status_code"):
            code = getattr(exc, "status_code")

        msg = _sanitize(str(exc), self.api_keys)

        if code in (401, 403):
            raise LLMAuthError(f"Authentication failed ({code}): {msg}") from exc

        if code == 429:
            retry_after: float | None = None
            response_obj = getattr(exc, "response", None)
            if response_obj and hasattr(response_obj, "headers"):
                headers = response_obj.headers
                header_val = headers.get("retry-after") or headers.get("Retry-After")
                if header_val:
                    try:
                        retry_after = float(header_val)
                    except (ValueError, TypeError):
                        pass
            raise LLMRateLimitError(f"Rate limit exceeded (429): {msg}", retry_after_sec=retry_after) from exc

        if code in (408, 500, 502, 503, 504):
            raise LLMTransientError(f"Transient server error ({code}): {msg}") from exc

        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            raise LLMTransientError(f"Request timeout: {msg}") from exc

        if isinstance(exc, (httpx.NetworkError, ConnectionError)):
            raise LLMTransientError(f"Network connection error: {msg}") from exc

        # Check for safety / block keywords in message
        lower_msg = msg.lower()
        if "quota exceeded" in lower_msg or "resource_exhausted" in lower_msg:
            raise LLMRateLimitError(f"Quota exceeded: {msg}") from exc

        if "safety" in lower_msg or "blocked" in lower_msg:
            raise LLMBlockedError(f"Generation blocked: {msg}") from exc

        # Let other unhandled exceptions propagate
        raise exc

    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        task_name: str,
    ) -> LLMResult[T]:
        """Generate structured JSON using Gemini with bounded retries."""
        return _run_with_retry_and_validation(
            call_provider=lambda eff_prompt: self._call_gemini(eff_prompt, system, schema),
            system=system,
            prompt=prompt,
            schema=schema,
            task_name=task_name,
            cfg=self.cfg,
            sleep=self.sleep,
            api_key=self.api_keys,
        )


# ==============================================================================
# Fake LLM Client
# ==============================================================================


class FakeLLMClient:
    """Scripted fake client for unit and integration testing."""

    def __init__(
        self,
        responses: list[str | Exception],
        cfg: LLMConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.responses: list[str | Exception] = list(responses)
        self.cfg = cfg if cfg is not None else LLMConfig(provider="fake")
        self.sleep = sleep
        self.calls: list[dict[str, Any]] = []

    def _call_fake(
        self, prompt: str, system: str, schema: type[T], task_name: str
    ) -> tuple[str, str, int | None, int | None]:
        self.calls.append({
            "system": system,
            "prompt": prompt,
            "schema": schema.__name__,
            "task_name": task_name,
        })

        if not self.responses:
            raise AssertionError("FakeLLMClient: no scripted responses left")

        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item

        return item, "fake", None, None

    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        task_name: str,
    ) -> LLMResult[T]:
        """Generate structured JSON from scripted responses."""
        return _run_with_retry_and_validation(
            call_provider=lambda eff_prompt: self._call_fake(eff_prompt, system, schema, task_name),
            system=system,
            prompt=prompt,
            schema=schema,
            task_name=task_name,
            cfg=self.cfg,
            sleep=self.sleep,
            api_key=None,
        )


# ==============================================================================
# Factory
# ==============================================================================


def create_llm_client(settings: Settings) -> LLMClient:
    """Create an LLM client configured according to settings."""
    if settings.llm.provider == "gemini":
        return GeminiClient(cfg=settings.llm)
    elif settings.llm.provider == "fake":
        return FakeLLMClient(responses=[], cfg=settings.llm)
    else:
        raise LLMConfigError(f"Unknown LLM provider: '{settings.llm.provider}'")

