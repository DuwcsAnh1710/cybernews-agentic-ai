"""Multi-channel Dispatch Engine for CyberPulse AI (Telegram & Discord Webhooks).

Provides deterministic delivery of editorial packages and breaking threat intelligence
to Telegram channels/chats and Discord webhooks, formatted natively for each platform.
Strictly adheres to zero-emoji and clean technical typography guidelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import html
import logging
import os
import re
from typing import Any
import httpx

from cyberpulse.models import EditorialPackage

logger = logging.getLogger("cyberpulse.dispatch")

DEFAULT_PORTAL_BASE_URL: str = "https://duwcsanh1710.github.io/cybernews-agentic-ai"


def format_run_date_vn(run_id: str) -> str:
    """Format run_id timestamp into Vietnam datetime string 'DD/MM/YYYY HH:MM'."""
    match = re.search(r"^(?:run|standalone)-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", run_id)
    if match:
        y, m, d, hh, mm, ss = match.groups()
        utc_dt = datetime(int(y), int(m), int(d), int(hh), int(mm), int(ss), tzinfo=timezone.utc)
        vn_dt = utc_dt.astimezone(timezone(timedelta(hours=7)))
        return vn_dt.strftime("%d/%m/%Y %H:%M")
    vn_now = datetime.now(timezone(timedelta(hours=7)))
    return vn_now.strftime("%d/%m/%Y %H:%M")


@dataclass
class ChannelResult:
    """Result of a dispatch operation to a single channel."""

    channel: str
    success: bool
    skipped: bool = False
    message_id: str | None = None
    error: str | None = None
    rendered_payload: str | dict[str, Any] | None = None


@dataclass
class DispatchResult:
    """Overall result of multi-channel dispatch."""

    run_id: str
    channel_results: list[ChannelResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def all_successful(self) -> bool:
        """Return True if all attempted channels succeeded (or were skipped)."""
        return all(r.success for r in self.channel_results)


def format_telegram_message(
    package: EditorialPackage,
    base_url: str = DEFAULT_PORTAL_BASE_URL,
) -> str:
    """Format editorial package into clean Telegram HTML message without emojis."""
    edition_url = f"{base_url.rstrip('/')}/editions/{package.run_id}.html"
    date_vn = format_run_date_vn(package.run_id)

    lines: list[str] = [
        f"<b>[CYBERPULSE INTEL] {html.escape(package.digest.title)}</b>",
        f"<i>Thời gian: {date_vn} (UTC+7)</i>",
        "",
        f"<i>{html.escape(package.digest.intro)}</i>",
        "",
        "<b>[TIN TIÊU ĐIỂM]</b>",
    ]

    for sec in package.digest.sections:
        snippet = sec.body.text.strip().replace("\n", " ")
        if len(snippet) > 180:
            snippet = snippet[:177].rstrip() + "..."
        lines.append(f"• <b>{html.escape(sec.heading)}</b>: {html.escape(snippet)}")

    lines.extend([
        "",
        f'<a href="{edition_url}">Đọc toàn bộ bài phân tích trên CyberPulse Reader</a>',
        "",
        "#CyberSecurity #ThreatIntel #AnNinhMang #CyberPulse",
    ])

    return "\n".join(lines)


def format_discord_payload(
    package: EditorialPackage,
    base_url: str = DEFAULT_PORTAL_BASE_URL,
) -> dict[str, Any]:
    """Format editorial package into rich Discord Embed payload without emojis."""
    edition_url = f"{base_url.rstrip('/')}/editions/{package.run_id}.html"
    date_vn = format_run_date_vn(package.run_id)

    fields: list[dict[str, Any]] = []
    for sec in package.digest.sections:
        snippet = sec.body.text.strip()
        if len(snippet) > 350:
            snippet = snippet[:347].rstrip() + "..."
        fields.append({
            "name": sec.heading[:256],
            "value": snippet[:1024],
            "inline": False,
        })

    embed = {
        "title": package.digest.title[:256],
        "url": edition_url,
        "description": f"**Thời gian:** {date_vn} (UTC+7)\n\n" + package.digest.intro[:1900],
        "color": 0x0369A1,  # CyberPulse Cyan / Primary Accent
        "fields": fields,
        "footer": {
            "text": f"CyberPulse AI Intelligence • Phiên bản: {package.run_id}",
        },
    }

    return {
        "embeds": [embed],
    }


def send_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
    client: httpx.Client | None = None,
) -> ChannelResult:
    """Send formatted text to a Telegram chat or channel."""
    if not bot_token or not chat_id:
        return ChannelResult(
            channel="telegram",
            success=True,
            skipped=True,
            error="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing",
        )

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    def _execute(c: httpx.Client) -> ChannelResult:
        try:
            resp = c.post(api_url, json=payload, timeout=15.0)
            data = resp.json()
            if resp.is_success and data.get("ok"):
                msg_id = str(data.get("result", {}).get("message_id", ""))
                logger.info("Dispatched Telegram message %s to chat %s", msg_id, chat_id)
                return ChannelResult(
                    channel="telegram",
                    success=True,
                    message_id=msg_id,
                    rendered_payload=text,
                )
            desc = data.get("description", resp.text)
            logger.error("Telegram API error: %s", desc)
            return ChannelResult(
                channel="telegram",
                success=False,
                error=desc,
                rendered_payload=text,
            )
        except Exception as exc:
            logger.error("Failed to dispatch to Telegram: %s", exc)
            return ChannelResult(
                channel="telegram",
                success=False,
                error=str(exc),
                rendered_payload=text,
            )

    if client is not None:
        return _execute(client)
    with httpx.Client() as c:
        return _execute(c)


def send_discord(
    webhook_url: str,
    payload: dict[str, Any],
    client: httpx.Client | None = None,
) -> ChannelResult:
    """Send JSON payload to a Discord webhook."""
    if not webhook_url:
        return ChannelResult(
            channel="discord",
            success=True,
            skipped=True,
            error="DISCORD_WEBHOOK_URL is missing",
        )

    def _execute(c: httpx.Client) -> ChannelResult:
        try:
            resp = c.post(webhook_url, json=payload, timeout=15.0)
            if resp.status_code in (200, 204):
                logger.info("Dispatched Discord webhook successfully (status %d)", resp.status_code)
                return ChannelResult(
                    channel="discord",
                    success=True,
                    rendered_payload=payload,
                )
            err_msg = f"HTTP {resp.status_code}: {resp.text}"
            logger.error("Discord Webhook error: %s", err_msg)
            return ChannelResult(
                channel="discord",
                success=False,
                error=err_msg,
                rendered_payload=payload,
            )
        except Exception as exc:
            logger.error("Failed to dispatch to Discord: %s", exc)
            return ChannelResult(
                channel="discord",
                success=False,
                error=str(exc),
                rendered_payload=payload,
            )

    if client is not None:
        return _execute(client)
    with httpx.Client() as c:
        return _execute(c)


def dispatch_package(
    package: EditorialPackage,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    discord_webhook_url: str | None = None,
    base_url: str = DEFAULT_PORTAL_BASE_URL,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> DispatchResult:
    """Dispatch an editorial package to all enabled channels."""
    token = telegram_token if telegram_token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = telegram_chat_id if telegram_chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
    webhook = discord_webhook_url if discord_webhook_url is not None else os.environ.get("DISCORD_WEBHOOK_URL", "")

    tg_text = format_telegram_message(package, base_url=base_url)
    dc_payload = format_discord_payload(package, base_url=base_url)

    results: list[ChannelResult] = []

    # 1. Telegram
    if dry_run:
        results.append(ChannelResult(
            channel="telegram",
            success=True,
            rendered_payload=tg_text,
        ))
    elif not token or not chat_id:
        results.append(ChannelResult(
            channel="telegram",
            success=True,
            skipped=True,
            error="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing",
        ))
    else:
        results.append(send_telegram(bot_token=token, chat_id=chat_id, text=tg_text, client=client))

    # 2. Discord
    if dry_run:
        results.append(ChannelResult(
            channel="discord",
            success=True,
            rendered_payload=dc_payload,
        ))
    elif not webhook:
        results.append(ChannelResult(
            channel="discord",
            success=True,
            skipped=True,
            error="DISCORD_WEBHOOK_URL missing",
        ))
    else:
        results.append(send_discord(webhook_url=webhook, payload=dc_payload, client=client))

    return DispatchResult(
        run_id=package.run_id,
        channel_results=results,
        dry_run=dry_run,
    )
