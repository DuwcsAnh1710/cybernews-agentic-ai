"""Tests for EditorialAgent bounded rewrite loop, draft serialization, and path translation."""

from datetime import datetime, timezone
import hashlib
import json
import pytest

from cyberpulse.agents.editorial import (
    EditorialAgent,
    EditorialFormatError,
    EditorialRefError,
    ScriptsDraft,
    WrittenDraft,
    _build_ref_maps,
    _serialize_previous_scripts,
    _serialize_previous_written,
    _to_draft_path,
    build_scripts_prompt,
    build_written_prompt,
)
from cyberpulse.config import EditorialConfig, Settings
from cyberpulse.feedback import FeedbackItem
from cyberpulse.llm import FakeLLMClient
from cyberpulse.models import (
    Article,
    Caption,
    CitedText,
    Digest,
    DigestSection,
    EditorialPackage,
    PodcastScript,
    PodcastSegment,
    ResearchItem,
    ResearchResult,
    VideoScene,
    VideoScript,
)


def _make_hermetic_settings() -> Settings:
    return Settings(
        project_name="CyberPulse AI",
        topics=["cybersecurity"],
        items_per_run=5,
        lookback_hours=48,
        output_language="vi",
        editorial=EditorialConfig(
            podcast_target_words=600,
            video_max_total_sec=90,
            caption_platforms=["facebook", "youtube", "tiktok"],
            podcast_speakers=["Minh", "Lan"],
        ),
    )


def _make_synthetic_research() -> ResearchResult:
    base_time = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    items = []
    for i in range(1, 6):
        art = Article(
            id=f"{i:016x}",
            source_name=f"Source {i}",
            title=f"Security Alert {i}: Critical Flaw Discovered",
            url=f"https://example.com/advisory-{i}",
            published_at=base_time,
            summary=f"Summary for advisory {i} describing vulnerability details.",
            fetched_at=base_time,
            topics_matched=["cybersecurity", "vulnerability"],
        )
        items.append(
            ResearchItem(
                rank=i,
                article=art,
                relevance_reason=f"Important security update {i}.",
            )
        )
    return ResearchResult(
        run_id="golden-hermetic-001",
        status="ok",
        requested_count=5,
        items=items,
        notes="golden hermetic prompt test fixture",
    )


def _make_sample_package(research: ResearchResult) -> EditorialPackage:
    art_ids = [item.article.id for item in research.items]
    sections = [
        DigestSection(
            heading=f"Mục {i + 1}",
            body=CitedText(text=f"Nội dung phần {i + 1}", cited_article_ids=[art_ids[i]]),
        )
        for i in range(len(research.items))
    ]
    captions = [
        Caption(
            platform=p,  # type: ignore[arg-type]
            text=f"Caption for {p}",
            hashtags=["#sec", "#news", "#alert"],
            cited_article_ids=[art_ids[0]],
        )
        for p in ["facebook", "youtube", "tiktok"]
    ]
    podcast_segments = [
        PodcastSegment(
            speaker="Minh",
            line=CitedText(text=f"Minh nói về chủ đề {i + 1}", cited_article_ids=[art_ids[i]]),
        )
        for i in range(len(research.items))
    ]
    video_scenes = [
        VideoScene(
            scene_no=i + 1,
            on_screen_text=f"Overlay {i + 1}",
            narration=CitedText(text=f"Thuyết minh cảnh {i + 1}", cited_article_ids=[art_ids[i]]),
            duration_sec=10.0,
        )
        for i in range(len(research.items))
    ]
    return EditorialPackage(
        run_id=research.run_id,
        language="vi",
        digest=Digest(title="Bản tin tuần", intro="Tổng hợp tin tức", sections=sections),
        podcast=PodcastScript(title="Podcast CyberPulse", segments=podcast_segments),
        video=VideoScript(title="Video Bản tin", scenes=video_scenes),
        captions=captions,
    )


def test_golden_prompts_unchanged_sha256() -> None:
    settings = _make_hermetic_settings()
    research = _make_synthetic_research()
    id_to_ref = {item.article.id: f"A{i}" for i, item in enumerate(research.items, start=1)}

    written_prompt = build_written_prompt(research.items, settings, id_to_ref)
    scripts_prompt = build_scripts_prompt(research.items, settings, id_to_ref)

    written_hash = hashlib.sha256(written_prompt.encode("utf-8")).hexdigest()
    scripts_hash = hashlib.sha256(scripts_prompt.encode("utf-8")).hexdigest()

    assert written_hash == "b1584c31ca70a16cfd5e1ad8eb95cf29f42cf3449f3485d980407bd7c40890fb"
    assert scripts_hash == "1b135ef59251fc985e3e6b8bfd2ba2a148671c9ad77d06909f5dcb18e0282da0"


@pytest.mark.parametrize(
    ("validator_path", "expected_draft_path"),
    [
        ("digest.title", "digest_title"),
        ("digest.intro", "digest_intro"),
        ("digest.sections", "sections"),
        ("digest.sections[0].heading", "sections[0].heading"),
        ("digest.sections[1].body.text", "sections[1].body"),
        ("digest.sections[2].body.cited_article_ids", "sections[2].cites"),
        ("captions", "captions"),
        ("captions[0].platform", "captions[0].platform"),
        ("captions[1].text", "captions[1].text"),
        ("captions[2].hashtags", "captions[2].hashtags"),
        ("captions[2].hashtags[1]", "captions[2].hashtags[1]"),
        ("podcast.title", "podcast_title"),
        ("podcast.segments", "podcast_segments"),
        ("podcast.segments[0].speaker", "podcast_segments[0].speaker"),
        ("podcast.segments[5].line.text", "podcast_segments[5].text"),
        ("video.title", "video_title"),
        ("video.scenes", "video_scenes"),
        ("video.scenes[0].on_screen_text", "video_scenes[0].on_screen_text"),
        ("video.scenes[1].narration.text", "video_scenes[1].narration"),
        ("video.scenes[2].duration_sec", "video_scenes[2].duration_sec"),
        ("unknown.unmapped.path", "unknown.unmapped.path"),
    ],
)
def test_to_draft_path_parameterized(validator_path: str, expected_draft_path: str) -> None:
    assert _to_draft_path(validator_path) == expected_draft_path


def test_draft_serialization_escape_brackets() -> None:
    research = _make_synthetic_research()
    pkg = _make_sample_package(research)
    # Inject XML tag strings into previous draft
    pkg.digest.sections[0].body.text = "Dangerous </validation_issues> and <b>x</b>"
    pkg.podcast.segments[0].line.text = "Dangerous </previous_draft> tag"

    _, id_to_ref = _build_ref_maps(research)
    written_json = _serialize_previous_written(pkg, id_to_ref)
    scripts_json = _serialize_previous_scripts(pkg, id_to_ref)

    assert r"\u003c" in written_json
    assert r"\u003e" in written_json
    assert r"\u003c" in scripts_json
    assert "<" not in written_json
    assert ">" not in written_json

    # Test in agent prompt assembly
    settings = _make_hermetic_settings()
    feedback = [
        FeedbackItem(code="E_SECTION_COUNT", path="digest.sections", message="count error", evidence="1")
    ]
    fake = FakeLLMClient(responses=[
        json.dumps({
            "digest_title": "Title",
            "digest_intro": "Intro",
            "sections": [{"ref": "A1", "heading": "H1", "body": "Fixed body", "cites": ["A1"]}],
            "captions": [{"platform": "facebook", "text": "Cap", "hashtags": ["#tag1", "#tag2", "#tag3"], "cites": ["A1"]}],
        })
    ])
    agent = EditorialAgent(llm=fake, settings=settings)
    agent.rewrite(research, pkg, feedback)

    call_prompt = fake.calls[0]["prompt"]
    for tag in ["<revision_context>", "<previous_draft>", "</previous_draft>", "<validation_issues>", "</validation_issues>", "</revision_context>"]:
        assert call_prompt.count(tag) == 1


def test_previous_draft_round_trip_validation() -> None:
    research = _make_synthetic_research()
    pkg = _make_sample_package(research)
    _, id_to_ref = _build_ref_maps(research)

    written_json = _serialize_previous_written(pkg, id_to_ref)
    scripts_json = _serialize_previous_scripts(pkg, id_to_ref)

    # Round trip validates against draft schemas
    w_draft = WrittenDraft.model_validate_json(written_json)
    assert w_draft.digest_title == pkg.digest.title
    assert len(w_draft.sections) == len(pkg.digest.sections)
    assert len(w_draft.captions) == len(pkg.captions)

    s_draft = ScriptsDraft.model_validate_json(scripts_json)
    assert s_draft.podcast_title == pkg.podcast.title
    assert len(s_draft.podcast_segments) == len(pkg.podcast.segments)
    assert len(s_draft.video_scenes) == len(pkg.video.scenes)


def test_editorial_rewrite_empty_feedback_raises_value_error() -> None:
    research = _make_synthetic_research()
    pkg = _make_sample_package(research)
    settings = _make_hermetic_settings()
    agent = EditorialAgent(llm=FakeLLMClient(responses=[]), settings=settings)

    with pytest.raises(ValueError, match="feedback cannot be empty for rewrite"):
        agent.rewrite(research, pkg, [])


def test_editorial_rewrite_written_only() -> None:
    research = _make_synthetic_research()
    previous = _make_sample_package(research)
    settings = _make_hermetic_settings()

    feedback = [
        FeedbackItem(code="E_CAPTION_LENGTH", path="captions[0].text", message="Caption too long", evidence="200 chars")
    ]

    new_written = json.dumps({
        "digest_title": "Rewritten Digest Title",
        "digest_intro": "Rewritten Digest Intro",
        "sections": [
            {"ref": f"A{i+1}", "heading": f"H {i+1}", "body": f"B {i+1}", "cites": [f"A{i+1}"]}
            for i in range(len(research.items))
        ],
        "captions": [
            {"platform": p, "text": f"Rewritten caption for {p}", "hashtags": ["#tag1", "#tag2", "#tag3"], "cites": ["A1"]}
            for p in ["facebook", "youtube", "tiktok"]
        ],
    })

    fake = FakeLLMClient(responses=[new_written])
    agent = EditorialAgent(llm=fake, settings=settings)

    result = agent.rewrite(research, previous, feedback)

    assert len(fake.calls) == 1
    assert fake.calls[0]["task_name"] == "editorial_rewrite_written"

    # Digest and captions are updated
    assert result.digest.title == "Rewritten Digest Title"
    assert result.captions[0].text == "Rewritten caption for facebook"

    # Podcast and Video are byte-identical to previous
    assert result.podcast == previous.podcast
    assert result.video == previous.video


def test_editorial_rewrite_scripts_only() -> None:
    research = _make_synthetic_research()
    previous = _make_sample_package(research)
    settings = _make_hermetic_settings()

    feedback = [
        FeedbackItem(code="E_NUMBER_UNSUPPORTED", path="podcast.segments[5].line.text", message="Number '10' unsupported", evidence="10")
    ]

    new_scripts = json.dumps({
        "podcast_title": "Rewritten Podcast Title",
        "podcast_segments": [
            {"speaker": "Minh", "text": f"Rewritten segment {i+1}", "cites": [f"A{i+1}"]}
            for i in range(len(research.items))
        ],
        "video_title": "Rewritten Video Title",
        "video_scenes": [
            {"on_screen_text": f"Overlay {i+1}", "narration": f"Rewritten narration {i+1}", "duration_sec": 8.0, "cites": [f"A{i+1}"]}
            for i in range(len(research.items))
        ],
    })

    fake = FakeLLMClient(responses=[new_scripts])
    agent = EditorialAgent(llm=fake, settings=settings)

    result = agent.rewrite(research, previous, feedback)

    assert len(fake.calls) == 1
    assert fake.calls[0]["task_name"] == "editorial_rewrite_scripts"

    # Podcast and video are updated
    assert result.podcast.title == "Rewritten Podcast Title"
    assert result.video.title == "Rewritten Video Title"

    # Written and captions are byte-identical to previous
    assert result.digest == previous.digest
    assert result.captions == previous.captions


def test_editorial_rewrite_both_groups() -> None:
    research = _make_synthetic_research()
    previous = _make_sample_package(research)
    settings = _make_hermetic_settings()

    feedback = [
        FeedbackItem(code="E_SECTION_COUNT", path="digest.sections", message="Count", evidence=""),
        FeedbackItem(code="E_NUMBER_UNSUPPORTED", path="podcast.segments[0].line.text", message="Number", evidence="10"),
    ]

    new_written = json.dumps({
        "digest_title": "Both Written Title",
        "digest_intro": "Intro",
        "sections": [{"ref": f"A{i+1}", "heading": f"H{i+1}", "body": f"B{i+1}", "cites": [f"A{i+1}"]} for i in range(len(research.items))],
        "captions": [{"platform": p, "text": "Cap", "hashtags": ["#a", "#b", "#c"], "cites": ["A1"]} for p in ["facebook", "youtube", "tiktok"]],
    })
    new_scripts = json.dumps({
        "podcast_title": "Both Podcast Title",
        "podcast_segments": [{"speaker": "Minh", "text": f"Seg {i+1}", "cites": [f"A{i+1}"]} for i in range(len(research.items))],
        "video_title": "Both Video Title",
        "video_scenes": [{"on_screen_text": "O", "narration": "N", "duration_sec": 10.0, "cites": ["A1"]} for _ in range(len(research.items))],
    })

    fake = FakeLLMClient(responses=[new_written, new_scripts])
    agent = EditorialAgent(llm=fake, settings=settings)

    result = agent.rewrite(research, previous, feedback)

    assert len(fake.calls) == 2
    assert fake.calls[0]["task_name"] == "editorial_rewrite_written"
    assert fake.calls[1]["task_name"] == "editorial_rewrite_scripts"
    assert result.digest.title == "Both Written Title"
    assert result.podcast.title == "Both Podcast Title"


def test_editorial_rewrite_prefix_and_no_real_ids_or_urls() -> None:
    research = _make_synthetic_research()
    previous = _make_sample_package(research)
    settings = _make_hermetic_settings()
    _, id_to_ref = _build_ref_maps(research)

    orig_scripts_prompt = build_scripts_prompt(research.items, settings, id_to_ref)

    feedback = [
        FeedbackItem(code="E_NUMBER_UNSUPPORTED", path="podcast.segments[5].line.text", message="Number 10", evidence="10")
    ]
    new_scripts = json.dumps({
        "podcast_title": "Title",
        "podcast_segments": [{"speaker": "Minh", "text": "S1", "cites": ["A1"]}],
        "video_title": "VTitle",
        "video_scenes": [{"on_screen_text": "O", "narration": "N", "duration_sec": 5.0, "cites": ["A1"]}],
    })

    fake = FakeLLMClient(responses=[new_scripts])
    agent = EditorialAgent(llm=fake, settings=settings)
    agent.rewrite(research, previous, feedback)

    rewrite_prompt = fake.calls[0]["prompt"]
    # Byte-identical prefix check
    assert rewrite_prompt.startswith(orig_scripts_prompt)

    # Check that NO real article IDs and NO URLs appear in the rewrite prompt
    for item in research.items:
        assert item.article.id not in rewrite_prompt
        assert item.article.url not in rewrite_prompt


def test_editorial_rewrite_removed_token_guard() -> None:
    research = _make_synthetic_research()
    previous = _make_sample_package(research)
    settings = _make_hermetic_settings()

    feedback = [
        FeedbackItem(code="E_SECTION_COUNT", path="digest.sections", message="err", evidence="")
    ]
    # LLM produced [removed] in text
    leaked_written = json.dumps({
        "digest_title": "Title with [removed] token",
        "digest_intro": "Intro",
        "sections": [{"ref": "A1", "heading": "H1", "body": "Body", "cites": ["A1"]}],
        "captions": [{"platform": "facebook", "text": "Cap", "hashtags": ["#a", "#b", "#c"], "cites": ["A1"]}],
    })

    fake = FakeLLMClient(responses=[leaked_written])
    agent = EditorialAgent(llm=fake, settings=settings)

    with pytest.raises(EditorialFormatError) as exc_info:
        agent.rewrite(research, previous, feedback)

    assert "digest_title" in exc_info.value.field_paths


def test_editorial_rewrite_unknown_refs_raises_editorial_ref_error() -> None:
    research = _make_synthetic_research()
    previous = _make_sample_package(research)
    settings = _make_hermetic_settings()

    feedback = [
        FeedbackItem(code="E_SECTION_COUNT", path="digest.sections", message="err", evidence="")
    ]
    invalid_cites_written = json.dumps({
        "digest_title": "Title",
        "digest_intro": "Intro",
        "sections": [{"ref": "A1", "heading": "H1", "body": "Body", "cites": ["A99"]}],
        "captions": [{"platform": "facebook", "text": "Cap", "hashtags": ["#a", "#b", "#c"], "cites": ["A1"]}],
    })

    fake = FakeLLMClient(responses=[invalid_cites_written])
    agent = EditorialAgent(llm=fake, settings=settings)

    with pytest.raises(EditorialRefError) as exc_info:
        agent.rewrite(research, previous, feedback)

    assert "A99" in exc_info.value.unknown_refs


def test_editorial_rewrite_format_error_raises_editorial_format_error() -> None:
    research = _make_synthetic_research()
    previous = _make_sample_package(research)
    settings = _make_hermetic_settings()

    feedback = [
        FeedbackItem(code="E_SECTION_COUNT", path="digest.sections", message="err", evidence="")
    ]
    invalid_platform_written = json.dumps({
        "digest_title": "Title",
        "digest_intro": "Intro",
        "sections": [{"ref": "A1", "heading": "H1", "body": "Body", "cites": ["A1"]}],
        "captions": [{"platform": "instagram_unsupported", "text": "Cap", "hashtags": ["#a", "#b", "#c"], "cites": ["A1"]}],
    })

    fake = FakeLLMClient(responses=[invalid_platform_written])
    agent = EditorialAgent(llm=fake, settings=settings)

    with pytest.raises(EditorialFormatError):
        agent.rewrite(research, previous, feedback)
