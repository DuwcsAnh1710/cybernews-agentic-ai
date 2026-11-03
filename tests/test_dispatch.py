"""Unit tests for Multi-Channel Dispatch Engine (Telegram & Discord Webhooks) (T8.2)."""

from __future__ import annotations

import httpx
import pytest

from tests.test_ui_data import make_sample_editorial
from cyberpulse.dispatch import (
    ChannelResult,
    DispatchResult,
    dispatch_package,
    format_discord_payload,
    format_run_date_vn,
    format_telegram_message,
    send_discord,
    send_telegram,
)


def test_format_run_date_vn() -> None:
    # 2026-11-03 10:00:00 UTC -> 17:00:00 UTC+7
    date_str = format_run_date_vn("run-20261103T100000Z-test01")
    assert date_str == "03/11/2026 17:00"

    # Fallback to current time if no pattern matched
    fallback = format_run_date_vn("invalid-run-id")
    assert len(fallback) == 16  # DD/MM/YYYY HH:MM


def test_format_telegram_message_contains_key_elements() -> None:
    pkg = make_sample_editorial("run-20261103T100000Z-test01")
    base_url = "https://example.com/portal"

    msg = format_telegram_message(pkg, base_url=base_url)

    # Must contain title
    assert pkg.digest.title in msg
    # Must contain Vietnam timestamp
    assert "03/11/2026 17:00" in msg
    assert "(UTC+7)" in msg
    # Must contain intro
    assert pkg.digest.intro in msg
    # Must contain portal link
    assert f"{base_url}/editions/{pkg.run_id}.html" in msg
    # Must contain section heading
    assert pkg.digest.sections[0].heading in msg
    # Must NOT contain generic AI emojis
    for forbidden in ["🎙️", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉"]:
        assert forbidden not in msg


def test_format_discord_payload_structure() -> None:
    pkg = make_sample_editorial("run-20261103T100000Z-test02")
    base_url = "https://example.com/portal"

    payload = format_discord_payload(pkg, base_url=base_url)

    assert "embeds" in payload
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert embed["title"] == pkg.digest.title
    assert embed["url"] == f"{base_url}/editions/{pkg.run_id}.html"
    assert "03/11/2026 17:00" in embed["description"]
    assert "(UTC+7)" in embed["description"]
    assert len(embed["fields"]) >= 1
    # Check NO emojis in fields
    for field in embed["fields"]:
        for forbidden in ["🎙️", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉"]:
            assert forbidden not in field["name"]
            assert forbidden not in field["value"]


def test_send_telegram_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "sendMessage" in str(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 12345}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_telegram(
        bot_token="test-token",
        chat_id="@test_channel",
        text="Hello world",
        client=client,
    )

    assert result.channel == "telegram"
    assert result.success is True
    assert result.message_id == "12345"
    assert result.error is None


def test_send_telegram_failure_handles_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Chat not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_telegram(
        bot_token="test-token",
        chat_id="@invalid_channel",
        text="Hello world",
        client=client,
    )

    assert result.channel == "telegram"
    assert result.success is False
    assert "Chat not found" in str(result.error)


def test_send_discord_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_discord(
        webhook_url="https://discord.com/api/webhooks/123/token",
        payload={"content": "test"},
        client=client,
    )

    assert result.channel == "discord"
    assert result.success is True
    assert result.error is None


def test_send_discord_failure_handles_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Unknown Webhook"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_discord(
        webhook_url="https://discord.com/api/webhooks/invalid",
        payload={"content": "test"},
        client=client,
    )

    assert result.channel == "discord"
    assert result.success is False
    assert "404" in str(result.error) or "Unknown Webhook" in str(result.error)


def test_dispatch_package_dry_run() -> None:
    pkg = make_sample_editorial("run-dry-001")
    result = dispatch_package(
        package=pkg,
        telegram_token="dummy-tok",
        telegram_chat_id="@dummy_chat",
        discord_webhook_url="https://discord.com/api/webhooks/dummy",
        dry_run=True,
    )

    assert result.dry_run is True
    assert len(result.channel_results) == 3
    channels = {cr.channel for cr in result.channel_results}
    assert channels == {"telegram", "discord", "email"}
    for cr in result.channel_results:
        assert cr.success is True
        assert cr.rendered_payload is not None


def test_dispatch_package_skips_unconfigured_channels() -> None:
    pkg = make_sample_editorial("run-skip-001")
    result = dispatch_package(
        package=pkg,
        telegram_token="",
        telegram_chat_id="",
        discord_webhook_url="",
        email_sender="",
        email_password="",
        email_recipients=[],
        dry_run=False,
    )

    assert len(result.channel_results) == 3
    for cr in result.channel_results:
        assert cr.skipped is True
        assert cr.success is True
