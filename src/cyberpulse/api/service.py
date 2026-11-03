"""Service layer for CyberPulse AI Threat Intelligence API."""

from __future__ import annotations

from datetime import datetime, timezone
import email.utils
import html
import json
import logging
from pathlib import Path
from typing import Any
import xml.sax.saxutils

from cyberpulse import __version__
from cyberpulse.api.models import (
    AnalyzedArticle,
    ArticleSourceInfo,
    ArticleThreatBadge,
    CveThreatItem,
    EditionDetailResponse,
    EditionSummary,
    MediaLinks,
)
from cyberpulse.config import Settings
from cyberpulse.models import Article, EditorialPackage, ResearchResult
from cyberpulse.portal import (
    detect_threat_badge,
    extract_cves,
    extract_vendor_tags,
    format_vietnam_time,
    parse_run_timestamp,
)
from cyberpulse.storage import connect, get_db_path, load_editorial_package, load_research_result

logger = logging.getLogger("cyberpulse.api.service")


class ThreatIntelService:
    """Core domain service for threat intelligence querying and syndication."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.output_dir = Path(settings.paths.output_dir)
        self.data_dir = Path(settings.paths.data_dir)

    def _get_editorial_files(self) -> list[tuple[Path, datetime, str]]:
        """Return list of (file_path, dt, run_id) sorted descending by timestamp."""
        editorial_dir = self.output_dir / "editorial"
        results: list[tuple[Path, datetime, str]] = []
        if not editorial_dir.exists() or not editorial_dir.is_dir():
            return results

        for path in editorial_dir.glob("*.json"):
            if path.name.endswith(".original.json"):
                continue
            run_id = path.stem
            dt = parse_run_timestamp(run_id, path)
            results.append((path, dt, run_id))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def _resolve_media_path(self, run_id: str, media_type: str) -> Path | None:
        """Resolve actual filesystem path for requested media asset."""
        if media_type == "audio":
            candidates = [
                self.output_dir / "media" / "podcast" / f"{run_id}.mp3",
                self.output_dir / "export" / run_id / "podcast.mp3",
                Path("export") / run_id / "podcast.mp3",
            ]
        elif media_type == "audio_srt":
            candidates = [
                self.output_dir / "media" / "podcast" / f"{run_id}.srt",
                self.output_dir / "export" / run_id / "podcast.srt",
                Path("export") / run_id / "podcast.srt",
            ]
        elif media_type == "video":
            candidates = [
                self.output_dir / "media" / "video" / f"{run_id}.mp4",
                self.output_dir / "export" / run_id / "video.mp4",
                Path("export") / run_id / "video.mp4",
            ]
        elif media_type == "video_srt":
            candidates = [
                self.output_dir / "media" / "video" / f"{run_id}.srt",
                self.output_dir / "export" / run_id / "video.srt",
                Path("export") / run_id / "video.srt",
            ]
        else:
            return None

        for c in candidates:
            if c.exists() and c.is_file():
                return c
        return None

    def get_health(self) -> dict[str, Any]:
        """Collect system health, timestamp, and count of stored editions."""
        files = self._get_editorial_files()
        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc),
            "editions_count": len(files),
            "version": __version__,
            "service": "CyberPulse AI Threat Intelligence API",
        }

    def get_editions(
        self,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[int, list[EditionSummary]]:
        """Retrieve paginated list of editions ordered newest first."""
        files = self._get_editorial_files()
        total = len(files)
        paginated_files = files[offset : offset + limit]

        items: list[EditionSummary] = []
        for path, dt, run_id in paginated_files:
            try:
                raw_text = path.read_text(encoding="utf-8")
                pkg = EditorialPackage.model_validate_json(raw_text)

                has_audio = self._resolve_media_path(pkg.run_id, "audio") is not None
                has_video = self._resolve_media_path(pkg.run_id, "video") is not None

                items.append(
                    EditionSummary(
                        run_id=pkg.run_id,
                        title=pkg.digest.title,
                        published_at=dt,
                        summary=pkg.digest.intro,
                        language=pkg.language,
                        article_count=len(pkg.digest.sections),
                        has_podcast=has_audio,
                        has_video=has_video,
                        detail_url=f"/api/v1/editions/{pkg.run_id}",
                    )
                )
            except Exception as err:
                logger.warning("Error reading editorial package from %s: %s", path, err)

        return total, items

    def _load_package(self, run_id: str) -> tuple[EditorialPackage | None, datetime]:
        """Load editorial package by run_id from disk or SQLite fallback."""
        pkg_file = self.output_dir / "editorial" / f"{run_id}.json"
        if pkg_file.exists() and pkg_file.is_file():
            try:
                pkg = EditorialPackage.model_validate_json(pkg_file.read_text(encoding="utf-8"))
                dt = parse_run_timestamp(pkg.run_id, pkg_file)
                return pkg, dt
            except Exception as err:
                logger.warning("Failed parsing package file %s: %s", pkg_file, err)

        # Database fallback
        db_path = get_db_path(self.settings)
        if db_path.exists():
            try:
                conn = connect(db_path)
                try:
                    pkg = load_editorial_package(conn, run_id)
                    if pkg:
                        dt = parse_run_timestamp(pkg.run_id)
                        return pkg, dt
                finally:
                    conn.close()
            except Exception as err:
                logger.warning("Database fallback failed for run_id %s: %s", run_id, err)

        return None, datetime.fromtimestamp(0, tz=timezone.utc)

    def _load_research_articles(self, run_id: str) -> dict[str, Article]:
        """Load research articles lookup map by article_id."""
        article_lookup: dict[str, Article] = {}
        research_file = self.output_dir / "research" / f"{run_id}.json"
        if research_file.exists() and research_file.is_file():
            try:
                res = ResearchResult.model_validate_json(research_file.read_text(encoding="utf-8"))
                for item in res.items:
                    article_lookup[item.article.id] = item.article
                return article_lookup
            except Exception as err:
                logger.warning("Failed parsing research file %s: %s", research_file, err)

        # Database fallback
        db_path = get_db_path(self.settings)
        if db_path.exists():
            try:
                conn = connect(db_path)
                try:
                    res = load_research_result(conn, run_id)
                    if res:
                        for item in res.items:
                            article_lookup[item.article.id] = item.article
                finally:
                    conn.close()
            except Exception as err:
                logger.warning("Database research lookup failed for %s: %s", run_id, err)

        return article_lookup

    def get_edition_detail(self, run_id: str) -> EditionDetailResponse | None:
        """Fetch detailed edition representation including analyzed articles and media links."""
        pkg, dt = self._load_package(run_id)
        if pkg is None:
            return None

        article_lookup = self._load_research_articles(pkg.run_id)

        # Media links
        has_audio = self._resolve_media_path(pkg.run_id, "audio") is not None
        has_audio_srt = self._resolve_media_path(pkg.run_id, "audio_srt") is not None
        has_video = self._resolve_media_path(pkg.run_id, "video") is not None
        has_video_srt = self._resolve_media_path(pkg.run_id, "video_srt") is not None

        media = MediaLinks(
            audio_mp3_url=f"/api/v1/editions/{pkg.run_id}/media/audio" if has_audio else None,
            audio_srt_url=f"/api/v1/editions/{pkg.run_id}/media/audio/srt" if has_audio_srt else None,
            video_mp4_url=f"/api/v1/editions/{pkg.run_id}/media/video" if has_video else None,
            video_srt_url=f"/api/v1/editions/{pkg.run_id}/media/video/srt" if has_video_srt else None,
            has_audio=has_audio,
            has_video=has_video,
        )

        # Analyzed articles
        analyzed_articles: list[AnalyzedArticle] = []
        for section in pkg.digest.sections:
            combined_text = f"{section.heading} {section.body.text}"
            cves = extract_cves(combined_text)
            badge_info = detect_threat_badge(section.body.text)
            vendors = extract_vendor_tags(combined_text, topics=self.settings.topics)

            sources: list[ArticleSourceInfo] = []
            for aid in section.body.cited_article_ids:
                if aid in article_lookup:
                    art = article_lookup[aid]
                    sources.append(
                        ArticleSourceInfo(
                            id=art.id,
                            source_name=art.source_name,
                            title=art.title,
                            url=art.url,
                            published_at=art.published_at,
                            summary=art.summary,
                            topics_matched=art.topics_matched,
                        )
                    )
                else:
                    sources.append(
                        ArticleSourceInfo(
                            id=aid,
                            source_name="Unknown Source",
                            title=f"Source Article {aid[:8]}",
                            url="#",
                        )
                    )

            analyzed_articles.append(
                AnalyzedArticle(
                    heading=section.heading,
                    text=section.body.text,
                    cves=cves,
                    threat_badge=ArticleThreatBadge(
                        label=badge_info.label,
                        css_class=badge_info.css_class,
                        text=badge_info.text,
                    ),
                    vendors=vendors,
                    cited_article_ids=section.body.cited_article_ids,
                    sources=sources,
                )
            )

        return EditionDetailResponse(
            run_id=pkg.run_id,
            title=pkg.digest.title,
            summary=pkg.digest.intro,
            published_at=dt,
            language=pkg.language,
            media=media,
            articles=analyzed_articles,
            podcast_script=pkg.podcast.model_dump() if pkg.podcast else None,
            video_script=pkg.video.model_dump() if pkg.video else None,
            captions=[c.model_dump() for c in pkg.captions],
        )

    def get_latest_edition(self) -> EditionDetailResponse | None:
        """Retrieve most recent edition report."""
        files = self._get_editorial_files()
        if not files:
            return None
        latest_run_id = files[0][2]
        return self.get_edition_detail(latest_run_id)

    def get_media_file(self, run_id: str, media_type: str) -> Path | None:
        """Return path to media asset if present."""
        return self._resolve_media_path(run_id, media_type)

    def get_threat_radar(
        self,
        vendor: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[CveThreatItem]]:
        """Aggregate CVE indicators across published editions with filtering."""
        files = self._get_editorial_files()
        cve_map: dict[str, dict[str, Any]] = {}

        for path, dt, run_id in files:
            try:
                raw_text = path.read_text(encoding="utf-8")
                pkg = EditorialPackage.model_validate_json(raw_text)
                article_lookup = self._load_research_articles(pkg.run_id)

                for section in pkg.digest.sections:
                    combined_text = f"{section.heading} {section.body.text}"
                    cves = extract_cves(combined_text)
                    if not cves:
                        continue

                    badge = detect_threat_badge(section.body.text)
                    vendors = extract_vendor_tags(combined_text, topics=self.settings.topics)

                    ref_urls: list[str] = []
                    for aid in section.body.cited_article_ids:
                        if aid in article_lookup and article_lookup[aid].url:
                            ref_urls.append(article_lookup[aid].url)

                    for cve in cves:
                        if cve not in cve_map:
                            cve_map[cve] = {
                                "cve_id": cve,
                                "risk_level": badge.text,
                                "severity_badge": badge.label,
                                "affected_vendors": set(vendors),
                                "first_seen": dt,
                                "last_seen": dt,
                                "editions": {pkg.run_id},
                                "headlines": {section.heading},
                                "references": set(ref_urls),
                            }
                        else:
                            entry = cve_map[cve]
                            entry["affected_vendors"].update(vendors)
                            entry["editions"].add(pkg.run_id)
                            entry["headlines"].add(section.heading)
                            entry["references"].update(ref_urls)
                            if dt < entry["first_seen"]:
                                entry["first_seen"] = dt
                            if dt > entry["last_seen"]:
                                entry["last_seen"] = dt
                                # Update badge to latest severity seen
                                entry["risk_level"] = badge.text
                                entry["severity_badge"] = badge.label
            except Exception as err:
                logger.warning("Error processing radar data for %s: %s", path, err)

        # Convert to list and apply filters
        items: list[CveThreatItem] = []
        for cve_id, data in cve_map.items():
            vendors_list = sorted(list(data["affected_vendors"]))
            editions_list = sorted(list(data["editions"]), reverse=True)
            headlines_list = sorted(list(data["headlines"]))
            references_list = sorted(list(data["references"]))

            # Vendor filter
            if vendor and not any(vendor.lower() in v.lower() for v in vendors_list):
                continue

            # Severity filter
            if severity:
                sev_norm = severity.lower()
                badge_norm = data["severity_badge"].lower()
                risk_norm = data["risk_level"].lower()
                if sev_norm not in badge_norm and sev_norm not in risk_norm:
                    continue

            items.append(
                CveThreatItem(
                    cve_id=cve_id,
                    risk_level=data["risk_level"],
                    severity_badge=data["severity_badge"],
                    affected_vendors=vendors_list,
                    first_seen=data["first_seen"],
                    last_seen=data["last_seen"],
                    editions_count=len(editions_list),
                    editions=editions_list,
                    headlines=headlines_list,
                    references=references_list,
                )
            )

        # Sort by last_seen descending, then cve_id ascending
        items.sort(key=lambda x: (x.last_seen, x.cve_id), reverse=True)
        total = len(items)
        paginated_items = items[offset : offset + limit]

        return total, paginated_items

    def generate_rss_feed(self, base_url: str = "") -> str:
        """Generate RSS 2.0 XML specification feed for syndication."""
        files = self._get_editorial_files()
        feed_link = base_url.rstrip("/") if base_url else "http://localhost:8000"
        now_dt = datetime.now(timezone.utc)
        build_date_str = email.utils.format_datetime(now_dt)

        items_xml: list[str] = []
        for path, dt, run_id in files[:20]:
            try:
                raw_text = path.read_text(encoding="utf-8")
                pkg = EditorialPackage.model_validate_json(raw_text)

                pub_date_str = email.utils.format_datetime(dt)
                edition_url = f"{feed_link}/api/v1/editions/{pkg.run_id}"

                sections_bullets = "".join(
                    f"<li><strong>{html.escape(s.heading)}</strong>: {html.escape(s.body.text)}</li>"
                    for s in pkg.digest.sections
                )
                description_html = f"<p>{html.escape(pkg.digest.intro)}</p><ul>{sections_bullets}</ul>"

                enclosure_xml = ""
                mp3_path = self._resolve_media_path(pkg.run_id, "audio")
                if mp3_path and mp3_path.exists():
                    audio_url = f"{feed_link}/api/v1/editions/{pkg.run_id}/media/audio"
                    size_bytes = mp3_path.stat().st_size
                    enclosure_xml = f'\n      <enclosure url="{audio_url}" length="{size_bytes}" type="audio/mpeg" />'

                item_str = f"""    <item>
      <title>{xml.sax.saxutils.escape(pkg.digest.title)}</title>
      <link>{edition_url}</link>
      <guid isPermaLink="false">{pkg.run_id}</guid>
      <pubDate>{pub_date_str}</pubDate>
      <description><![CDATA[{description_html}]]></description>
      <category>Cybersecurity Threat Intelligence</category>{enclosure_xml}
    </item>"""
                items_xml.append(item_str)
            except Exception as err:
                logger.warning("Error generating RSS item for %s: %s", path, err)

        all_items = "\n".join(items_xml)
        rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>CyberPulse AI - Threat Intelligence Feed</title>
    <link>{feed_link}/api/v1/editions</link>
    <description>Automated cybersecurity intelligence feed for SOC and SIEM integration</description>
    <language>vi</language>
    <lastBuildDate>{build_date_str}</lastBuildDate>
    <atom:link href="{feed_link}/api/v1/syndication/feed.xml" rel="self" type="application/rss+xml" />
{all_items}
  </channel>
</rss>"""
        return rss_xml

    def generate_json_feed(self, base_url: str = "") -> dict[str, Any]:
        """Generate JSON Feed v1.1 compliant dictionary structure."""
        files = self._get_editorial_files()
        feed_link = base_url.rstrip("/") if base_url else "http://localhost:8000"

        feed_items: list[dict[str, Any]] = []
        for path, dt, run_id in files[:20]:
            try:
                raw_text = path.read_text(encoding="utf-8")
                pkg = EditorialPackage.model_validate_json(raw_text)

                edition_url = f"{feed_link}/api/v1/editions/{pkg.run_id}"

                sections_html = "".join(
                    f"<h3>{html.escape(s.heading)}</h3><p>{html.escape(s.body.text)}</p>"
                    for s in pkg.digest.sections
                )
                content_html = f"<p><em>{html.escape(pkg.digest.intro)}</em></p>{sections_html}"

                plain_sections = "\n\n".join(f"* {s.heading}: {s.body.text}" for s in pkg.digest.sections)
                content_text = f"{pkg.digest.intro}\n\n{plain_sections}"

                # Extract all tags (CVEs and vendors)
                all_cves = extract_cves(raw_text)
                all_vendors = extract_vendor_tags(raw_text, topics=self.settings.topics)
                tags = sorted(list(set(all_cves + all_vendors)))

                attachments: list[dict[str, Any]] = []
                mp3_path = self._resolve_media_path(pkg.run_id, "audio")
                if mp3_path and mp3_path.exists():
                    attachments.append(
                        {
                            "url": f"{feed_link}/api/v1/editions/{pkg.run_id}/media/audio",
                            "mime_type": "audio/mpeg",
                            "size_in_bytes": mp3_path.stat().st_size,
                            "title": f"CyberPulse Podcast: {pkg.digest.title}",
                        }
                    )

                feed_items.append(
                    {
                        "id": pkg.run_id,
                        "url": edition_url,
                        "title": pkg.digest.title,
                        "summary": pkg.digest.intro,
                        "content_text": content_text,
                        "content_html": content_html,
                        "date_published": dt.isoformat(),
                        "tags": tags,
                        "attachments": attachments,
                    }
                )
            except Exception as err:
                logger.warning("Error generating JSON feed item for %s: %s", path, err)

        return {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "CyberPulse AI - Threat Intelligence Feed",
            "home_page_url": f"{feed_link}/api/v1/editions",
            "feed_url": f"{feed_link}/api/v1/syndication/feed.json",
            "description": "Automated cybersecurity intelligence feed for SOC and SIEM integration",
            "language": "vi",
            "items": feed_items,
        }
