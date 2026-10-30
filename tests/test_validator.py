"""Offline unit tests for Grounding Validator (TASK T4.2)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
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
from cyberpulse.llm import FakeLLMClient, LLMTransientError
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
from cyberpulse.validator import (
    ClaimFinding,
    ClaimReviewResponse,
    ValidationIssue,
    ValidationReport,
    validate_package,
)


# ==============================================================================
# Sample Data Builders (marked '(mẫu)', sample URLs)
# ==============================================================================

ID_1 = "1111111111111111"
ID_2 = "2222222222222222"
ID_3 = "3333333333333333"


def make_test_settings() -> Settings:
    """Return Settings tailored for unit testing."""
    return Settings(
        project_name="CyberPulse AI Test",
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
            llm_review=True,
            podcast_word_tolerance=0.2,
            section_words_min=60,
            section_words_max=120,
            term_allowlist=["Trung"],
        ),
    )


def make_test_research(run_id: str = "sample-run-001") -> ResearchResult:
    """Build a valid sample ResearchResult with 3 items."""
    now = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)
    return ResearchResult(
        run_id=run_id,
        status="ok",
        requested_count=3,
        items=[
            ResearchItem(
                rank=1,
                relevance_reason="Lỗ hổng Node2Shell trong Brotli có điểm CVSS 8.2 và CVE-2026-9999 (mẫu).",
                article=Article(
                    id=ID_1,
                    source_name="The Hacker News",
                    title="SAMPLE — Critical Brotli Flaw Node2Shell Disclosed with CVSS 8.2",
                    url="https://example.com/sample/node2shell",
                    published_at=now,
                    fetched_at=now,
                    summary=(
                        "CISA warned that three vulnerabilities including CVE-2026-9999 were discovered. "
                        "FoxIO reported active exploitation of Node2Shell in servers."
                    ),
                ),
            ),
            ResearchItem(
                rank=2,
                relevance_reason="Palo Alto Networks vá lỗi PHP2Shell (mẫu).",
                article=Article(
                    id=ID_2,
                    source_name="BleepingComputer",
                    title="SAMPLE — Palo Alto Networks Patches PHP2Shell Flaw",
                    url="https://example.com/sample/php2shell",
                    published_at=now,
                    fetched_at=now,
                    summary="Palo Alto Networks fixed 50 bugs in firewalls and management interfaces.",
                ),
            ),
            ResearchItem(
                rank=3,
                relevance_reason="Broadcom vá lỗ hổng zero-day VMware ESX CVE-2026-8888 (mẫu).",
                article=Article(
                    id=ID_3,
                    source_name="SecurityWeek",
                    title="SAMPLE — VMware ESX Zero-Day Exploited by Threat Actor",
                    url="https://example.com/sample/vmware-esx",
                    published_at=now,
                    fetched_at=now,
                    summary="Broadcom addressed zero-day CVE-2026-8888 in VMware ESX exploited by hackers.",
                ),
            ),
        ],
    )


def _gen_vietnamese_words(count: int, prefix: str = "nội dung tin tức") -> str:
    """Generate approx word count in Vietnamese for testing."""
    filler = "thông tin an toàn mạng cảnh báo bảo mật cập nhật hệ thống rà soát khắc phục"
    words = filler.split()
    tokens = [prefix]
    while len(tokens) < count:
        tokens.extend(words)
    return " ".join(tokens[:count])


def make_test_editorial(run_id: str = "sample-run-001") -> EditorialPackage:
    """Build a valid sample EditorialPackage conforming to make_test_research."""
    # 70 words per section body (within [60, 120])
    # Section 1 contains CVSS 8,2 (source 8.2), 3 (source three), CVE-2026-9999, Node2Shell, FoxIO, CISA
    body1_text = (
        "Cơ quan CISA cùng FoxIO phát đi cảnh báo khẩn cấp về lỗ hổng Node2Shell với mã định danh "
        "CVE-2026-9999 có điểm đánh giá là 8,2. Đợt rà soát này ghi nhận 3 vấn đề an ninh nghiêm trọng "
        "đang bị kẻ tấn công khai thác trên diện rộng. "
        + _gen_vietnamese_words(30, "(mẫu) các chuyên gia an ninh mạng khuyến cáo tổ chức thực hiện vá")
    )
    # Ensure word count is ~70
    tokens1 = body1_text.split()
    if len(tokens1) < 65:
        tokens1.extend(_gen_vietnamese_words(70 - len(tokens1)).split())
    body1 = " ".join(tokens1[:75]) + "."

    # Section 2 contains PHP2Shell, 50 (source 50), Palo Alto Networks
    body2_text = (
        "Hãng Palo Alto Networks vừa chính thức phát hành bản cập nhật nhằm khắc phục lỗ hổng PHP2Shell "
        "cùng 50 lỗi bảo mật khác trên hệ thống tường lửa và giao diện quản lý. "
        "(Management Interfaces - giao diện cho phép cấu hình quản trị) "
        + _gen_vietnamese_words(35, "(mẫu) quản trị viên cần triển khai cài đặt các bản vá kịp thời")
    )
    tokens2 = body2_text.split()
    if len(tokens2) < 65:
        tokens2.extend(_gen_vietnamese_words(70 - len(tokens2)).split())
    body2 = " ".join(tokens2[:75]) + "."

    # Section 3 contains Broadcom, VMware, ESX, CVE-2026-8888, and allowlisted term "Trung"
    body3_text = (
        "Tập đoàn Broadcom đã cung cấp bản sửa lỗi cho lỗ hổng zero-day CVE-2026-8888 trên hệ thống VMware ESX. "
        "Vụ việc được phát hiện có liên quan đến nhóm tin tặc từ Trung Quốc trong chiến dịch gián điệp mạng. "
        + _gen_vietnamese_words(35, "(mẫu) doanh nghiệp cần kiểm tra nhật ký ghi nhận để phát hiện truy cập")
    )
    tokens3 = body3_text.split()
    if len(tokens3) < 65:
        tokens3.extend(_gen_vietnamese_words(70 - len(tokens3)).split())
    body3 = " ".join(tokens3[:75]) + "."

    # Podcast segments: target 300 words (±20% -> 240-360 words)
    # Speaker Minh: ~140 words, Lan: ~140 words -> total ~280 words
    seg1 = (
        "Chào Lan và xin chào quý thính giả đang lắng nghe bản tin. "
        "Hôm nay chúng ta điểm qua 3 tin tức bảo mật quan trọng nhất gồm có "
        "Node2Shell trên Brotli, bản vá của Palo Alto Networks và lỗ hổng trên VMware ESX. "
        + _gen_vietnamese_words(100, "(mẫu) các thông tin an ninh mạng cập nhật mới nhất cho doanh nghiệp")
    )
    seg2 = (
        "Chào anh Minh. Đúng vậy, các quản trị viên hệ thống cần đặc biệt lưu ý kiểm tra bản vá "
        "và cập nhật ngay các thiết bị để đảm bảo an toàn thông tin cho toàn bộ mạng nội bộ doanh nghiệp. "
        + _gen_vietnamese_words(100, "(mẫu) chúng tôi sẽ tiếp tục theo dõi diễn biến tình hình bảo mật")
    )

    return EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(
            title="Điểm tin An ninh mạng: Cảnh báo các lỗ hổng nghiêm trọng (mẫu)",
            intro=(
                "Bản tin an ninh mạng hôm nay tổng hợp 3 sự kiện đáng chú ý liên quan đến "
                "lỗ hổng Node2Shell, bản vá từ Palo Alto Networks và sự cố VMware ESX (mẫu)."
            ),
            sections=[
                DigestSection(
                    heading="Cảnh báo lỗ hổng Node2Shell trong thư viện Brotli (mẫu)",
                    body=CitedText(text=body1, cited_article_ids=[ID_1]),
                ),
                DigestSection(
                    heading="Palo Alto Networks vá lỗi bảo mật PHP2Shell (mẫu)",
                    body=CitedText(text=body2, cited_article_ids=[ID_2]),
                ),
                DigestSection(
                    heading="Phát hiện khai thác zero-day trên VMware ESX (mẫu)",
                    body=CitedText(text=body3, cited_article_ids=[ID_3]),
                ),
            ],
        ),
        podcast=PodcastScript(
            title="Podcast An ninh mạng số phát sóng đặc biệt (mẫu)",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(text=seg1, cited_article_ids=[ID_1, ID_2, ID_3]),
                ),
                PodcastSegment(
                    speaker="Lan",
                    line=CitedText(text=seg2, cited_article_ids=[ID_1, ID_2, ID_3]),
                ),
            ],
        ),
        video=VideoScript(
            title="Cảnh báo an ninh mạng nhanh (mẫu)",
            scenes=[
                VideoScene(
                    scene_no=1,
                    on_screen_text="CẢNH BÁO BẢO MẬT (mẫu)",
                    narration=CitedText(
                        text="Điểm qua 3 tin nóng bảo mật bạn cần biết ngay hôm nay! (mẫu)",
                        cited_article_ids=[ID_1, ID_2, ID_3],
                    ),
                    duration_sec=8.0,
                ),
                VideoScene(
                    scene_no=2,
                    on_screen_text="Lỗ hổng Node2Shell",
                    narration=CitedText(
                        text="CISA cảnh báo lỗ hổng Node2Shell đang bị khai thác! (mẫu)",
                        cited_article_ids=[ID_1],
                    ),
                    duration_sec=10.0,
                ),
                VideoScene(
                    scene_no=3,
                    on_screen_text="Palo Alto Networks vá lỗi",
                    narration=CitedText(
                        text="Palo Alto Networks đã phát hành bản vá PHP2Shell. (mẫu)",
                        cited_article_ids=[ID_2],
                    ),
                    duration_sec=10.0,
                ),
                VideoScene(
                    scene_no=4,
                    on_screen_text="CẬP NHẬT BẢN VÁ NGAY",
                    narration=CitedText(
                        text="Hãy cập nhật hệ thống ngay để giữ an toàn thông tin! (mẫu)",
                        cited_article_ids=[ID_1, ID_2, ID_3],
                    ),
                    duration_sec=8.0,
                ),
            ],
        ),
        captions=[
            Caption(
                platform="facebook",
                text="Điểm tin an ninh mạng nổi bật hôm nay: CISA cảnh báo Node2Shell, Palo Alto Networks vá PHP2Shell và VMware ESX (mẫu).",
                hashtags=["#AnNinhMang", "#BaoMat", "#Node2Shell", "#CyberPulse"],
                cited_article_ids=[ID_1, ID_2, ID_3],
            ),
            Caption(
                platform="youtube",
                text="Bản tin công nghệ và an toàn thông tin: Cập nhật các lỗ hổng bảo mật mới nhất từ CISA và các hãng công nghệ lớn (mẫu).",
                hashtags=["#CyberSecurity", "#TechNews", "#AnToanThongTin"],
                cited_article_ids=[ID_1, ID_2, ID_3],
            ),
            Caption(
                platform="tiktok",
                text="3 cảnh báo an ninh mạng quan trọng nhất trong tuần bạn cần cập nhật ngay! (mẫu)",
                hashtags=["#TikTokNews", "#BaoMat", "#CongNghe"],
                cited_article_ids=[ID_1, ID_2, ID_3],
            ),
        ],
    )


# ==============================================================================
# Acceptance Criterion 1: Valid Baseline Package Passes with 0 Errors
# ==============================================================================


def test_valid_baseline_package_passes() -> None:
    """AC 1: A valid sample package -> passed=True, zero errors."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()

    report = validate_package(research, pkg, settings, llm=None)

    assert report.passed is True
    assert len(report.errors) == 0
    assert report.llm_review == "skipped"
    assert "layer1_structural" in report.checks_run
    assert "layer2_source_consistency" in report.checks_run
    assert report.scope_note.startswith("Structural and source-consistency checks")


# ==============================================================================
# Acceptance Criterion 2: Every Error Code Triggered with Expected Path
# ==============================================================================


def test_error_e_run_id_mismatch() -> None:
    settings = make_test_settings()
    research = make_test_research(run_id="run-A")
    pkg = make_test_editorial(run_id="run-B")

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    codes = [e.code for e in report.errors]
    assert "E_RUN_ID_MISMATCH" in codes
    issue = next(e for e in report.errors if e.code == "E_RUN_ID_MISMATCH")
    assert issue.path == "run_id"
    assert "run-A != run-B" in issue.evidence


def test_error_e_cite_unknown() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Mutate a cited id to unknown
    pkg.digest.sections[0].body.cited_article_ids = ["9999999999999999"]

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_CITE_UNKNOWN")
    assert issue.path == "digest.sections[0].body.cited_article_ids"
    assert issue.evidence == "9999999999999999"


def test_error_e_section_count() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Drop one section so 2 sections != 3 research items
    pkg.digest.sections = pkg.digest.sections[:2]

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_SECTION_COUNT")
    assert issue.path == "digest.sections"
    assert "2 != 3" in issue.evidence


def test_error_e_section_cite_wrong_rank() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Section 0 cites article 2 instead of article 1
    pkg.digest.sections[0].body.cited_article_ids = [ID_2]

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_SECTION_CITE")
    assert issue.path == "digest.sections[0].body.cited_article_ids"
    assert issue.evidence == ID_1


def test_error_e_article_uncovered() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Section 2 cites ID_2 instead of ID_3 -> ID_3 uncovered
    pkg.digest.sections[2].body.cited_article_ids = [ID_2]

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_ARTICLE_UNCOVERED")
    assert issue.path == "digest.sections"
    assert issue.evidence == ID_3


@pytest.mark.parametrize(
    "leaked_token,pattern_type",
    [
        ("https://malicious.com", "url"),
        ("http://example.com/api", "url"),
        ("www.malicious.org", "www"),
        ("contact@cyberpulse.vn", "email"),
        ("a1b2c3d4e5f60718", "hex_id"),
    ],
)
def test_error_e_url_or_id_in_text(leaked_token: str, pattern_type: str) -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.podcast.segments[0].line.text += f" Xem thêm tại {leaked_token}."

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_URL_OR_ID_IN_TEXT")
    assert issue.path == "podcast.segments[0].line.text"
    assert leaked_token in issue.evidence


def test_error_e_caption_platform_missing_and_duplicate() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Replace tiktok caption with duplicate facebook caption
    pkg.captions[2] = copy.deepcopy(pkg.captions[0])

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    codes = [e.code for e in report.errors]
    assert codes.count("E_CAPTION_PLATFORM") >= 2
    # Missing tiktok
    missing = next(e for e in report.errors if e.code == "E_CAPTION_PLATFORM" and e.evidence == "tiktok")
    assert missing.path == "captions"
    # Duplicate facebook
    dup = next(e for e in report.errors if e.code == "E_CAPTION_PLATFORM" and e.path == "captions[2].platform")
    assert dup.evidence == "facebook"


def test_error_e_caption_length() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # TikTok limit is 250 characters
    pkg.captions[2].text = "A" * 260

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_CAPTION_LENGTH")
    assert issue.path == "captions[2].text"
    assert "260" in issue.evidence


@pytest.mark.parametrize(
    "bad_hashtags,expected_path",
    [
        (["#one", "#two"], "captions[0].hashtags"),  # count 2 < 3
        (["#one", "#two", "#three", "#four", "#five", "#six", "#seven"], "captions[0].hashtags"),  # count 7 > 6
        (["#valid1", "invalid_no_hash", "#valid3"], "captions[0].hashtags[1]"),  # no leading #
        (["#valid1", "#has space", "#valid3"], "captions[0].hashtags[1]"),  # space in tag
    ],
)
def test_error_e_hashtag_format(bad_hashtags: list[str], expected_path: str) -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.captions[0].hashtags = bad_hashtags

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_HASHTAG_FORMAT")
    assert issue.path == expected_path


def test_error_e_speaker_unknown() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.podcast.segments[0].speaker = "Nam"  # configured: Minh, Lan

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_SPEAKER_UNKNOWN")
    assert issue.path == "podcast.segments[0].speaker"
    assert issue.evidence == "Nam"


def test_error_e_video_total() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # max is 60s in test_settings; make scenes 20 + 20 + 20 + 10 = 70s
    for sc in pkg.video.scenes:
        sc.duration_sec = 18.0

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_VIDEO_TOTAL")
    assert issue.path == "video.scenes"
    assert "72.0s > 60s" in issue.evidence


@pytest.mark.parametrize("bad_duration", [3.5, 21.0])
def test_error_e_scene_duration(bad_duration: float) -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.video.scenes[0].duration_sec = bad_duration

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_SCENE_DURATION")
    assert issue.path == "video.scenes[0].duration_sec"
    assert str(bad_duration) in issue.evidence


def test_error_e_onscreen_length() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.video.scenes[0].on_screen_text = "N" * 65

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_ONSCREEN_LENGTH")
    assert issue.path == "video.scenes[0].on_screen_text"


def test_warning_w_podcast_length() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Target is 100 ± 20% (80 - 120 words). Shorten to 40 words:
    pkg.podcast.segments[0].line.text = "Chào Lan ngắn gọn."
    pkg.podcast.segments[1].line.text = "Chào Minh ngắn gọn."

    report = validate_package(research, pkg, settings, llm=None)
    # Warnings do not fail report
    assert report.passed is True
    issue = next(w for w in report.warnings if w.code == "W_PODCAST_LENGTH")
    assert issue.path == "podcast.segments"
    assert "expected 240-360" in issue.evidence


def test_warning_w_section_length() -> None:
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Min is 60 words, max 120 words. Truncate section 0 to 20 words:
    pkg.digest.sections[0].body.text = (
        "Cơ quan CISA cùng FoxIO phát đi cảnh báo khẩn cấp về lỗ hổng Node2Shell "
        "với mã định danh CVE-2026-9999 có điểm đánh giá là 8,2."
    )

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is True
    issue = next(w for w in report.warnings if w.code == "W_SECTION_LENGTH")
    assert issue.path == "digest.sections[0].body.text"
    assert "words" in issue.evidence


# ==============================================================================
# Acceptance Criterion 3: E_NUMBER_UNSUPPORTED Nuances
# ==============================================================================


def test_ac3_number_unsupported_not_in_sources() -> None:
    """E_NUMBER_UNSUPPORTED triggers for a number not in sources."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Add an unsupported number "999" into section 1
    pkg.digest.sections[0].body.text += " Con số ảnh hưởng là 999 thiết bị."

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.path == "digest.sections[0].body.text"
    assert issue.evidence == "999"


def test_ac3_number_supported_present_in_cited_source() -> None:
    """E_NUMBER_UNSUPPORTED does NOT trigger for a number present in the cited source."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Section 1 contains 8,2 (source has 8.2), 3 (source has three), and item count 3
    # Check that section 1 has no E_NUMBER_UNSUPPORTED
    report = validate_package(research, pkg, settings, llm=None)
    section1_num_errors = [
        e for e in report.errors
        if e.code == "E_NUMBER_UNSUPPORTED" and e.path == "digest.sections[0].body.text"
    ]
    assert len(section1_num_errors) == 0


def test_ac3_number_supported_word_to_digit() -> None:
    """E_NUMBER_UNSUPPORTED does NOT trigger for 'three' in source vs '3' in text."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Source 1 has 'three vulnerabilities', block 1 uses digit '3'
    assert "three" in research.items[0].article.summary.lower()
    assert "3" in pkg.digest.sections[0].body.text

    report = validate_package(research, pkg, settings, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED" and e.evidence == "3"]
    assert len(num_errors) == 0


def test_ac3_number_supported_comma_decimal() -> None:
    """E_NUMBER_UNSUPPORTED does NOT trigger for '8,2' vs '8.2'."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    assert "8.2" in research.items[0].article.title
    assert "8,2" in pkg.digest.sections[0].body.text

    report = validate_package(research, pkg, settings, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED" and "8" in e.evidence]
    assert len(num_errors) == 0


def test_ac3_number_supported_item_count() -> None:
    """E_NUMBER_UNSUPPORTED does NOT trigger for the research item count."""
    settings = make_test_settings()
    research = make_test_research()
    # 3 research items
    pkg = make_test_editorial()
    # Add "3" into section 2 (which cites article 2 that does not contain "3")
    pkg.digest.sections[1].body.text += " Gồm có 3 khuyến nghị an toàn."

    report = validate_package(research, pkg, settings, llm=None)
    sec2_num_errors = [
        e for e in report.errors
        if e.code == "E_NUMBER_UNSUPPORTED" and e.path == "digest.sections[1].body.text"
    ]
    assert len(sec2_num_errors) == 0


# ==============================================================================
# Acceptance Criterion 4: E_CVE_UNSUPPORTED Checks Own Cited Sources
# ==============================================================================


def test_ac4_cve_unsupported_cross_article() -> None:
    """E_CVE_UNSUPPORTED checks against the block's own cited sources, not all articles."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # CVE-2026-8888 exists in article 3, but section 1 only cites article 1!
    pkg.digest.sections[0].body.text += " Đồng thời có nhắc đến CVE-2026-8888."

    report = validate_package(research, pkg, settings, llm=None)
    assert report.passed is False
    issue = next(
        e for e in report.errors
        if e.code == "E_CVE_UNSUPPORTED" and e.path == "digest.sections[0].body.text"
    )
    assert issue.evidence == "CVE-2026-8888"


# ==============================================================================
# Acceptance Criterion 5: W_TERM_UNSUPPORTED Nuances
# ==============================================================================


def test_ac5_term_unsupported_triggers_for_absent_acronym() -> None:
    """W_TERM_UNSUPPORTED triggers for an acronym absent from sources."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Add ungrounded acronym "BGP" or "IoT" into section 1
    pkg.digest.sections[0].body.text += " Đe dọa nghiêm trọng đến thiết bị IoT."

    report = validate_package(research, pkg, settings, llm=None)
    issue = next(
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "IoT"
    )
    assert issue.path == "digest.sections[0].body.text"


def test_ac5_term_inside_parentheses_excluded() -> None:
    """W_TERM_UNSUPPORTED does NOT trigger for terms inside parentheses."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Section 2 contains '(Management Interfaces - giao diện cho phép cấu hình quản trị)'
    # Management and Interfaces are mid-sentence capitalized terms not in source
    report = validate_package(research, pkg, settings, llm=None)
    parenthesis_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence in {"Management", "Interfaces"}
    ]
    assert len(parenthesis_warnings) == 0


def test_ac5_sentence_initial_capitalized_word_not_triggered() -> None:
    """W_TERM_UNSUPPORTED does NOT trigger for sentence-initial capitalized words."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # Add a sentence starting with an English capitalized word not in source, e.g. "Security là..."
    pkg.digest.sections[0].body.text += " Security là yếu tố hàng đầu của tổ chức."

    report = validate_package(research, pkg, settings, llm=None)
    sec_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "Security"
    ]
    assert len(sec_warnings) == 0


def test_ac5_speaker_names_and_allowlist_not_triggered() -> None:
    """W_TERM_UNSUPPORTED does NOT trigger for speaker names or allowlisted terms."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    # "Minh" and "Lan" in podcast segments; "Trung" in section 3 ("Trung Quốc")
    report = validate_package(research, pkg, settings, llm=None)
    allowlisted_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence in {"Minh", "Lan", "Trung"}
    ]
    assert len(allowlisted_warnings) == 0


def test_w_term_unsupported_headline_title_case_vietnamese_no_warning() -> None:
    """Title Case Vietnamese headline in podcast.title does not trigger W_TERM_UNSUPPORTED."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.podcast.title = "Điểm Tin An Ninh Mạng: Khai Thác Mới"

    report = validate_package(research, pkg, settings, llm=None)
    term_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.path == "podcast.title"
    ]
    assert len(term_warnings) == 0


def test_w_term_unsupported_all_caps_overlay_no_warning() -> None:
    """ALL-CAPS overlay in on_screen_text does not trigger W_TERM_UNSUPPORTED."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.video.scenes[0].on_screen_text = "CẬP NHẬT NGAY"

    report = validate_package(research, pkg, settings, llm=None)
    overlay_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.path == "video.scenes[0].on_screen_text"
    ]
    assert len(overlay_warnings) == 0


def test_w_term_unsupported_clause_start_after_colon_no_warning() -> None:
    """A capitalized word starting a clause after a colon is not flagged."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.captions[0].text = "Cảnh báo: Khai thác lỗ hổng bảo mật nghiêm trọng trong máy chủ."

    report = validate_package(research, pkg, settings, llm=None)
    khai_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "Khai"
    ]
    assert len(khai_warnings) == 0


def test_w_term_unsupported_mid_sentence_acronym_still_flagged() -> None:
    """Mid-sentence acronym absent from sources remains flagged in podcast line."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.podcast.segments[0].line.text += " Nguy hiểm cho thiết bị IoT."

    report = validate_package(research, pkg, settings, llm=None)
    iot_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "IoT"
    ]
    assert len(iot_warnings) == 1
    assert iot_warnings[0].path == "podcast.segments[0].line.text"


def test_w_term_unsupported_mixed_case_alphanumeric_in_title_still_flagged() -> None:
    """Mixed-case/alphanumeric token absent from source in title remains flagged."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.podcast.title = "Cảnh Báo Lỗ Hổng PHP9Shell Nguy Hiểm"

    report = validate_package(research, pkg, settings, llm=None)
    php_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "PHP9Shell"
    ]
    assert len(php_warnings) == 1
    assert php_warnings[0].path == "podcast.title"


def test_w_term_unsupported_mid_sentence_proper_noun_still_flagged() -> None:
    """Mid-sentence capitalized ASCII proper noun absent from source remains flagged."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()
    pkg.digest.sections[0].body.text += " Vụ việc xảy ra theo báo cáo của Contoso."

    report = validate_package(research, pkg, settings, llm=None)
    contoso_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "Contoso"
    ]
    assert len(contoso_warnings) == 1
    assert contoso_warnings[0].path == "digest.sections[0].body.text"


# ==============================================================================
# Acceptance Criterion 6: Layer 3 Claim Review
# ==============================================================================


def test_ac6_claim_review_valid_findings() -> None:
    """Valid findings become warnings W_CLAIM_<ISSUE>."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()

    target_quote = "3 vấn đề an ninh nghiêm trọng"
    fake_response = ClaimReviewResponse(
        findings=[
            ClaimFinding(
                path="digest.sections[0].body.text",
                quote=target_quote,
                issue="overstated",
                explanation_vi="Nguồn chỉ nêu ghi nhận lỗi, không khẳng định mức độ nghiêm trọng trên diện rộng.",
            )
        ]
    )

    fake_client = FakeLLMClient(responses=[fake_response.model_dump_json()])
    report = validate_package(research, pkg, settings, llm=fake_client)

    assert report.llm_review == "done"
    issue = next(w for w in report.warnings if w.code == "W_CLAIM_OVERSTATED")
    assert issue.path == "digest.sections[0].body.text"
    assert issue.evidence == target_quote
    assert "Nguồn chỉ nêu" in issue.message


def test_ac6_claim_review_drops_non_substring_or_unknown_path() -> None:
    """Findings with a non-substring quote or unknown path are dropped."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()

    fake_response = ClaimReviewResponse(
        findings=[
            ClaimFinding(
                path="digest.sections[0].body.text",
                quote="câu trích dẫn này hoàn toàn không tồn tại trong văn bản",
                issue="not_in_source",
                explanation_vi="Không có trong nguồn.",
            ),
            ClaimFinding(
                path="unknown.invalid.path[99]",
                quote="FoxIO",
                issue="misattributed",
                explanation_vi="Đường dẫn không hợp lệ.",
            ),
        ]
    )

    fake_client = FakeLLMClient(responses=[fake_response.model_dump_json()])
    report = validate_package(research, pkg, settings, llm=fake_client)

    assert report.llm_review == "done"
    claim_warnings = [w for w in report.warnings if w.code.startswith("W_CLAIM_")]
    assert len(claim_warnings) == 0
    # Dropped findings should be recorded in checks_run
    layer3_check = next(c for c in report.checks_run if "layer3_llm_claim_review" in c)
    assert "dropped=2" in layer3_check


def test_ac6_claim_review_llm_error_graceful() -> None:
    """LLMError -> llm_review='failed', W_LLM_REVIEW_UNAVAILABLE, layers 1-2 reported."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()

    fake_client = FakeLLMClient(
        responses=[LLMTransientError("API timeout")],
        cfg=LLMConfig(provider="fake", max_retries=0),
    )
    report = validate_package(research, pkg, settings, llm=fake_client)

    assert report.llm_review == "failed"
    assert report.passed is True  # No structural/source errors
    issue = next(w for w in report.warnings if w.code == "W_LLM_REVIEW_UNAVAILABLE")
    assert issue.evidence == "LLMTransientError"
    assert "API timeout" in issue.message


def test_ac6_claim_review_skipped_when_llm_none() -> None:
    """llm=None -> llm_review='skipped'."""
    settings = make_test_settings()
    research = make_test_research()
    pkg = make_test_editorial()

    report = validate_package(research, pkg, settings, llm=None)
    assert report.llm_review == "skipped"
    assert any("skipped" in c for c in report.checks_run)


def test_ac6_claim_review_skipped_when_config_false() -> None:
    """settings.validator.llm_review=False -> llm_review='skipped'."""
    settings = make_test_settings()
    settings.validator.llm_review = False
    research = make_test_research()
    pkg = make_test_editorial()

    fake_client = FakeLLMClient(responses=["should not be called"])
    report = validate_package(research, pkg, settings, llm=fake_client)

    assert report.llm_review == "skipped"
    assert len(fake_client.calls) == 0
