"""Unit tests for Automated Email Newsletter Dispatch (T8.5)."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from cyberpulse.dispatch import (
    dispatch_package,
    format_email_html,
    format_email_plain,
    send_email,
)
from tests.test_ui_data import make_sample_editorial


def test_format_email_plain_contains_required_elements() -> None:
    pkg = make_sample_editorial("run-20261103T120000Z-emlplain")
    base_url = "https://cyberpulseda.wuaze.com"

    plain = format_email_plain(pkg, base_url=base_url)

    assert "[CYBERPULSE INTEL]" in plain
    assert pkg.digest.title in plain
    assert "03/11/2026 19:00" in plain
    assert "(UTC+7)" in plain
    assert pkg.digest.intro in plain
    assert f"{base_url}/editions/{pkg.run_id}.html" in plain
    assert pkg.digest.sections[0].heading in plain

    # Zero-emoji invariant
    for forbidden in ["🎙️", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉", "🚀", "🔥"]:
        assert forbidden not in plain


def test_format_email_html_structure_and_styling() -> None:
    pkg = make_sample_editorial("run-20261103T120000Z-emlhtml")
    base_url = "https://cyberpulseda.wuaze.com"

    html = format_email_html(pkg, base_url=base_url)

    assert "<!DOCTYPE html>" in html
    assert '<meta name="viewport"' in html
    assert "#0F172A" in html  # Dark cybersecurity theme
    assert "#38BDF8" in html  # Neon sky accent
    assert pkg.digest.title in html
    assert pkg.digest.intro in html
    assert f"{base_url}/editions/{pkg.run_id}.html" in html
    assert "Đọc toàn bộ bài phân tích trên CyberPulse Reader" in html

    # Zero-emoji invariant
    for forbidden in ["🎙️", "🎧", "🟢", "🟡", "🔴", "⚠️", "✅", "📅", "🎉", "🚀", "🔥"]:
        assert forbidden not in html


def test_send_email_success_with_mock_smtp() -> None:
    mock_server = MagicMock()
    sender = "intel@cyberpulse.org"
    password = "secret-super-password"
    recipients = ["analyst1@sec.org", "analyst2@sec.org"]

    result = send_email(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        sender_email=sender,
        sender_password=password,
        recipients=recipients,
        subject="[CYBERPULSE INTEL] Daily Report",
        html_content="<p>Test HTML</p>",
        plain_content="Test Plain",
        use_tls=True,
        smtp_client=mock_server,
    )

    assert result.channel == "email"
    assert result.success is True
    assert result.skipped is False
    assert result.error is None
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with(sender, password)
    assert mock_server.sendmail.call_count == 1


def test_send_email_missing_credentials_skipped() -> None:
    result = send_email(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        sender_email="",
        sender_password="",
        recipients=[],
        subject="[CYBERPULSE INTEL] Test",
        html_content="<p>Test</p>",
        plain_content="Test",
    )

    assert result.channel == "email"
    assert result.success is True
    assert result.skipped is True
    assert "missing" in str(result.error)


def test_send_email_masks_password_in_errors() -> None:
    mock_server = MagicMock()
    password = "super_secret_smtp_token_12345"
    mock_server.login.side_effect = RuntimeError(f"Authentication failed for user: password was {password}")

    result = send_email(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        sender_email="intel@cyberpulse.org",
        sender_password=password,
        recipients=["analyst@sec.org"],
        subject="[CYBERPULSE INTEL] Test",
        html_content="<p>Test</p>",
        plain_content="Test",
        use_tls=False,
        smtp_client=mock_server,
    )

    assert result.channel == "email"
    assert result.success is False
    assert password not in result.error
    assert "********" in result.error


def test_dispatch_package_email_only() -> None:
    pkg = make_sample_editorial("run-email-only-001")
    result = dispatch_package(
        package=pkg,
        email_only=True,
        dry_run=True,
    )

    assert result.dry_run is True
    assert len(result.channel_results) == 1
    assert result.channel_results[0].channel == "email"
    assert result.channel_results[0].success is True
    assert "<!DOCTYPE html>" in result.channel_results[0].rendered_payload


def test_dispatch_package_email_channel_with_smtp_mock() -> None:
    mock_server = MagicMock()
    pkg = make_sample_editorial("run-email-mock-002")

    result = dispatch_package(
        package=pkg,
        email_sender="sender@example.com",
        email_password="topsecretpassword",
        email_recipients=["team@example.com"],
        smtp_client=mock_server,
        email_only=True,
        dry_run=False,
    )

    assert len(result.channel_results) == 1
    cr = result.channel_results[0]
    assert cr.channel == "email"
    assert cr.success is True
    assert cr.skipped is False
    mock_server.login.assert_called_once_with("sender@example.com", "topsecretpassword")
