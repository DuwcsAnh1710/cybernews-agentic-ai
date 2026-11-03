"""Multi-channel export engine and bundle packager for CyberPulse AI.

Provides deterministic packaging of editorial packages into email digests,
social media captions, video/podcast assets, publishing guidelines, and
cryptographically verified manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import logging
from pathlib import Path
import shutil
import zipfile

from cyberpulse.models import EditorialPackage, ResearchResult

logger = logging.getLogger("cyberpulse.export")


@dataclass
class ExportResult:
    """Result of an editorial bundle export operation."""

    run_id: str
    export_dir: Path
    files_created: list[str]
    article_count: int
    has_podcast_audio: bool
    has_podcast_srt: bool
    has_video: bool = False


ExportBundle = ExportResult


def _sha256_of_file(path: Path) -> str:
    """Compute hex SHA-256 digest of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _format_markdown_digest(
    package: EditorialPackage,
    run_id: str,
    exported_at: str,
    article_count: int,
    article_lookup: dict[str, dict],
) -> str:
    """Render clean formatted Markdown newsletter digest."""
    lines: list[str] = [
        f"# {package.digest.title}",
        "",
        f"> **Mã phiên chạy (Run ID):** `{run_id}` | **Ngôn ngữ:** Tiếng Việt (vi) | **Số bài viết phân tích:** {article_count}",
        f"> **Thời gian xuất bản:** {exported_at}",
        "",
        "## Giới thiệu",
        package.digest.intro,
        "",
        "## Điểm tin nổi bật (Top Highlights)",
    ]

    for sec in package.digest.sections:
        lines.append(f"- **{sec.heading}:** {sec.body.text[:140].rstrip()}...")

    lines.append("")
    lines.append("## Nội dung chi tiết (Curated Stories)")
    lines.append("")

    for sec in package.digest.sections:
        lines.append(f"### {sec.heading}")
        lines.append("")
        lines.append(sec.body.text)
        lines.append("")

        cites: list[str] = []
        for aid in sec.body.cited_article_ids:
            art = article_lookup.get(aid)
            if art:
                src_name = art.get("source_name", "Nguồn")
                url = art.get("url", "")
                title = art.get("title", aid)
                cites.append(f"[{title}]({url}) ({src_name})")
            else:
                cites.append(f"`{aid}`")

        if cites:
            lines.append(f"*Nguồn trích dẫn:* {', '.join(cites)}")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## Ghi chú Phân tích Kỹ thuật (Technical Analysis Notes)",
        "",
        "- **Đánh giá rủi ro an ninh mạng:** Các sự cố và lỗ hổng được tổng hợp trong bản tin có mức độ ảnh hưởng trực tiếp đến hạ tầng bảo mật, dữ liệu định danh và quy trình vận hành hệ thống.",
        "- **Khuyến nghị phòng thủ:**",
        "  1. Kịp thời cập nhật bản vá bảo mật chính thức từ các nhà cung cấp.",
        "  2. Tăng cường rà soát nhật ký truy cập (access logs) và thiết lập cảnh báo bất thường.",
        "  3. Tuyên truyền nâng cao nhận thức an toàn thông tin cho toàn bộ nhân sự trong tổ chức.",
        "",
        "---",
        "*Bản tin được tạo tự động bởi CyberPulse AI Editorial Studio.*",
    ])

    return "\n".join(lines)


def _format_html_digest(
    package: EditorialPackage,
    run_id: str,
    exported_at: str,
    article_count: int,
    article_lookup: dict[str, dict],
) -> str:
    """Render self-contained HTML newsletter with clean inline CSS."""
    title_esc = html.escape(package.digest.title)
    intro_esc = html.escape(package.digest.intro)
    run_id_esc = html.escape(run_id)
    exported_at_esc = html.escape(exported_at)

    highlights_html_list: list[str] = []
    for sec in package.digest.sections:
        h_esc = html.escape(sec.heading)
        snip_esc = html.escape(sec.body.text[:130].rstrip())
        highlights_html_list.append(f"<li><strong>{h_esc}:</strong> {snip_esc}...</li>")
    highlights_html = "\n        ".join(highlights_html_list)

    sections_html_list: list[str] = []
    for sec in package.digest.sections:
        h_esc = html.escape(sec.heading)
        t_esc = html.escape(sec.body.text)
        cites_html: list[str] = []
        for aid in sec.body.cited_article_ids:
            art = article_lookup.get(aid)
            if art:
                src_esc = html.escape(art.get("source_name", "Nguồn"))
                url_esc = html.escape(art.get("url", "#"))
                art_t_esc = html.escape(art.get("title", aid))
                cites_html.append(f'<a href="{url_esc}" target="_blank" rel="noopener">{art_t_esc} ({src_esc})</a>')
            else:
                cites_html.append(f"<code>{html.escape(aid)}</code>")
        cites_block = (
            f'<div class="citations"><strong>Nguồn trích dẫn:</strong> {" | ".join(cites_html)}</div>'
            if cites_html
            else ""
        )

        sections_html_list.append(f"""
      <article class="story-card">
        <h3>{h_esc}</h3>
        <p>{t_esc}</p>
        {cites_block}
      </article>""")

    sections_html = "\n".join(sections_html_list)

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title_esc}</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --muted: #64748b;
      --border: #e2e8f0;
      --accent: #0284c7;
      --highlight-bg: #f0f9ff;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.6;
      margin: 0;
      padding: 24px 16px;
    }}
    .newsletter-container {{
      max-width: 720px;
      margin: 0 auto;
      background: var(--card-bg);
      border-radius: 12px;
      border: 1px solid var(--border);
      padding: 36px 32px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }}
    header {{
      border-bottom: 2px solid var(--border);
      padding-bottom: 20px;
      margin-bottom: 24px;
    }}
    h1 {{
      font-size: 24px;
      font-weight: 700;
      color: #0369a1;
      margin: 0 0 12px 0;
      line-height: 1.3;
    }}
    .metadata {{
      font-size: 13px;
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .metadata-item {{
      background: #f1f5f9;
      padding: 4px 8px;
      border-radius: 4px;
    }}
    .intro {{
      font-size: 16px;
      line-height: 1.7;
      margin-bottom: 28px;
      color: #334155;
    }}
    .highlights-box {{
      background-color: var(--highlight-bg);
      border-left: 4px solid var(--accent);
      border-radius: 6px;
      padding: 16px 20px;
      margin-bottom: 32px;
    }}
    .highlights-box h2 {{
      font-size: 16px;
      font-weight: 600;
      color: var(--accent);
      margin: 0 0 10px 0;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .highlights-box ul {{
      margin: 0;
      padding-left: 20px;
      font-size: 14px;
      line-height: 1.6;
    }}
    .story-card {{
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
    }}
    .story-card h3 {{
      font-size: 18px;
      font-weight: 600;
      color: #0f172a;
      margin: 0 0 12px 0;
    }}
    .story-card p {{
      font-size: 15px;
      margin: 0 0 14px 0;
      color: #334155;
    }}
    .citations {{
      font-size: 13px;
      color: var(--muted);
      background: #f8fafc;
      padding: 8px 12px;
      border-radius: 4px;
      border-left: 3px solid #cbd5e1;
    }}
    .citations a {{
      color: var(--accent);
      text-decoration: none;
    }}
    .citations a:hover {{
      text-decoration: underline;
    }}
    .tech-notes {{
      margin-top: 36px;
      padding-top: 24px;
      border-top: 1px solid var(--border);
    }}
    .tech-notes h2 {{
      font-size: 17px;
      color: #1e293b;
      margin-top: 0;
    }}
    .tech-notes ul {{
      font-size: 14px;
      color: #475569;
      padding-left: 20px;
    }}
    footer {{
      margin-top: 36px;
      padding-top: 20px;
      border-top: 1px solid var(--border);
      font-size: 12px;
      text-align: center;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="newsletter-container">
    <header>
      <h1>{title_esc}</h1>
      <div class="metadata">
        <span class="metadata-item"><strong>Run:</strong> {run_id_esc}</span>
        <span class="metadata-item"><strong>Ngày:</strong> {exported_at_esc}</span>
        <span class="metadata-item"><strong>Tổng số bài:</strong> {article_count} tin</span>
      </div>
    </header>

    <div class="intro">
      {intro_esc}
    </div>

    <div class="highlights-box">
      <h2>Điểm tin nổi bật</h2>
      <ul>
        {highlights_html}
      </ul>
    </div>

    <section class="stories">
      {sections_html}
    </section>

    <section class="tech-notes">
      <h2>Ghi chú Phân tích Kỹ thuật & Khuyến nghị</h2>
      <ul>
        <li><strong>Đánh giá nguy cơ:</strong> Các chiến dịch tấn công và lỗ hổng bảo mật mới nhất đe dọa trực tiếp dữ liệu và tính liên tục trong vận hành của tổ chức.</li>
        <li><strong>Biện pháp đối phó:</strong> Ưu tiên rà soát các hệ thống chưa được cập nhật bản vá, phân quyền chặt chẽ các tài khoản đặc quyền và kiểm tra hệ thống sao lưu định kỳ.</li>
      </ul>
    </section>

    <footer>
      <p>CyberPulse AI &mdash; Hệ thống Tình báo An ninh Mạng Đa Kênh Tự Động</p>
    </footer>
  </div>
</body>
</html>
"""


def _format_social_facebook(package: EditorialPackage) -> str:
    """Render Facebook post with title, hashtags, takeaways, and discussion prompt."""
    fb_cap = next((c for c in package.captions if c.platform == "facebook"), None)
    takeaways = [f"• {sec.heading}" for sec in package.digest.sections]

    caption_text = fb_cap.text if fb_cap else package.digest.intro
    hashtags = " ".join(fb_cap.hashtags) if fb_cap and fb_cap.hashtags else "#CyberPulse #AnNinhMang #CyberSecurity"

    return f"""{package.digest.title}

{caption_text}

TỔNG HỢP ĐIỂM TIN NỔI BẬT:
{chr(10).join(takeaways)}

Bạn nhận định thế nào về các nguy cơ an ninh mạng kể trên và tổ chức của bạn đã sẵn sàng ứng phó chưa? Hãy để lại ý kiến bình luận bên dưới để cùng thảo luận nhé!

{hashtags}
"""


def _format_social_youtube(
    package: EditorialPackage,
    source_articles: list[dict],
) -> str:
    """Render YouTube video description with chapters, summary, and credits."""
    yt_cap = next((c for c in package.captions if c.platform == "youtube"), None)
    summary_text = yt_cap.text if yt_cap else package.digest.intro

    # Calculate timestamps/chapters from video scenes
    chapters: list[str] = ["00:00 - Giới thiệu bản tin"]
    current_time = 0.0
    for scene in package.video.scenes:
        current_time += scene.duration_sec
        m = int(current_time // 60)
        s = int(current_time % 60)
        scene_title = scene.on_screen_text.replace("\n", " ").strip()
        if len(scene_title) > 40:
            scene_title = scene_title[:37] + "..."
        chapters.append(f"{m:02d}:{s:02d} - {scene_title}")

    # Source credits
    credits: list[str] = []
    for art in source_articles:
        s_name = art.get("source", art.get("source_name", "RSS"))
        t = art.get("title", "")
        u = art.get("url", "")
        if u:
            credits.append(f"- {s_name}: {t}\n  Link: {u}")
        else:
            credits.append(f"- {s_name}: {t}")

    hashtags = " ".join(yt_cap.hashtags) if yt_cap and yt_cap.hashtags else "#CyberPulse #AnNinhMang #CyberSecurity"

    credits_block = "\n".join(credits) if credits else "- Các nguồn tin tức an ninh mạng quốc tế uy tín."

    return f"""{package.video.title}

{summary_text}

TIMESTAMPS / CÁC PHẦN TRONG VIDEO:
{chr(10).join(chapters)}

NGUỒN TIN THAM KHẢO & TRÍCH DẪN:
{credits_block}

Đừng quên bấm Like, Đăng ký kênh CyberPulse AI và bấm chuông thông báo để không bỏ lỡ các tin tức an toàn thông tin quan trọng nhất!

{hashtags}
"""


def _format_social_tiktok(package: EditorialPackage) -> str:
    """Render TikTok/Reels vertical script and bullet hooks."""
    tt_cap = next((c for c in package.captions if c.platform == "tiktok"), None)
    hashtags = " ".join(tt_cap.hashtags) if tt_cap and tt_cap.hashtags else "#CyberPulse #AnNinhMang #CyberSecurity #Hacker"

    scenes_script: list[str] = []
    for sc in package.video.scenes:
        scenes_script.append(f"""[CẢNH {sc.scene_no} | {sc.duration_sec}s]
- Chữ trên màn hình: {sc.on_screen_text}
- Giọng đọc (Voiceover): {sc.narration.text}""")

    hook_line = (
        package.video.scenes[0].narration.text
        if package.video.scenes
        else (tt_cap.text if tt_cap else "Cảnh báo an ninh mạng khẩn cấp hôm nay!")
    )

    return f"""[KỊCH BẢN VIDEO DỌC TIKTOK / REELS / YOUTUBE SHORTS]
Tiêu đề: {package.video.title}

=== 1. HOOK MỞ ĐẦU (0 - 3s) ===
"{hook_line}"

=== 2. KỊCH BẢN TỪNG CẢNH (STORYBOARD) ===
{chr(10).join(scenes_script)}

=== 3. KÊU GỌI HÀNH ĐỘNG (CTA) ===
"Follow ngay CyberPulse AI để bảo vệ hệ thống của bạn trước khi quá muộn!"

=== 4. CAPTION & HASHTAGS ĐĂNG BÀI ===
{tt_cap.text if tt_cap else package.digest.intro[:150]}

{hashtags}
"""


def _format_publishing_guide(run_id: str, exported_at: str) -> str:
    """Render editor checklist, publishing schedule, and guidelines."""
    return f"""# Hướng Dẫn Đăng Bài & Lịch Trình Phát Hành Đa Kênh

**Mã phiên chạy (Run ID):** `{run_id}`
**Thời gian xuất bản:** {exported_at}

---

## 1. Lịch trình phát hành khuyến nghị (Publishing Schedule)

| Khung giờ | Kênh phân phối | Tệp nội dung sử dụng | Mục tiêu & Lưu ý |
| :--- | :--- | :--- | :--- |
| **08:00** | **Email Newsletter** | `digest.html`, `digest.md` | Tiếp cận hộp thư độc giả đầu giờ làm việc; tỷ lệ mở email cao nhất trong ngày. |
| **09:30** | **Mạng xã hội (Facebook, LinkedIn)** | `social_facebook.txt` | Khung giờ tương tác cao buổi sáng; đính kèm ảnh minh họa hoặc link bản tin. |
| **11:30** | **Podcast & YouTube** | `podcast.mp3`, `podcast.srt`, `social_youtube.txt` | Phục vụ độc giả nghe âm thanh và xem video giờ nghỉ trưa. |
| **14:00** | **Short-form Video (TikTok/Reels)** | `social_tiktok.txt`, `{run_id}.mp4` | Giữ nhịp tương tác buổi chiều với video ngắn 30-60 giây. |

---

## 2. Quy chuẩn từng nền tảng (Platform Guidelines)

### Bản tin Email (Email Digest)
- Sử dụng tệp `digest.html` làm nội dung email HTML responsive.
- Kiểm tra lại giao diện hiển thị trên thiết bị di động trước khi gửi hàng loạt.
- Tệp `digest.md` thích hợp dùng cho Substack, Ghost hoặc nền tảng hỗ trợ Markdown.

### Facebook & Mạng Xã Hội
- Sử dụng nội dung sẵn có trong `social_facebook.txt`.
- Đính kèm hình ảnh trực quan hoặc ảnh chụp màn hình lỗ hổng/cảnh báo bảo mật.
- Tương tác với các bình luận trong 60 phút đầu tiên sau khi đăng bài.

### YouTube & Podcast
- Xuất bản tệp âm thanh `podcast.mp3` kèm tệp phụ đề `podcast.srt`.
- Dán nội dung `social_youtube.txt` vào phần mô tả video (đã bao gồm các mốc thời gian chapters).

### TikTok & Reels
- Bám sát kịch bản cảnh quay trong `social_tiktok.txt`.
- Đảm bảo 3 giây đầu tiên kích thích sự tò mò của người xem.

---

## 3. Chiến lược Thẻ Hashtag (Hashtag Strategy)

- **Hashtag thương hiệu:** `#CyberPulse #CyberPulseAI`
- **Hashtag an ninh mạng chính:** `#AnNinhMang #CyberSecurity #BaoMatThongTin #InfoSec`
- **Hashtag xu hướng:** `#TechNews #TinCongNghe #Hacker #Malware #Ransomware`

---

## 4. Danh mục kiểm tra của Biên tập viên (Editor Checklist)

- [ ] Đã kiểm tra tính chính xác của các thuật ngữ chuyên môn tiếng Việt.
- [ ] Đã xác nhận mọi đường dẫn trích dẫn bài viết gốc đều khả dụng.
- [ ] Đã nghe thử tệp âm thanh `podcast.mp3` (kiểm tra phát âm hai MC Minh & Lan).
- [ ] Đã kiểm tra tính đồng bộ của tệp phụ đề `podcast.srt`.
- [ ] Đã kiểm tra video ngắn `{run_id}.mp4` (khung hình 9:16 dọc, âm thanh và phụ đề).
- [ ] Đã gắn nhãn phê duyệt chính thức trên hệ thống trước khi phân phối.
"""


def export_editorial_bundle(
    package: EditorialPackage,
    run_id: str,
    output_dir: Path,
    export_root: Path = Path("export"),
) -> ExportResult:
    """Export complete multi-channel editorial bundle for a run."""
    target_dir = export_root / run_id
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load source articles from research artifact if available
    research_file = output_dir / "research" / f"{run_id}.json"
    article_lookup: dict[str, dict] = {}
    source_articles_manifest: list[dict] = []

    if research_file.exists() and research_file.is_file():
        try:
            res_data = json.loads(research_file.read_text(encoding="utf-8"))
            for it in res_data.get("items", []):
                art = it.get("article", {})
                aid = art.get("id")
                if aid:
                    article_lookup[aid] = art
                    source_articles_manifest.append({
                        "id": aid,
                        "title": art.get("title", ""),
                        "source": art.get("source_name", ""),
                        "url": art.get("url", ""),
                    })
        except Exception as err:
            logger.warning("Could not parse research file %s: %s", research_file, err)

    if not source_articles_manifest:
        for aid in sorted(list(package.all_cited_ids())):
            source_articles_manifest.append({
                "id": aid,
                "title": f"Article {aid}",
            })

    article_count = len(source_articles_manifest)
    exported_at_dt = datetime.now(timezone.utc)
    exported_at_iso = exported_at_dt.isoformat()
    exported_at_vn = exported_at_dt.strftime("%d/%m/%Y %H:%M UTC")

    # 2. Generate digest.md
    md_content = _format_markdown_digest(
        package=package,
        run_id=run_id,
        exported_at=exported_at_vn,
        article_count=article_count,
        article_lookup=article_lookup,
    )
    (target_dir / "digest.md").write_text(md_content, encoding="utf-8")

    # 3. Generate digest.html
    html_content = _format_html_digest(
        package=package,
        run_id=run_id,
        exported_at=exported_at_vn,
        article_count=article_count,
        article_lookup=article_lookup,
    )
    (target_dir / "digest.html").write_text(html_content, encoding="utf-8")

    # 4. Generate social_facebook.txt
    fb_content = _format_social_facebook(package)
    (target_dir / "social_facebook.txt").write_text(fb_content, encoding="utf-8")

    # 5. Generate social_youtube.txt
    yt_content = _format_social_youtube(package, source_articles_manifest)
    (target_dir / "social_youtube.txt").write_text(yt_content, encoding="utf-8")

    # 6. Generate social_tiktok.txt
    tt_content = _format_social_tiktok(package)
    (target_dir / "social_tiktok.txt").write_text(tt_content, encoding="utf-8")

    # 7. Copy Podcast Media (if available)
    has_podcast_audio = False
    has_podcast_srt = False
    media_dir = output_dir / "media" / "podcast"
    src_mp3 = media_dir / f"{run_id}.mp3"
    src_srt = media_dir / f"{run_id}.srt"

    if src_mp3.exists() and src_mp3.is_file():
        shutil.copy2(src_mp3, target_dir / "podcast.mp3")
        has_podcast_audio = True

    if src_srt.exists() and src_srt.is_file():
        shutil.copy2(src_srt, target_dir / "podcast.srt")
        has_podcast_srt = True

    # 7b. Copy Video Media (if available)
    has_video = False
    video_dir = output_dir / "media" / "video"
    src_mp4 = video_dir / f"{run_id}.mp4"
    if src_mp4.exists() and src_mp4.is_file():
        shutil.copy2(src_mp4, target_dir / f"{run_id}.mp4")
        has_video = True

    # 8. Generate huong_dan_dang_bai.md
    guide_content = _format_publishing_guide(run_id=run_id, exported_at=exported_at_vn)
    (target_dir / "huong_dan_dang_bai.md").write_text(guide_content, encoding="utf-8")

    # 9. Generate manifest.json with SHA256 checksums and byte sizes
    files_info: list[dict] = []
    for item in sorted(target_dir.iterdir()):
        if item.is_file() and item.name != "manifest.json":
            files_info.append({
                "name": item.name,
                "sha256": _sha256_of_file(item),
                "bytes": item.stat().st_size,
            })

    manifest_payload = {
        "run_id": run_id,
        "exported_at": exported_at_iso,
        "article_count": article_count,
        "source_articles": source_articles_manifest,
        "files": files_info,
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    created_names = [f["name"] for f in files_info] + ["manifest.json"]
    created_names.sort()

    return ExportResult(
        run_id=run_id,
        export_dir=target_dir,
        files_created=created_names,
        article_count=article_count,
        has_podcast_audio=has_podcast_audio,
        has_podcast_srt=has_podcast_srt,
        has_video=has_video,
    )


def create_export_zip(bundle_dir: Path) -> bytes:
    """Compress all files in bundle_dir into an in-memory ZIP byte buffer."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if bundle_dir.exists() and bundle_dir.is_dir():
            for item in sorted(bundle_dir.iterdir()):
                if item.is_file():
                    zf.write(item, arcname=item.name)
    return buffer.getvalue()
