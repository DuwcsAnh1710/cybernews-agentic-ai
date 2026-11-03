"""Unit tests for GeminiClient multi-key rotation and failover (T3.3)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
import pytest
from pydantic import BaseModel

from cyberpulse.config import LLMConfig
from cyberpulse.llm import (
    GeminiClient,
    LLMAuthError,
    LLMRateLimitError,
    _sanitize,
    mask_api_key,
    parse_api_keys,
)


class DummySchema(BaseModel):
    ok: bool
    text: str


class MockSDKResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.candidates = [SimpleNamespace(finish_reason="STOP")]
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=15,
        )


class MockSDKClient:
    def __init__(self, exc: Exception | None = None, response_text: str | None = None) -> None:
        self.exc = exc
        self.response_text = response_text
        self.call_count = 0
        self.models = self._Models(self)

    class _Models:
        def __init__(self, parent: MockSDKClient) -> None:
            self.parent = parent

        def generate_content(self, *args: Any, **kwargs: Any) -> Any:
            self.parent.call_count += 1
            if self.parent.exc:
                raise self.parent.exc
            return MockSDKResponse(self.parent.response_text or '{"ok": true, "text": "hello"}')


def test_parse_api_keys_comma_delimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_FALLBACK", raising=False)

    keys = parse_api_keys(api_key="key_alpha, key_beta, key_gamma")
    assert keys == ["key_alpha", "key_beta", "key_gamma"]


def test_parse_api_keys_env_priority_and_deduplication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEYS", "key1\nkey2;key3")
    monkeypatch.setenv("GEMINI_API_KEY", "key2, key4")
    monkeypatch.setenv("GEMINI_API_KEY_FALLBACK", "key5, key1")

    keys = parse_api_keys()
    assert keys == ["key1", "key2", "key3", "key4", "key5"]


def test_mask_api_key() -> None:
    assert mask_api_key("") == "***"
    assert mask_api_key("short") == "***"
    assert mask_api_key("AIzaSy1234567890XYZ") == "AIza...0XYZ"


def test_sanitize_multiple_keys() -> None:
    text = "Error with key1: AIzaKey111 and key2: AIzaKey222 failed."
    sanitized = _sanitize(text, ["AIzaKey111", "AIzaKey222"])
    assert "AIzaKey111" not in sanitized
    assert "AIzaKey222" not in sanitized
    assert "Error with key1: *** and key2: *** failed." == sanitized


def test_multikey_rotation_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """When primary key hits 429 quota exhaustion, client rotates to backup key and succeeds."""
    class Error429(Exception):
        code = 429

    mock_client1 = MockSDKClient(exc=Error429("Quota limit 20 requests exceeded"))
    mock_client2 = MockSDKClient(response_text='{"ok": true, "text": "success from backup key"}')

    cfg = LLMConfig(max_retries=1, backoff_base_sec=0.01)
    client = GeminiClient(
        cfg=cfg,
        api_keys=["key_primary_123", "key_backup_456"],
        sdk_client={"key_primary_123": mock_client1, "key_backup_456": mock_client2},
        sleep=lambda s: None,
    )

    assert client.api_key == "key_primary_123"
    result = client.generate_json(
        system="sys",
        prompt="prompt",
        schema=DummySchema,
        task_name="test_rotation",
    )

    # Should have succeeded with backup key
    assert result.data.ok is True
    assert result.data.text == "success from backup key"
    assert mock_client1.call_count == 1
    assert mock_client2.call_count == 1
    assert client.api_key == "key_backup_456"


def test_multikey_rotation_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """When primary key fails auth (401), client rotates to backup key and succeeds."""
    class Error401(Exception):
        code = 401

    mock_client1 = MockSDKClient(exc=Error401("API key expired"))
    mock_client2 = MockSDKClient(response_text='{"ok": true, "text": "success from backup key"}')

    cfg = LLMConfig(max_retries=0)
    client = GeminiClient(
        cfg=cfg,
        api_keys=["bad_key", "good_key"],
        sdk_client={"bad_key": mock_client1, "good_key": mock_client2},
        sleep=lambda s: None,
    )

    result = client.generate_json(
        system="sys",
        prompt="prompt",
        schema=DummySchema,
        task_name="test_rotation_401",
    )

    assert result.data.ok is True
    assert result.data.text == "success from backup key"
    assert client.api_key == "good_key"


def test_multikey_all_exhausted_raises_ratelimit() -> None:
    """When all keys in the pool hit 429, LLMRateLimitError is raised."""
    class Error429(Exception):
        code = 429

    mock_client1 = MockSDKClient(exc=Error429("Key 1 exhausted"))
    mock_client2 = MockSDKClient(exc=Error429("Key 2 exhausted"))

    cfg = LLMConfig(max_retries=0)
    client = GeminiClient(
        cfg=cfg,
        api_keys=["key1", "key2"],
        sdk_client={"key1": mock_client1, "key2": mock_client2},
        sleep=lambda s: None,
    )

    with pytest.raises(LLMRateLimitError):
        client.generate_json(
            system="sys",
            prompt="prompt",
            schema=DummySchema,
            task_name="test_all_exhausted",
        )

    assert mock_client1.call_count == 1
    assert mock_client2.call_count == 1


def test_sentinel_keys_pool_never_leaked(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure no keys in the multi-key pool are leaked in logs or error messages."""
    sentinel_a = "AIzaTEST-KEY-ALPHA-1234"
    sentinel_b = "AIzaTEST-KEY-BETA-5678"

    class LeakingError(Exception):
        code = 401
        def __str__(self) -> str:
            return f"Unauthorized with keys {sentinel_a} and {sentinel_b}"

    caplog.set_level(logging.DEBUG)
    client = GeminiClient(
        cfg=LLMConfig(max_retries=0),
        api_keys=[sentinel_a, sentinel_b],
        sdk_client=MockSDKClient(exc=LeakingError()),
    )

    with pytest.raises(LLMAuthError) as exc_info:
        client.generate_json(
            system="sys",
            prompt="prompt",
            schema=DummySchema,
            task_name="sentinel_test",
        )

    assert sentinel_a not in str(exc_info.value)
    assert sentinel_b not in str(exc_info.value)
    assert sentinel_a not in caplog.text
    assert sentinel_b not in caplog.text
