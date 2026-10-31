"""Tests for hashtag normalization (T4.2c).

Covers:
- Unit tests for the normalize_hashtags() function.
- Integration tests ensuring Caption/EditorialPackage models normalize
  correctly and that validate_package still flags genuine violations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cyberpulse.config import (
    EditorialConfig,
    FetchConfig,
    LLMConfig,
    NormalizeConfig,
    PathsConfig,
    ResearchConfig,
    Settings,
    ValidatorConfig,
)
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
    normalize_hashtags,
)
from cyberpulse.validator import validate_package


# ==============================================================================
# Unit tests for normalize_hashtags()
# ==============================================================================


class TestNormalizeHashtagsUnit:
    """Pure unit tests for the normalize_hashtags function."""

    def test_comma_joined_str_to_list(self) -> None:
        assert normalize_hashtags("#A,#B,#C") == ["#A", "#B", "#C"]

    def test_space_separated_str_to_list(self) -> None:
        assert normalize_hashtags("#A #B #C") == ["#A", "#B", "#C"]

    def test_list_unchanged(self) -> None:
        assert normalize_hashtags(["#A", "#B", "#C"]) == ["#A", "#B", "#C"]

    def test_missing_hash_prefixed(self) -> None:
        assert normalize_hashtags("AnNinhMang,CyberSecurity") == [
            "#AnNinhMang",
            "#CyberSecurity",
        ]

    def test_inner_whitespace_removed(self) -> None:
        # Inner whitespace within list elements is removed
        assert normalize_hashtags(["#An Ninh Mang", "#Cyber Security"]) == [
            "#AnNinhMang",
            "#CyberSecurity",
        ]

    def test_case_insensitive_dedup_keeps_first(self) -> None:
        result = normalize_hashtags(
            ["#CyberSecurity", "#cybersecurity", "#CYBERSECURITY"]
        )
        assert result == ["#CyberSecurity"]

    def test_empty_string_returns_empty_list(self) -> None:
        assert normalize_hashtags("") == []

    def test_none_returned_unchanged(self) -> None:
        assert normalize_hashtags(None) is None

    def test_int_returned_unchanged(self) -> None:
        assert normalize_hashtags(42) == 42

    def test_diacritics_and_casing_preserved(self) -> None:
        result = normalize_hashtags("#AnNinhMạng,#BảoMật")
        assert result == ["#AnNinhMạng", "#BảoMật"]

    def test_mixed_separators(self) -> None:
        result = normalize_hashtags("#A, #B\n#C \t #D")
        assert result == ["#A", "#B", "#C", "#D"]

    def test_newline_separated(self) -> None:
        assert normalize_hashtags("#A\n#B\n#C") == ["#A", "#B", "#C"]


# ==============================================================================
# Integration tests with Pydantic models and validator
# ==============================================================================

ID_1 = "1111111111111111"
ID_2 = "2222222222222222"
ID_3 = "3333333333333333"


def _make_settings() -> Settings:
    """Minimal Settings for offline validation."""
    return Settings(
        project_name="Test",
        topics=["cybersecurity"],
        items_per_run=3,
        lookback_hours=48,
        output_language="vi",
        log_level="INFO",
        paths=PathsConfig(
            data_dir=Path("data"),
            output_dir=Path("output"),
            log_dir=Path("logs"),
        ),
        fetch=FetchConfig(),
        normalize=NormalizeConfig(),
        llm=LLMConfig(provider="fake"),
        research=ResearchConfig(),
        editorial=EditorialConfig(
            podcast_target_words=300,
            video_max_total_sec=60,
            caption_platforms=["facebook", "youtube", "tiktok"],
            podcast_speakers=["Minh", "Lan"],
        ),
        validator=ValidatorConfig(
            llm_review=False,
            podcast_word_tolerance=0.2,
            section_words_min=60,
            section_words_max=120,
            term_allowlist=["Trung"],
        ),
    )


def _gen_words(count: int) -> str:
    filler = "thông tin an toàn mạng cảnh báo bảo mật cập nhật hệ thống rà soát khắc phục"
    words = filler.split()
    tokens: list[str] = []
    while len(tokens) < count:
        tokens.extend(words)
    return " ".join(tokens[:count])


def _make_research() -> ResearchResult:
    now = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)
    return ResearchResult(
        run_id="test-run",
        status="ok",
        requested_count=3,
        items=[
            ResearchItem(
                rank=r,
                relevance_reason=f"Reason {r} (mẫu).",
                article=Article(
                    id=aid,
                    source_name="Test Source",
                    title=f"SAMPLE — Article {r}",
                    url=f"https://example.com/sample/{r}",
                    published_at=now,
                    fetched_at=now,
                    summary=f"Summary for article {r}.",
                ),
            )
            for r, aid in [(1, ID_1), (2, ID_2), (3, ID_3)]
        ],
    )


def _make_editorial(
    caption_hashtags: list[object],
) -> EditorialPackage:
    """Build a valid EditorialPackage using the given hashtags per caption.

    ``caption_hashtags`` must have exactly 3 elements (one per platform).
    """
    body = _gen_words(70) + "."
    seg_text = _gen_words(150) + "."
    return EditorialPackage(
        run_id="test-run",
        language="vi",
        digest=Digest(
            title="Tiêu đề tin tức mẫu",
            intro="Giới thiệu bản tin mẫu.",
            sections=[
                DigestSection(
                    heading=f"Mục {i} mẫu",
                    body=CitedText(text=body, cited_article_ids=[aid]),
                )
                for i, aid in enumerate([ID_1, ID_2, ID_3], 1)
            ],
        ),
        podcast=PodcastScript(
            title="Podcast mẫu",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(
                        text=seg_text, cited_article_ids=[ID_1, ID_2, ID_3]
                    ),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(
                        text=seg_text, cited_article_ids=[ID_1, ID_2, ID_3]
                    ),
                ),
            ],
        ),
        video=VideoScript(
            title="Video mẫu",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="Cảnh báo bảo mật",
                    narration=CitedText(
                        text="Điểm tin an ninh mẫu.",
                        cited_article_ids=[ID_1, ID_2, ID_3],
                    ),
                    duration_sec=10.0,
                ),
                VideoScene(
                    scene_no=2,
                    on_screen_text="Cập nhật",
                    narration=CitedText(
                        text="Tin tức thứ hai mẫu.",
                        cited_article_ids=[ID_2],
                    ),
                    duration_sec=10.0,
                ),
            ],
        ),
        captions=[
            Caption(
                platform=plat,
                text=f"Nội dung mẫu cho {plat}.",
                hashtags=ht,  # type: ignore[arg-type]
                cited_article_ids=[ID_1, ID_2, ID_3],
            )
            for plat, ht in zip(
                ["facebook", "youtube", "tiktok"], caption_hashtags
            )
        ],
    )


class TestCaptionModelNormalization:
    """Integration tests verifying Caption model normalizes hashtags."""

    def test_caption_model_normalizes_comma_string(self) -> None:
        cap = Caption(
            platform="facebook",
            text="Test caption.",
            hashtags="#A,#B,#C,#D,#E",  # type: ignore[arg-type]
            cited_article_ids=[ID_1],
        )
        assert cap.hashtags == ["#A", "#B", "#C", "#D", "#E"]

    def test_editorial_package_with_comma_hashtags_no_error(self) -> None:
        """5 comma-joined hashtags per caption → no E_HASHTAG_FORMAT."""
        comma_tags = "#AnNinhMang,#CyberSecurity,#Binance,#Linux,#DataBreach"
        pkg = _make_editorial([comma_tags, comma_tags, comma_tags])
        # Verify normalization happened
        for cap in pkg.captions:
            assert isinstance(cap.hashtags, list)
            assert len(cap.hashtags) == 5

        # Validate — should produce NO E_HASHTAG_FORMAT
        report = validate_package(
            research=_make_research(),
            pkg=pkg,
            settings=_make_settings(),
            llm=None,
        )
        hashtag_errors = [
            issue for issue in report.errors if issue.code == "E_HASHTAG_FORMAT"
        ]
        assert hashtag_errors == [], f"Unexpected E_HASHTAG_FORMAT: {hashtag_errors}"

    def test_str_with_only_2_hashtags_still_flagged(self) -> None:
        """A comma-joined string with only 2 hashtags → E_HASHTAG_FORMAT."""
        two_tags = "#A,#B"
        five_tags = ["#A", "#B", "#C", "#D", "#E"]
        pkg = _make_editorial([two_tags, five_tags, five_tags])
        report = validate_package(
            research=_make_research(),
            pkg=pkg,
            settings=_make_settings(),
            llm=None,
        )
        hashtag_errors = [
            issue for issue in report.errors if issue.code == "E_HASHTAG_FORMAT"
        ]
        assert len(hashtag_errors) == 1
        assert "count=2" in hashtag_errors[0].evidence

    def test_list_with_only_2_hashtags_still_flagged(self) -> None:
        """A proper list with only 2 hashtags → E_HASHTAG_FORMAT."""
        two_tags = ["#A", "#B"]
        five_tags = ["#A", "#B", "#C", "#D", "#E"]
        pkg = _make_editorial([two_tags, five_tags, five_tags])
        report = validate_package(
            research=_make_research(),
            pkg=pkg,
            settings=_make_settings(),
            llm=None,
        )
        hashtag_errors = [
            issue for issue in report.errors if issue.code == "E_HASHTAG_FORMAT"
        ]
        assert len(hashtag_errors) == 1
        assert "count=2" in hashtag_errors[0].evidence
