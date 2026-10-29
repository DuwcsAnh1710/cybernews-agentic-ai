"""Unit and integration tests for Editorial Agent (Agent 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import pytest

from cyberpulse.agents.editorial import (
    EditorialAgent,
    EditorialFormatError,
    EditorialRefError,
    WrittenDraft,
)
from cyberpulse.cli import main
from cyberpulse.config import ConfigError, Settings, load_settings
from cyberpulse.llm import (
    FakeLLMClient,
    LLMConfig,
    LLMRateLimitError,
    LLMResponseError,
)
from cyberpulse.models import (
    Article,
    EditorialPackage,
    ResearchItem,
    ResearchResult,
    make_article_id,
)
from cyberpulse.render_preview import DISCLAIMER, render_markdown
from cyberpulse.storage import connect, init_db


def _make_article(
    index: int,
    source: str = "Sample Source A",
    pub_offset_hours: int = 0,
) -> Article:
    url = f"https://example.com/sample/item-{index}"
    t0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    pub_at = t0 - timedelta(hours=pub_offset_hours)
    title = f"SAMPLE — Threat Advisory {index}"
    return Article(
        id=make_article_id(url),
        source_name=source,
        title=title,
        url=url,
        published_at=pub_at,
        summary=f"SAMPLE — Chi tiết tóm tắt lỗ hổng {index} (mẫu).",
        fetched_at=t0,
        topics_matched=["cybersecurity"],
    )


def _make_research_result(articles: list[Article], run_id: str = "test-run-editorial-1") -> ResearchResult:
    items = [
        ResearchItem(
            rank=i + 1,
            article=a,
            relevance_reason=f"SAMPLE — Lý do chọn bài {i + 1} (mẫu).",
        )
        for i, a in enumerate(articles)
    ]
    return ResearchResult(
        run_id=run_id,
        status="ok",
        requested_count=len(articles),
        items=items,
        notes="sample research result",
    )


def _make_written_draft_json(ref_list: list[str]) -> str:
    sections = [
        {
            "ref": ref,
            "heading": f"SAMPLE — Tiêu đề mục tin {ref} (mẫu)",
            "body": f"SAMPLE — Nội dung phân tích lỗ hổng cho bài {ref} đầy đủ thông tin kỹ thuật (mẫu).",
            "cites": [ref],
        }
        for ref in ref_list
    ]
    captions = [
        {
            "platform": "facebook",
            "text": "SAMPLE — Cập nhật an toàn thông tin hôm nay trên Facebook (mẫu).",
            "hashtags": ["#security", "#cybernews", "#advisory"],
            "cites": ref_list,
        },
        {
            "platform": "youtube",
            "text": "SAMPLE — Video phân tích tổng hợp các lỗ hổng mạng trên YouTube (mẫu).",
            "hashtags": ["#cve", "#infosec", "#patch"],
            "cites": ref_list,
        },
        {
            "platform": "tiktok",
            "text": "SAMPLE — Cảnh báo bảo mật nhanh trên TikTok (mẫu).",
            "hashtags": ["#threat", "#hacker", "#alert"],
            "cites": [ref_list[0]],
        },
    ]
    return json.dumps({
        "digest_title": "SAMPLE — Bản tin An toàn thông tin nổi bật (mẫu)",
        "digest_intro": "SAMPLE — Tổng hợp các sự cố an ninh mạng và lỗ hổng bảo mật quan trọng trong tuần (mẫu).",
        "sections": sections,
        "captions": captions,
    })


def _make_scripts_draft_json(ref_list: list[str]) -> str:
    podcast_segments = [
        {
            "speaker": "Minh",
            "text": "SAMPLE — Chào mừng quý vị thính giả đến với bản tin an ninh mạng (mẫu).",
            "cites": ref_list,
        },
        {
            "speaker": "Lan",
            "text": f"SAMPLE — Hôm nay chúng ta sẽ bắt đầu với lỗ hổng được ghi nhận trong {ref_list[0]} (mẫu).",
            "cites": [ref_list[0]],
        },
        {
            "speaker": "Minh",
            "text": "SAMPLE — Cảm ơn các bạn đã lắng nghe và hẹn gặp lại ở số tiếp theo (mẫu).",
            "cites": ref_list,
        },
    ]
    video_scenes = [
        {
            "on_screen_text": "CẢNH BÁO BẢO MẬT",
            "narration": "SAMPLE — Điểm tin an ninh mạng khẩn cấp hôm nay (mẫu).",
            "duration_sec": 5.0,
            "cites": ref_list,
        },
    ]
    for ref in ref_list:
        video_scenes.append({
            "on_screen_text": f"LỖ HỔNG {ref}",
            "narration": f"SAMPLE — Phát hiện lỗ hổng nghiêm trọng từ {ref} đang bị khai thác (mẫu).",
            "duration_sec": 8.0,
            "cites": [ref],
        })
    video_scenes.append({
        "on_screen_text": "THEO DÕI NGAY",
        "narration": "SAMPLE — Hãy cập nhật bản vá sớm nhất để bảo vệ hệ thống (mẫu).",
        "duration_sec": 5.0,
        "cites": ref_list,
    })

    return json.dumps({
        "podcast_title": "SAMPLE — Kịch bản Podcast An ninh mạng (mẫu)",
        "podcast_segments": podcast_segments,
        "video_title": "SAMPLE — Video Short Cảnh báo an ninh (mẫu)",
        "video_scenes": video_scenes,
    })


@pytest.fixture
def editorial_settings(tmp_path: Path) -> Settings:
    settings_file = tmp_path / "settings.toml"
    content = f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
items_per_run = 3
lookback_hours = 48
output_language = "vi"
log_level = "INFO"

[paths]
data_dir = "{(tmp_path / "data").as_posix()}"
output_dir = "{(tmp_path / "output").as_posix()}"
log_dir = "{(tmp_path / "logs").as_posix()}"

[editorial]
podcast_target_words = 600
video_max_total_sec = 90
caption_platforms = ["facebook", "youtube", "tiktok"]
podcast_speakers = ["Minh", "Lan"]
"""
    settings_file.write_text(content, encoding="utf-8")
    return load_settings(settings_file)


# ==============================================================================
# 1. Happy path: 3 sample articles -> package valid, 3 sections, captions == platforms
# ==============================================================================
def test_editorial_agent_happy_path(editorial_settings: Settings) -> None:
    """1. Happy path with 3 sample articles -> valid EditorialPackage, sections==3, captions match."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)
    ref_list = ["A1", "A2", "A3"]

    written_json = _make_written_draft_json(ref_list)
    scripts_json = _make_scripts_draft_json(ref_list)

    fake = FakeLLMClient(responses=[written_json, scripts_json])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    pkg = agent.run(research)

    assert isinstance(pkg, EditorialPackage)
    assert pkg.run_id == research.run_id
    assert pkg.language == "vi"
    assert len(pkg.digest.sections) == 3
    assert len(pkg.captions) == 3
    assert [c.platform for c in pkg.captions] == ["facebook", "youtube", "tiktok"]
    assert len(pkg.podcast.segments) == 3
    assert len(pkg.video.scenes) == 5  # 1 hook + 3 articles + 1 outro
    # Ensure every cited ID belongs to the input research items
    expected_ids = {a.id for a in articles}
    all_cited = pkg.all_cited_ids()
    assert all_cited.issubset(expected_ids)
    assert all_cited == expected_ids


# ==============================================================================
# 2. Prompts contain refs A1..An, and NO article id and NO URL
# ==============================================================================
def test_editorial_agent_prompts_contain_refs_and_no_ids_or_urls(
    editorial_settings: Settings,
) -> None:
    """2. The prompts sent to the LLM contain refs A1..An, and contain NO article id and NO URL."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)
    ref_list = ["A1", "A2", "A3"]

    written_json = _make_written_draft_json(ref_list)
    scripts_json = _make_scripts_draft_json(ref_list)

    fake = FakeLLMClient(responses=[written_json, scripts_json])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)
    agent.run(research)

    assert len(fake.calls) == 2
    for call in fake.calls:
        prompt_text = call["prompt"]
        # Must contain reference tags
        assert "A1" in prompt_text
        assert "A2" in prompt_text
        assert "A3" in prompt_text
        # Must NOT contain real article IDs
        for a in articles:
            assert a.id not in prompt_text
        # Must NOT contain URLs
        assert "https://" not in prompt_text
        assert "http://" not in prompt_text


# ==============================================================================
# 3. Unknown ref "A9" anywhere -> EditorialRefError
# ==============================================================================
def test_editorial_agent_unknown_ref_raises_editorial_ref_error(
    editorial_settings: Settings,
) -> None:
    """3. Unknown ref (e.g. 'A9') in a caption -> EditorialRefError with field path."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)

    written_data = json.loads(_make_written_draft_json(["A1", "A2", "A3"]))
    # Inject unknown ref into captions[0].cites
    written_data["captions"][0]["cites"] = ["A9"]

    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])

    fake = FakeLLMClient(responses=[json.dumps(written_data), scripts_json])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    with pytest.raises(EditorialRefError) as exc_info:
        agent.run(research)

    err = exc_info.value
    assert "A9" in err.unknown_refs
    assert "captions[0].cites" in err.field_paths


# ==============================================================================
# 4. Empty cites list in a section -> validation error, no package
# ==============================================================================
def test_editorial_agent_empty_cites_raises_error(
    editorial_settings: Settings,
) -> None:
    """4. Empty cites list in a section -> schema error, no package returned."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)

    written_data = json.loads(_make_written_draft_json(["A1", "A2", "A3"]))
    # Set cites to empty list (violates SectionDraft min_length=1)
    written_data["sections"][0]["cites"] = []

    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])

    fake = FakeLLMClient(
        responses=[json.dumps(written_data), json.dumps(written_data)],
        cfg=LLMConfig(provider="fake", schema_retries=0),
    )
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    with pytest.raises(LLMResponseError):
        agent.run(research)


# ==============================================================================
# 5. Caption with platform "instagram" -> EditorialFormatError
# ==============================================================================
def test_editorial_agent_invalid_platform_raises_format_error(
    editorial_settings: Settings,
) -> None:
    """5. Caption with platform 'instagram' -> EditorialFormatError."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)

    written_data = json.loads(_make_written_draft_json(["A1", "A2", "A3"]))
    written_data["captions"][0]["platform"] = "instagram"

    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])

    fake = FakeLLMClient(responses=[json.dumps(written_data), scripts_json])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    with pytest.raises(EditorialFormatError) as exc_info:
        agent.run(research)

    assert "instagram" in str(exc_info.value)
    assert "captions[0].platform" in exc_info.value.field_paths


# ==============================================================================
# 6. Duplicate refs in one block -> de-duplicated ids
# ==============================================================================
def test_editorial_agent_duplicate_refs_deduplicated(
    editorial_settings: Settings,
) -> None:
    """6. Duplicate refs in one block -> de-duplicated ids preserving order."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)

    written_data = json.loads(_make_written_draft_json(["A1", "A2", "A3"]))
    written_data["sections"][0]["cites"] = ["A1", "A1", "A2"]

    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])

    fake = FakeLLMClient(responses=[json.dumps(written_data), scripts_json])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    pkg = agent.run(research)
    section_0_ids = pkg.digest.sections[0].body.cited_article_ids
    assert section_0_ids == [articles[0].id, articles[1].id]


# ==============================================================================
# 7. Scenes come back without numbers -> scene_no assigned 1..n
# ==============================================================================
def test_editorial_agent_scene_numbers_assigned_strictly_1_to_n(
    editorial_settings: Settings,
) -> None:
    """7. Scenes come back without numbers -> scene_no assigned 1..n."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)

    written_json = _make_written_draft_json(["A1", "A2", "A3"])
    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])

    fake = FakeLLMClient(responses=[written_json, scripts_json])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    pkg = agent.run(research)
    scene_nos = [s.scene_no for s in pkg.video.scenes]
    assert scene_nos == list(range(1, len(pkg.video.scenes) + 1))


# ==============================================================================
# 8. Empty research -> ValueError and 0 LLM calls
# ==============================================================================
def test_editorial_agent_empty_research_raises_value_error(
    editorial_settings: Settings,
) -> None:
    """8. Empty research -> ValueError, fake.calls == []."""
    empty_research = ResearchResult(
        run_id="test-empty",
        status="insufficient",
        requested_count=3,
        items=[],
        notes="empty items",
    )
    fake = FakeLLMClient(responses=[])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    with pytest.raises(ValueError, match="research.items cannot be empty"):
        agent.run(empty_research)

    assert fake.calls == []


# ==============================================================================
# 9. LLMRateLimitError on call 2 -> propagates
# ==============================================================================
def test_editorial_agent_rate_limit_error_on_call_2_propagates(
    editorial_settings: Settings,
) -> None:
    """9. LLMRateLimitError on call 2 (max_retries=0) -> propagates."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)

    written_json = _make_written_draft_json(["A1", "A2", "A3"])
    cfg = LLMConfig(provider="fake", max_retries=0)
    fake = FakeLLMClient(
        responses=[written_json, LLMRateLimitError("Call 2 rate limited")],
        cfg=cfg,
    )
    agent = EditorialAgent(llm=fake, settings=editorial_settings)

    with pytest.raises(LLMRateLimitError):
        agent.run(research)


# ==============================================================================
# 10. Preview: disclaimer present, real URLs from research, no foreign URLs
# ==============================================================================
def test_render_preview_markdown(editorial_settings: Settings) -> None:
    """10. Preview: disclaimer present; each section has Nguồn: with real sample URL; set of URLs in preview ⊆ research URLs."""
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)

    written_json = _make_written_draft_json(["A1", "A2", "A3"])
    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])

    fake = FakeLLMClient(responses=[written_json, scripts_json])
    agent = EditorialAgent(llm=fake, settings=editorial_settings)
    pkg = agent.run(research)

    md = render_markdown(pkg, research)

    # 1. Disclaimer check
    assert DISCLAIMER in md
    # 2. Real URLs present
    for a in articles:
        assert a.url in md
        assert f"[{a.source_name}]({a.url})" in md
    # 3. Set of URLs in preview is subset of research URLs
    found_urls = set(re.findall(r"https?://[^\s\)\>\]]+", md))
    research_urls = {a.url for a in articles}
    assert found_urls.issubset(research_urls)


# ==============================================================================
# 11. Invalid [editorial] values -> ConfigError
# ==============================================================================
@pytest.mark.parametrize(
    ("bad_field", "bad_value"),
    [
        ("podcast_speakers", '["Minh"]'),                    # 1 name
        ("podcast_speakers", '["Minh", "Minh"]'),             # duplicate names
        ("caption_platforms", '["instagram"]'),               # invalid platform
        ("caption_platforms", '[]'),                          # empty list
        ("video_max_total_sec", "10"),                        # < 30
        ("video_max_total_sec", "200"),                       # > 180
        ("podcast_target_words", "100"),                      # < 300
        ("podcast_target_words", "2000"),                     # > 1500
    ],
)
def test_editorial_config_validation_errors(tmp_path: Path, bad_field: str, bad_value: str) -> None:
    """11. Invalid [editorial] values -> ConfigError."""
    bad_settings = tmp_path / f"bad_{bad_field}.toml"
    content = f"""project_name = "CyberPulse AI"
topics = ["cybersecurity"]
items_per_run = 3
lookback_hours = 48
output_language = "vi"
log_level = "INFO"

[paths]
data_dir = "data"
output_dir = "output"
log_dir = "logs"

[editorial]
podcast_target_words = 600
video_max_total_sec = 90
caption_platforms = ["facebook", "youtube", "tiktok"]
podcast_speakers = ["Minh", "Lan"]
{bad_field} = {bad_value}
"""
    bad_settings.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_settings(bad_settings)
    assert bad_field in str(exc_info.value)


# ==============================================================================
# 12. CLI tests: missing file (7), key unset (4), Fake success (0), Ref error (7)
# ==============================================================================
def test_cli_editorial_missing_research_file_exits_7(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """12a. Missing research file -> exit 7."""
    settings_path, _ = config_paths
    missing_file = Path("non_existent_research_result.json")

    ret = main(["editorial", "--research", str(missing_file), "--settings", str(settings_path)])
    assert ret == 7

    captured = capsys.readouterr()
    assert "Error: research file not found" in captured.err


def test_cli_editorial_key_unset_exits_4(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """12b. GEMINI_API_KEY unset -> exit 4."""
    settings_path, _ = config_paths
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)
    research_file = tmp_path / "valid_research.json"
    research_file.write_text(research.model_dump_json(indent=2), encoding="utf-8")

    ret = main(["editorial", "--research", str(research_file), "--settings", str(settings_path)])
    assert ret == 4

    captured = capsys.readouterr()
    assert 'GEMINI_API_KEY: missing — live LLM check skipped. See README "LLM setup".' in captured.out


def test_cli_editorial_success_exits_0(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """12c. Monkeypatched Fake client -> exit 0, both JSON and MD files exist, DB untouched."""
    settings_path, _ = config_paths
    settings = load_settings(settings_path)
    db_path = settings.paths.data_dir / "cyberpulse.db"
    conn = connect(db_path)
    init_db(conn)

    # Count DB rows before
    count_runs_before = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    count_contents_before = conn.execute("SELECT count(*) FROM contents").fetchone()[0]
    conn.close()

    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)
    research_file = tmp_path / "valid_research.json"
    research_file.write_text(research.model_dump_json(indent=2), encoding="utf-8")

    written_json = _make_written_draft_json(["A1", "A2", "A3"])
    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])
    fake_client = FakeLLMClient(responses=[written_json, scripts_json])

    monkeypatch.setattr("cyberpulse.cli.create_llm_client", lambda cfg: fake_client)
    monkeypatch.setenv("GEMINI_API_KEY", "mock-valid-key")

    ret = main(["editorial", "--research", str(research_file), "--settings", str(settings_path)])
    assert ret == 0

    captured = capsys.readouterr()
    out = captured.out
    assert "Saved editorial package:" in out
    assert "Saved editorial preview:" in out
    assert "Editorial Summary:" in out
    assert "- Sections: 3" in out
    assert "- Captions: 3" in out

    # Check files exist
    editorial_dir = settings.paths.output_dir / "editorial"
    json_path = editorial_dir / f"{research.run_id}.json"
    md_path = editorial_dir / f"{research.run_id}.md"
    assert json_path.exists()
    assert md_path.exists()

    # Validate generated JSON parses as EditorialPackage
    pkg = EditorialPackage.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert pkg.run_id == research.run_id
    assert len(pkg.digest.sections) == 3

    # Check DB was NOT written to
    conn2 = connect(db_path)
    count_runs_after = conn2.execute("SELECT count(*) FROM runs").fetchone()[0]
    count_contents_after = conn2.execute("SELECT count(*) FROM contents").fetchone()[0]
    conn2.close()
    assert count_runs_after == count_runs_before
    assert count_contents_after == count_contents_before


def test_cli_editorial_ref_error_exits_7(
    config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """12d. EditorialRefError in CLI -> exit 7 with unknown refs printed."""
    settings_path, _ = config_paths
    articles = [_make_article(i) for i in range(1, 4)]
    research = _make_research_result(articles)
    research_file = tmp_path / "valid_research.json"
    research_file.write_text(research.model_dump_json(indent=2), encoding="utf-8")

    written_data = json.loads(_make_written_draft_json(["A1", "A2", "A3"]))
    written_data["captions"][0]["cites"] = ["A9"]
    scripts_json = _make_scripts_draft_json(["A1", "A2", "A3"])

    fake_client = FakeLLMClient(responses=[json.dumps(written_data), scripts_json])
    monkeypatch.setattr("cyberpulse.cli.create_llm_client", lambda cfg: fake_client)
    monkeypatch.setenv("GEMINI_API_KEY", "mock-valid-key")

    ret = main(["editorial", "--research", str(research_file), "--settings", str(settings_path)])
    assert ret == 7

    captured = capsys.readouterr()
    assert "EditorialRefError" in captured.err
    assert "A9" in captured.err

