"""Offline unit tests for CyberPulse LLM client (Gemini + Fake client)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
import pytest
from pydantic import BaseModel

from cyberpulse.config import ConfigError, LLMConfig, Settings, load_settings
from cyberpulse.llm import (
    FakeLLMClient,
    GeminiClient,
    LLMAuthError,
    LLMBlockedError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTransientError,
    create_llm_client,
)


class Echo(BaseModel):
    """Tiny schema used for testing structured generation."""

    ok: bool
    n: int


class RecordedSleep:
    """Helper to record sleep durations without actual delay."""

    def __init__(self) -> None:
        self.durations: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)


class FakeSDKResponse:
    """Mock Gemini SDK response."""

    def __init__(
        self,
        text: str | None,
        in_tokens: int | None = 10,
        out_tokens: int | None = 20,
        finish_reason: str = "STOP",
        has_candidates: bool = True,
    ) -> None:
        self.text = text
        if has_candidates:
            self.candidates = [SimpleNamespace(finish_reason=finish_reason)]
        else:
            self.candidates = []
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=in_tokens,
            candidates_token_count=out_tokens,
        )


class FakeSDKClient:
    """Mock Gemini SDK Client."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.responses = list(responses) if responses is not None else []
        self.exc = exc
        self.recorded_calls: list[dict[str, Any]] = []

    class _Models:
        def __init__(self, parent: FakeSDKClient) -> None:
            self.parent = parent

        def generate_content(
            self,
            model: str,
            contents: str,
            config: Any,
        ) -> Any:
            self.parent.recorded_calls.append({
                "model": model,
                "contents": contents,
                "config": config,
            })
            if self.parent.exc:
                raise self.parent.exc
            if self.parent.responses:
                item = self.parent.responses.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            raise AssertionError("FakeSDKClient: no responses left")

    @property
    def models(self) -> _Models:
        return self._Models(self)


# ==============================================================================
# Test Cases 1 - 9: FakeLLMClient
# ==============================================================================


def test_1_fake_valid_json() -> None:
    """Test 1: Fake valid JSON -> data.ok True, attempts 1."""
    client = FakeLLMClient(responses=['{"ok": true, "n": 42}'])
    result = client.generate_json(
        system="System prompt",
        prompt="User prompt",
        schema=Echo,
        task_name="test_echo",
    )
    assert result.data.ok is True
    assert result.data.n == 42
    assert result.attempts == 1
    assert result.model == "fake"
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert len(client.calls) == 1
    assert client.calls[0]["task_name"] == "test_echo"


def test_2_fake_not_json_then_valid() -> None:
    """Test 2: Fake 'not json' then valid -> success, attempts 2; second prompt contains validation note."""
    client = FakeLLMClient(
        responses=["not json", '{"ok": true, "n": 100}'],
        cfg=LLMConfig(schema_retries=1),
    )
    result = client.generate_json(
        system="System",
        prompt="User prompt",
        schema=Echo,
        task_name="test_retry",
    )
    assert result.data.ok is True
    assert result.data.n == 100
    assert result.attempts == 2
    assert len(client.calls) == 2
    assert "Your previous output was invalid" in client.calls[1]["prompt"]
    assert "User prompt" in client.calls[1]["prompt"]


def test_3_fake_invalid_twice_with_schema_retries_1() -> None:
    """Test 3: Fake invalid twice with schema_retries=1 -> LLMResponseError with last_error and preview."""
    client = FakeLLMClient(
        responses=["invalid json 1", "invalid json 2"],
        cfg=LLMConfig(schema_retries=1),
    )
    with pytest.raises(LLMResponseError) as exc_info:
        client.generate_json(
            system="System",
            prompt="User prompt",
            schema=Echo,
            task_name="test_fail",
        )
    err = exc_info.value
    assert "invalid json 2" in err.raw_text_preview
    assert len(err.last_error) > 0


def test_4_fake_json_with_wrong_field_type() -> None:
    """Test 4: Fake JSON with wrong field type ('n': 'abc') -> treated as validation failure and retried."""
    client = FakeLLMClient(
        responses=['{"ok": true, "n": "abc"}', '{"ok": true, "n": 77}'],
        cfg=LLMConfig(schema_retries=1),
    )
    result = client.generate_json(
        system="System",
        prompt="User prompt",
        schema=Echo,
        task_name="test_field_type",
    )
    assert result.data.n == 77
    assert result.attempts == 2
    assert "Your previous output was invalid" in client.calls[1]["prompt"]


def test_5_fake_transient_error_backoff() -> None:
    """Test 5: Fake LLMTransientError x 2, valid with max_retries=2 -> success, attempts 3, backoff [2.0, 4.0]."""
    sleeper = RecordedSleep()
    client = FakeLLMClient(
        responses=[
            LLMTransientError("timeout 1"),
            LLMTransientError("timeout 2"),
            '{"ok": true, "n": 10}',
        ],
        cfg=LLMConfig(max_retries=2, backoff_base_sec=2.0),
        sleep=sleeper,
    )
    result = client.generate_json(
        system="System",
        prompt="Prompt",
        schema=Echo,
        task_name="test_transient",
    )
    assert result.data.n == 10
    assert result.attempts == 3
    assert sleeper.durations == [2.0, 4.0]


def test_6_fake_rate_limit_retries_exhausted() -> None:
    """Test 6: Fake LLMRateLimitError x 3 with max_retries=2 -> LLMRateLimitError raised, attempts 3."""
    sleeper = RecordedSleep()
    client = FakeLLMClient(
        responses=[
            LLMRateLimitError("rate limited 1"),
            LLMRateLimitError("rate limited 2"),
            LLMRateLimitError("rate limited 3"),
        ],
        cfg=LLMConfig(max_retries=2, backoff_base_sec=2.0),
        sleep=sleeper,
    )
    with pytest.raises(LLMRateLimitError):
        client.generate_json(
            system="System",
            prompt="Prompt",
            schema=Echo,
            task_name="test_ratelimit",
        )
    assert sleeper.durations == [2.0, 4.0]


def test_7_rate_limit_with_retry_after() -> None:
    """Test 7: Rate limit with retry_after_sec=10 -> sleep uses 10.0."""
    sleeper = RecordedSleep()
    client = FakeLLMClient(
        responses=[
            LLMRateLimitError("rate limited", retry_after_sec=10.0),
            '{"ok": true, "n": 99}',
        ],
        cfg=LLMConfig(max_retries=2, backoff_base_sec=2.0),
        sleep=sleeper,
    )
    result = client.generate_json(
        system="System",
        prompt="Prompt",
        schema=Echo,
        task_name="test_retry_after",
    )
    assert result.data.n == 99
    assert sleeper.durations == [10.0]


def test_8_auth_and_blocked_errors_not_retried() -> None:
    """Test 8: LLMAuthError and LLMBlockedError -> raised immediately, sleep never called."""
    sleeper = RecordedSleep()
    client_auth = FakeLLMClient(
        responses=[LLMAuthError("Invalid key")],
        cfg=LLMConfig(max_retries=2),
        sleep=sleeper,
    )
    with pytest.raises(LLMAuthError):
        client_auth.generate_json(
            system="System", prompt="Prompt", schema=Echo, task_name="auth"
        )
    assert len(sleeper.durations) == 0

    client_blocked = FakeLLMClient(
        responses=[LLMBlockedError("Safety filter")],
        cfg=LLMConfig(max_retries=2),
        sleep=sleeper,
    )
    with pytest.raises(LLMBlockedError):
        client_blocked.generate_json(
            system="System", prompt="Prompt", schema=Echo, task_name="blocked"
        )
    assert len(sleeper.durations) == 0


def test_9_fake_empty_script_raises_assertion_error() -> None:
    """Test 9: Fake with empty script -> AssertionError."""
    client = FakeLLMClient(responses=[])
    with pytest.raises(AssertionError, match="FakeLLMClient: no scripted responses left"):
        client.generate_json(
            system="System", prompt="Prompt", schema=Echo, task_name="empty"
        )


# ==============================================================================
# Test Cases 10 - 15: GeminiClient & Configuration
# ==============================================================================


def test_10_gemini_client_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 10: GeminiClient with no key -> LLMConfigError, message does not contain any key."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError) as exc_info:
        GeminiClient(cfg=LLMConfig())
    msg = str(exc_info.value)
    assert "GEMINI_API_KEY is not set" in msg


def test_11_gemini_client_injected_sdk_request_and_tokens() -> None:
    """Test 11: GeminiClient with injected fake SDK verifies parameters and token mapping."""
    fake_sdk = FakeSDKClient(
        responses=[
            FakeSDKResponse('{"ok": true, "n": 123}', in_tokens=18, out_tokens=32)
        ]
    )
    cfg = LLMConfig(
        model="gemini-3.8-flash",
        thinking_level="medium",
        max_output_tokens=4096,
    )
    client = GeminiClient(
        cfg=cfg,
        api_key="mock-test-key",
        sdk_client=fake_sdk,
    )
    result = client.generate_json(
        system="Test instruction",
        prompt="Test input",
        schema=Echo,
        task_name="test_gemini_params",
    )
    assert result.data.ok is True
    assert result.data.n == 123
    assert result.input_tokens == 18
    assert result.output_tokens == 32
    assert result.attempts == 1
    assert result.model == "gemini-3.8-flash"

    # Verify request arguments passed to the SDK
    assert len(fake_sdk.recorded_calls) == 1
    call = fake_sdk.recorded_calls[0]
    assert call["model"] == "gemini-3.8-flash"
    assert call["contents"] == "Test input"

    config_passed = call["config"]
    assert config_passed.system_instruction == "Test instruction"
    assert config_passed.response_mime_type == "application/json"
    assert config_passed.response_schema == Echo
    assert config_passed.max_output_tokens == 4096
    assert str(config_passed.thinking_config.thinking_level).upper() in ("MEDIUM", "THINKINGLEVEL.MEDIUM")


def test_12_gemini_client_sdk_exception_mapping() -> None:
    """Test 12: GeminiClient maps status codes 429 / 401 / 503 to RateLimit / Auth / Transient."""
    sleeper = RecordedSleep()
    cfg = LLMConfig(max_retries=0)

    # 429 RateLimit
    class MockError429(Exception):
        code = 429
        response = SimpleNamespace(headers={"retry-after": "7.5"})

    client_429 = GeminiClient(
        cfg=cfg,
        api_key="mock-key",
        sdk_client=FakeSDKClient(exc=MockError429("Quota exceeded")),
        sleep=sleeper,
    )
    with pytest.raises(LLMRateLimitError) as exc_429:
        client_429.generate_json(system="s", prompt="p", schema=Echo, task_name="t")
    assert exc_429.value.retry_after_sec == 7.5

    # 401 Auth
    class MockError401(Exception):
        code = 401

    client_401 = GeminiClient(
        cfg=cfg,
        api_key="mock-key",
        sdk_client=FakeSDKClient(exc=MockError401("Bad key")),
        sleep=sleeper,
    )
    with pytest.raises(LLMAuthError):
        client_401.generate_json(system="s", prompt="p", schema=Echo, task_name="t")

    # 503 Transient
    class MockError503(Exception):
        code = 503

    client_503 = GeminiClient(
        cfg=cfg,
        api_key="mock-key",
        sdk_client=FakeSDKClient(exc=MockError503("Service unavailable")),
        sleep=sleeper,
    )
    with pytest.raises(LLMTransientError):
        client_503.generate_json(system="s", prompt="p", schema=Echo, task_name="t")


def test_13_sentinel_key_never_leaked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test 13: Sentinel key AIzaTEST-SENTINEL-123 never appears in logs or exception messages."""
    sentinel = "AIzaTEST-SENTINEL-123"

    class LeakingSDKError(Exception):
        code = 401
        def __str__(self) -> str:
            return f"Unauthorized access with credential {sentinel} failed"

    caplog.set_level(logging.DEBUG)
    client = GeminiClient(
        cfg=LLMConfig(max_retries=0),
        api_key=sentinel,
        sdk_client=FakeSDKClient(exc=LeakingSDKError()),
    )

    with pytest.raises(LLMAuthError) as exc_info:
        client.generate_json(
            system=f"Secret system with {sentinel}",
            prompt=f"Secret prompt with {sentinel}",
            schema=Echo,
            task_name="sentinel_test",
        )

    # Check exception text
    assert sentinel not in str(exc_info.value)
    assert "***" in str(exc_info.value)

    # Check logs
    assert sentinel not in caplog.text


def test_14_gemini_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 14: GEMINI_MODEL env overrides [llm].model."""
    monkeypatch.setenv("GEMINI_MODEL", "gemini-custom-override")
    cfg = LLMConfig(model="gemini-3.8-flash")
    client = GeminiClient(cfg=cfg, api_key="test-key", sdk_client=FakeSDKClient([]))
    assert client.model == "gemini-custom-override"


def test_15_invalid_llm_values_raise_config_error(tmp_path: Any) -> None:
    """Test 15: Invalid [llm] values (thinking_level, max_retries, timeout_sec) raise ConfigError."""
    # Invalid thinking_level
    invalid_toml_1 = tmp_path / "settings_bad_thinking.toml"
    invalid_toml_1.write_text(
        """
        project_name = "CyberPulse"
        topics = ["security"]
        [llm]
        thinking_level = "max"
        """,
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(invalid_toml_1)

    # Invalid max_retries
    invalid_toml_2 = tmp_path / "settings_bad_retries.toml"
    invalid_toml_2.write_text(
        """
        project_name = "CyberPulse"
        topics = ["security"]
        [llm]
        max_retries = 9
        """,
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(invalid_toml_2)

    # Invalid timeout_sec
    invalid_toml_3 = tmp_path / "settings_bad_timeout.toml"
    invalid_toml_3.write_text(
        """
        project_name = "CyberPulse"
        topics = ["security"]
        [llm]
        timeout_sec = 1
        """,
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(invalid_toml_3)


def test_create_llm_client_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify create_llm_client factory instantiates appropriate client."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-factory-key")
    settings_gemini = Settings(
        project_name="CyberPulse",
        topics=["cyber"],
        llm=LLMConfig(provider="gemini"),
    )
    client_gemini = create_llm_client(settings_gemini)
    assert isinstance(client_gemini, GeminiClient)

    settings_fake = Settings(
        project_name="CyberPulse",
        topics=["cyber"],
        llm=LLMConfig(provider="fake"),
    )
    client_fake = create_llm_client(settings_fake)
    assert isinstance(client_fake, FakeLLMClient)