"""Deterministic Markdown preview renderer for EditorialPackage."""

from __future__ import annotations

from cyberpulse.models import EditorialPackage, ResearchResult

DISCLAIMER = "> Nội dung do AI tạo từ tóm tắt RSS. Chưa được kiểm chứng độc lập."


def render_markdown(pkg: EditorialPackage, research: ResearchResult) -> str:
    """Render an EditorialPackage and ResearchResult into a clean Markdown preview.

    Attributions and URLs are retrieved exclusively from the input ResearchResult,
    never from LLM-generated text.
    """
    article_map = {item.article.id: item.article for item in research.items}

    lines: list[str] = [
        DISCLAIMER,
        "",
        f"# {pkg.digest.title}",
        "",
        pkg.digest.intro,
        "",
    ]

    # Sections
    for section in pkg.digest.sections:
        lines.append(f"## {section.heading}")
        lines.append("")
        lines.append(section.body.text)
        lines.append("")
        sources = [
            f"[{article_map[aid].source_name}]({article_map[aid].url})"
            for aid in section.body.cited_article_ids
            if aid in article_map
        ]
        if sources:
            lines.append(f"Nguồn: {', '.join(sources)}")
            lines.append("")

    # Captions
    lines.append("## Mạng xã hội")
    lines.append("")
    for caption in pkg.captions:
        lines.append(f"### {caption.platform.capitalize()}")
        lines.append("")
        lines.append(caption.text)
        lines.append("")
        if caption.hashtags:
            lines.append(" ".join(caption.hashtags))
            lines.append("")

    # Podcast Script
    lines.append(f"## Kịch bản Podcast: {pkg.podcast.title}")
    lines.append("")
    for segment in pkg.podcast.segments:
        lines.append(f"**{segment.speaker}:** {segment.line.text}")
        lines.append("")

    # Video Script
    lines.append(f"## Kịch bản Video: {pkg.video.title}")
    lines.append("")
    lines.append("| Cảnh | Chữ trên màn hình | Lời thoại | Thời lượng (giây) |")
    lines.append("| :--- | :--- | :--- | :--- |")
    total_sec = 0.0
    for scene in pkg.video.scenes:
        total_sec += scene.duration_sec
        # Escape pipe characters in on_screen_text or narration for markdown table safety
        on_screen = scene.on_screen_text.replace("|", "\\|")
        narration = scene.narration.text.replace("|", "\\|")
        lines.append(f"| {scene.scene_no} | {on_screen} | {narration} | {scene.duration_sec:.1f}s |")
    lines.append("")
    lines.append(f"*Tổng thời lượng dự kiến: {total_sec:.1f} giây*")
    lines.append("")

    # Danh sách nguồn (all cited articles)
    lines.append("## Danh sách nguồn")
    lines.append("")
    cited_ids = pkg.all_cited_ids()
    # List in research rank order
    for item in research.items:
        if item.article.id in cited_ids:
            art = item.article
            lines.append(f"- [{art.title}]({art.url}) ({art.source_name})")

    lines.append("")
    return "\n".join(lines)

