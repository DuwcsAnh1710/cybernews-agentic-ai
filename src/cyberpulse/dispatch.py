"""Multi-channel Dispatch Engine for CyberPulse AI (Telegram & Discord Webhooks).

Provides deterministic delivery of editorial packages and breaking threat intelligence
to Telegram channels/chats and Discord webhooks, formatted natively for each platform.
Strictly adheres to zero-emoji and clean technical typography guidelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import logging
import os
from pathlib import Path
import re
import smtplib
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


def format_email_plain(
    package: EditorialPackage,
    base_url: str = DEFAULT_PORTAL_BASE_URL,
) -> str:
    """Format editorial package into clean plaintext email newsletter without emojis."""
    edition_url = f"{base_url.rstrip('/')}/editions/{package.run_id}.html"
    date_vn = format_run_date_vn(package.run_id)

    lines: list[str] = [
        f"[CYBERPULSE INTEL] {package.digest.title}",
        f"Thời gian: {date_vn} (UTC+7)",
        "=" * 60,
        "",
        package.digest.intro,
        "",
        "[TIN TIÊU ĐIỂM HÔM NAY]",
        "-" * 60,
    ]

    for idx, sec in enumerate(package.digest.sections, 1):
        lines.append(f"[{idx}] {sec.heading}")
        lines.append(sec.body.text.strip())
        lines.append("")

    lines.extend([
        "-" * 60,
        f"Đọc bản phân tích đầy đủ và chi tiết tại: {edition_url}",
        "",
        "CyberPulse AI • Tạp chí An ninh mạng Chuyên sâu",
        "#CyberSecurity #ThreatIntel #AnNinhMang #CyberPulse",
    ])

    return "\n".join(lines)


def format_email_html(
    package: EditorialPackage,
    base_url: str = DEFAULT_PORTAL_BASE_URL,
) -> str:
    """Format editorial package into responsive, email-client-compatible HTML newsletter."""
    edition_url = f"{base_url.rstrip('/')}/editions/{package.run_id}.html"
    date_vn = format_run_date_vn(package.run_id)
    safe_title = html.escape(package.digest.title)
    safe_intro = html.escape(package.digest.intro)

    sections_html = []
    for idx, sec in enumerate(package.digest.sections, 1):
        heading = html.escape(sec.heading)
        body = html.escape(sec.body.text.strip())
        sections_html.append(f"""
        <div style="margin-bottom: 20px; padding: 16px; background-color: #1E293B; border-radius: 8px; border-left: 4px solid #38BDF8;">
            <h3 style="margin: 0 0 8px 0; color: #F8FAFC; font-size: 16px; font-weight: 600;">
                [{idx}] {heading}
            </h3>
            <p style="margin: 0; color: #CBD5E1; font-size: 14px; line-height: 1.6;">
                {body}
            </p>
        </div>
        """)

    rendered_sections = "".join(sections_html)

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>[CYBERPULSE INTEL] {safe_title}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0F172A; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #E2E8F0;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #0F172A; padding: 24px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" style="max-width: 600px; margin: 0 auto; background-color: #0F172A; padding: 0 16px;">
                    <!-- Header -->
                    <tr>
                        <td style="padding-bottom: 24px; border-bottom: 1px solid #334155;">
                            <div style="font-size: 12px; font-weight: 700; color: #38BDF8; letter-spacing: 0.1em; text-transform: uppercase;">
                                [CYBERPULSE INTEL] THÔNG TIN AN NINH MẠNG HÀNG NGÀY
                            </div>
                            <div style="font-size: 13px; color: #94A3B8; margin-top: 4px;">
                                Thời gian: {date_vn} (UTC+7) • Phiên bản: {package.run_id}
                            </div>
                        </td>
                    </tr>
                    <!-- Title & Intro -->
                    <tr>
                        <td style="padding: 24px 0 16px 0;">
                            <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 700; color: #F8FAFC; line-height: 1.4;">
                                {safe_title}
                            </h1>
                            <p style="margin: 0; font-size: 15px; line-height: 1.6; color: #94A3B8; font-style: italic;">
                                {safe_intro}
                            </p>
                        </td>
                    </tr>
                    <!-- Sections -->
                    <tr>
                        <td style="padding-top: 8px;">
                            <h2 style="margin: 0 0 16px 0; font-size: 14px; font-weight: 700; color: #38BDF8; letter-spacing: 0.05em; text-transform: uppercase;">
                                [TIN TIÊU ĐIỂM]
                            </h2>
                            {rendered_sections}
                        </td>
                    </tr>
                    <!-- CTA Button -->
                    <tr>
                        <td align="center" style="padding: 24px 0;">
                            <a href="{edition_url}" style="display: inline-block; padding: 12px 28px; background-color: #0284C7; color: #FFFFFF; font-size: 14px; font-weight: 600; text-decoration: none; border-radius: 6px;">
                                Đọc toàn bộ bài phân tích trên CyberPulse Reader
                            </a>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="padding-top: 24px; border-top: 1px solid #334155; text-align: center; font-size: 12px; color: #64748B; line-height: 1.6;">
                            <p style="margin: 0 0 8px 0;">
                                Bản tin tổng hợp tự động bởi <strong>CyberPulse AI Agentic Platform</strong>.
                            </p>
                            <p style="margin: 0;">
                                #CyberSecurity #ThreatIntel #AnNinhMang #CyberPulse
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""


def send_email(
    smtp_host: str,
    smtp_port: int,
    sender_email: str,
    sender_password: str,
    recipients: list[str],
    subject: str,
    html_content: str,
    plain_content: str,
    use_tls: bool = True,
    smtp_client: Any | None = None,
) -> ChannelResult:
    """Send multipart HTML+Plain email newsletter via SMTP with secret masking."""
    if not sender_email or not sender_password or not recipients:
        return ChannelResult(
            channel="email",
            success=True,
            skipped=True,
            error="EMAIL_SENDER, EMAIL_PASSWORD or recipients missing",
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"CyberPulse Intelligence <{sender_email}>"
    msg["To"] = ", ".join(recipients)

    part1 = MIMEText(plain_content, "plain", "utf-8")
    part2 = MIMEText(html_content, "html", "utf-8")
    msg.attach(part1)
    msg.attach(part2)

    def _execute(server: Any) -> ChannelResult:
        try:
            if use_tls:
                server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())
            logger.info("Dispatched Email newsletter to %d recipients via %s:%d", len(recipients), smtp_host, smtp_port)
            return ChannelResult(
                channel="email",
                success=True,
                rendered_payload=html_content,
            )
        except Exception as exc:
            sanitized_err = str(exc).replace(sender_password, "********") if sender_password else str(exc)
            logger.error("Failed to dispatch Email newsletter: %s", sanitized_err)
            return ChannelResult(
                channel="email",
                success=False,
                error=sanitized_err,
                rendered_payload=html_content,
            )

    if smtp_client is not None:
        return _execute(smtp_client)

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20.0) as server:
                return _execute(server)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20.0) as server:
                return _execute(server)
    except Exception as exc:
        sanitized_err = str(exc).replace(sender_password, "********") if sender_password else str(exc)
        logger.error("Failed to connect to SMTP server %s:%d: %s", smtp_host, smtp_port, sanitized_err)
        return ChannelResult(
            channel="email",
            success=False,
            error=sanitized_err,
            rendered_payload=html_content,
        )


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


def send_telegram_video(
    bot_token: str,
    chat_id: str,
    video_path: Path | str,
    caption: str = "",
    client: httpx.Client | None = None,
) -> ChannelResult:
    """Send an MP4 video file or video URL to a Telegram chat or channel."""
    if not bot_token or not chat_id:
        return ChannelResult(
            channel="telegram_video",
            success=True,
            skipped=True,
            error="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing",
        )

    v_path = (
        Path(video_path)
        if isinstance(video_path, str) and not (video_path.startswith("http://") or video_path.startswith("https://"))
        else video_path
    )
    api_url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    def _execute(c: httpx.Client) -> ChannelResult:
        try:
            if isinstance(v_path, Path) and v_path.is_file():
                with open(v_path, "rb") as f:
                    files = {"video": (v_path.name, f, "video/mp4")}
                    data = {"chat_id": chat_id, "parse_mode": "HTML"}
                    if caption:
                        data["caption"] = caption[:1024]
                    resp = c.post(api_url, data=data, files=files, timeout=60.0)
            elif isinstance(v_path, str) and (v_path.startswith("http://") or v_path.startswith("https://")):
                payload = {
                    "chat_id": chat_id,
                    "video": v_path,
                    "parse_mode": "HTML",
                }
                if caption:
                    payload["caption"] = caption[:1024]
                resp = c.post(api_url, json=payload, timeout=30.0)
            else:
                return ChannelResult(
                    channel="telegram_video",
                    success=False,
                    error=f"Video file does not exist: {video_path}",
                )

            data = resp.json()
            if resp.is_success and data.get("ok"):
                msg_id = str(data.get("result", {}).get("message_id", ""))
                logger.info("Dispatched Telegram video %s to chat %s", msg_id, chat_id)
                return ChannelResult(
                    channel="telegram_video",
                    success=True,
                    message_id=msg_id,
                    rendered_payload=caption,
                )
            desc = data.get("description", resp.text)
            logger.error("Telegram API video error: %s", desc)
            return ChannelResult(
                channel="telegram_video",
                success=False,
                error=desc,
                rendered_payload=caption,
            )
        except Exception as exc:
            logger.error("Failed to dispatch video to Telegram: %s", exc)
            return ChannelResult(
                channel="telegram_video",
                success=False,
                error=str(exc),
                rendered_payload=caption,
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
    video_path: Path | str | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    discord_webhook_url: str | None = None,
    email_sender: str | None = None,
    email_password: str | None = None,
    email_recipients: list[str] | str | None = None,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_use_tls: bool = True,
    email_subject_prefix: str = "[CYBERPULSE INTEL]",
    base_url: str = DEFAULT_PORTAL_BASE_URL,
    dry_run: bool = False,
    client: httpx.Client | None = None,
    smtp_client: Any | None = None,
    telegram_only: bool = False,
    discord_only: bool = False,
    email_only: bool = False,
) -> DispatchResult:
    """Dispatch an editorial package to all enabled channels (Telegram, Discord, Email)."""
    token = telegram_token if telegram_token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = telegram_chat_id if telegram_chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
    webhook = discord_webhook_url if discord_webhook_url is not None else os.environ.get("DISCORD_WEBHOOK_URL", "")

    sender = email_sender if email_sender is not None else os.environ.get("EMAIL_SENDER", "")
    pwd = email_password if email_password is not None else os.environ.get("EMAIL_PASSWORD", "")

    recipients_raw = email_recipients if email_recipients is not None else os.environ.get("EMAIL_RECIPIENTS", "")
    if isinstance(recipients_raw, str):
        recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    elif isinstance(recipients_raw, (list, tuple)):
        recipients = [str(r).strip() for r in recipients_raw if str(r).strip()]
    else:
        recipients = []

    tg_text = format_telegram_message(package, base_url=base_url)
    dc_payload = format_discord_payload(package, base_url=base_url)
    em_plain = format_email_plain(package, base_url=base_url)
    em_html = format_email_html(package, base_url=base_url)
    email_subject = f"{email_subject_prefix.strip()} {package.digest.title}"

    results: list[ChannelResult] = []

    # 1. Telegram
    if not discord_only and not email_only:
        target_vid = video_path
        if target_vid is None:
            candidate_vid = Path("output") / "media" / "video" / f"{package.run_id}.mp4"
            if candidate_vid.is_file():
                target_vid = candidate_vid

        if dry_run:
            results.append(ChannelResult(
                channel="telegram",
                success=True,
                rendered_payload=tg_text,
            ))
            if target_vid:
                results.append(ChannelResult(
                    channel="telegram_video",
                    success=True,
                    rendered_payload=f"Video brief for {package.run_id}",
                ))
        elif not token or not chat_id:
            results.append(ChannelResult(
                channel="telegram",
                success=True,
                skipped=True,
                error="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing",
            ))
        else:
            tg_res = send_telegram(bot_token=token, chat_id=chat_id, text=tg_text, client=client)
            results.append(tg_res)
            if target_vid and tg_res.success:
                vid_caption = (
                    f"<b>[CYBERPULSE VIDEO BRIEF 9:16]</b>\n"
                    f"{package.digest.title}\n\n"
                    f"Bản tin an ninh mạng định dạng video ngắn 9:16."
                )
                tg_vid_res = send_telegram_video(
                    bot_token=token,
                    chat_id=chat_id,
                    video_path=target_vid,
                    caption=vid_caption,
                    client=client,
                )
                results.append(tg_vid_res)

    # 2. Discord
    if not telegram_only and not email_only:
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

    # 3. Email Newsletter
    if not telegram_only and not discord_only:
        if dry_run:
            results.append(ChannelResult(
                channel="email",
                success=True,
                rendered_payload=em_html,
            ))
        elif not sender or not pwd or not recipients:
            results.append(ChannelResult(
                channel="email",
                success=True,
                skipped=True,
                error="EMAIL_SENDER, EMAIL_PASSWORD or recipients missing",
            ))
        else:
            results.append(send_email(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                sender_email=sender,
                sender_password=pwd,
                recipients=recipients,
                subject=email_subject,
                html_content=em_html,
                plain_content=em_plain,
                use_tls=smtp_use_tls,
                smtp_client=smtp_client,
            ))

    return DispatchResult(
        run_id=package.run_id,
        channel_results=results,
        dry_run=dry_run,
    )
