"""Offline unit tests for validator number and term equivalences (TASK T4.2d)."""

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
)
from cyberpulse.validator import (
    _classify_and_canonicalize,
    _drop_list_enumerators,
    _extract_candidate_terms,
    _extract_numbers,
    _get_source_numbers,
    validate_package,
)


# ==============================================================================
# Hermetic synthetic builders (self-contained, no network, no external imports)
# ==============================================================================


def _make_synthetic_settings() -> Settings:
    return Settings(
        project_name="CyberPulse Test Numbers",
        topics=["cybersecurity"],
        items_per_run=1,
        lookback_hours=48,
        output_language="vi",
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
            podcast_word_tolerance=0.5,
            section_words_min=20,
            section_words_max=200,
            term_allowlist=["Trung"],
        ),
    )


def _make_synthetic_bundle(
    source_title: str,
    source_summary: str,
    editorial_text: str,
    extra_articles: list[tuple[str, str, str]] | None = None,
    cited_ids: list[str] | None = None,
    run_id: str = "synthetic-run-001",
) -> tuple[ResearchResult, EditorialPackage, Settings]:
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    primary_id = "1111222233334444"

    items: list[ResearchItem] = [
        ResearchItem(
            rank=1,
            relevance_reason="lý do an ninh mạng",
            article=Article(
                id=primary_id,
                source_name="Primary Source",
                title=source_title,
                url="https://example.com/primary",
                published_at=now,
                fetched_at=now,
                summary=source_summary,
            ),
        )
    ]

    if extra_articles:
        for idx, (art_id, extra_title, extra_summary) in enumerate(extra_articles, 2):
            items.append(
                ResearchItem(
                    rank=idx,
                    relevance_reason="lý do phụ",
                    article=Article(
                        id=art_id,
                        source_name=f"Extra Source {idx}",
                        title=extra_title,
                        url=f"https://example.com/extra-{idx}",
                        published_at=now,
                        fetched_at=now,
                        summary=extra_summary,
                    ),
                )
            )

    res = ResearchResult(
        run_id=run_id,
        status="ok",
        requested_count=len(items),
        items=items,
    )

    use_cites = [primary_id] if cited_ids is None else cited_ids
    body_text = f"{editorial_text} " + " ".join(["nội dung an toàn thông tin"] * 10)

    sections = [
        DigestSection(
            heading=f"tiêu đề mục {i+1}",
            body=CitedText(
                text=body_text if i == 0 else "nội dung chi tiết mục khác bảo đảm an toàn hệ thống thông tin dữ liệu",
                cited_article_ids=use_cites if i == 0 else [items[i].article.id],
            ),
        )
        for i in range(len(items))
    ]

    pkg = EditorialPackage(
        run_id=run_id,
        language="vi",
        digest=Digest(
            title="tiêu đề điểm tin an ninh mạng",
            intro="lời mở đầu điểm tin an ninh mạng",
            sections=sections,
        ),
        podcast=PodcastScript(
            title="podcast an ninh mạng",
            segments=[
                PodcastSegment(
                    speaker="Minh",
                    line=CitedText(
                        text=" ".join(["chúng tôi cập nhật thông tin an toàn không gian mạng"] * 25),
                        cited_article_ids=use_cites,
                    ),
                )
            ],
        ),
        video=VideoScript(
            title="video an ninh mạng",
            scenes=[
                VideoScene(
                    scene_no=1,
                    duration_sec=10.0,
                    visual_prompt="hình minh họa",
                    on_screen_text="điểm tin an ninh mạng",
                    narration=CitedText(
                        text="nội dung video ngắn gọn cập nhật tình hình an toàn thông tin mạng.",
                        cited_article_ids=use_cites,
                    ),
                )
            ],
        ),
        captions=[
            Caption(
                platform="facebook",
                text="tóm tắt điểm tin bảo mật an toàn mạng hôm nay.",
                hashtags=["#cyber", "#security", "#news"],
                cited_article_ids=use_cites,
            ),
            Caption(
                platform="youtube",
                text="tóm tắt điểm tin bảo mật an toàn mạng hôm nay trên video.",
                hashtags=["#cyber", "#security", "#news"],
                cited_article_ids=use_cites,
            ),
            Caption(
                platform="tiktok",
                text="tóm tắt điểm tin ngắn trên mạng.",
                hashtags=["#cyber", "#security", "#news"],
                cited_article_ids=use_cites,
            ),
        ],
    )
    return res, pkg, _make_synthetic_settings()


# ==============================================================================
# Helper Unit Tests (Testing Real Helpers Directly)
# ==============================================================================


def test_classify_and_canonicalize_precedence_order() -> None:
    """Verify P4 ordered classification: thousands -> version -> decimal -> integer."""
    # 2) Thousands pattern: digits only
    assert _classify_and_canonicalize("63.000") == ("thousands", "63000")
    assert _classify_and_canonicalize("63,000") == ("thousands", "63000")
    assert _classify_and_canonicalize("1.000.000") == ("thousands", "1000000")
    assert _classify_and_canonicalize("1,000,000") == ("thousands", "1000000")
    assert _classify_and_canonicalize("1.313") == ("thousands", "1313")
    assert _classify_and_canonicalize("1,313") == ("thousands", "1313")

    # 3) Version pattern: atomic token
    assert _classify_and_canonicalize("2.1.313") == ("version", "2.1.313")
    assert _classify_and_canonicalize("7.1.3") == ("version", "7.1.3")

    # 4) Decimals: uses "."
    assert _classify_and_canonicalize("6,3") == ("decimal", "6.3")
    assert _classify_and_canonicalize("6.3") == ("decimal", "6.3")
    assert _classify_and_canonicalize("9.8") == ("decimal", "9.8")
    assert _classify_and_canonicalize("10.1234") == ("decimal", "10.1234")

    # 5) Integers
    assert _classify_and_canonicalize("10") == ("integer", "10")
    assert _classify_and_canonicalize("101234") == ("integer", "101234")
    assert _classify_and_canonicalize("50") == ("integer", "50")


def test_p4_thousands_and_versions_equality_and_atomicity() -> None:
    """Tests from P4: '1.000.000' vs '1,000,000' equal; '2.1.313' and '7.1.3' stay atomic."""
    k1, c1 = _classify_and_canonicalize("1.000.000")
    k2, c2 = _classify_and_canonicalize("1,000,000")
    assert k1 == k2 == "thousands"
    assert c1 == c2 == "1000000"

    k3, c3 = _classify_and_canonicalize("2.1.313")
    assert k3 == "version"
    assert c3 == "2.1.313"

    k4, c4 = _classify_and_canonicalize("7.1.3")
    assert k4 == "version"
    assert c4 == "7.1.3"


def test_p5_list_enumerators_dropping_rule() -> None:
    r"""P5: drop \d{1,2}[.)] ONLY when block contains consecutive run starting at 1 of >=2 items."""
    # 3-item list -> enumerators dropped
    three_items = "1. Điểm tin một\n2. Điểm tin hai\n3. Điểm tin ba"
    assert _drop_list_enumerators(three_items) == "   Điểm tin một\n   Điểm tin hai\n   Điểm tin ba"
    assert _extract_numbers(three_items) == []

    # Single line starting with 10. -> not dropped, 10 extracted
    single_10 = "10. Tin tức bảo mật"
    assert _drop_list_enumerators(single_10) == "10. Tin tức bảo mật"
    assert _extract_numbers(single_10) == ["10"]

    # List starting at 2 -> not dropped
    start_at_2 = "2. Điểm tin hai\n3. Điểm tin ba"
    assert _drop_list_enumerators(start_at_2) == "2. Điểm tin hai\n3. Điểm tin ba"
    assert _extract_numbers(start_at_2) == ["2", "3"]


def test_p3_editorial_magnitude_token_is_number_never_term() -> None:
    """P3: <number><suffix> (e.g. '6.3bn') is a number, never candidate term."""
    editorial = "Binance chịu tổn thất 6.3bn USD trong vụ hack"
    # Extracted numbers must find 6.3
    nums = _extract_numbers(editorial)
    assert "6.3" in nums
    # Candidate terms must contain USD, but never '6.3bn' or '3bn'
    terms = _extract_candidate_terms(editorial)
    assert "USD" in terms
    assert "6.3bn" not in terms
    assert "3bn" not in terms


# ==============================================================================
# Acceptance Criteria 2: Positive Equivalence Tests
# ==============================================================================


def test_ac2_glued_currency_and_magnitude_dollars_and_bn() -> None:
    """Source '$6.3bn' vs editorial '6,3 tỷ': passes without E_NUMBER_UNSUPPORTED."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Binance Hit by Record-Breaking $6.3bn Hack",
        source_summary="Cryptocurrency heist totaling $6.3bn occurred.",
        editorial_text="sự việc gây thiệt hại 6,3 tỷ cho nền tảng",
    )
    report = validate_package(res, pkg, st, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED"]
    assert len(num_errors) == 0
    assert report.passed is True


def test_ac2_thousands_separator_equivalence_dots_and_commas() -> None:
    """Source 'Nearly 63,000' vs editorial 'gần 63.000': passes without error."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Nearly 63,000 Attack Attempts Recorded",
        source_summary="Firewalls intercepted nearly 63,000 malicious probes.",
        editorial_text="hệ thống ghi nhận gần 63.000 đợt tấn công nguy hiểm",
    )
    report = validate_package(res, pkg, st, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED"]
    assert len(num_errors) == 0
    assert report.passed is True


def test_ac2_version_tokens_atomic_match_no_error_no_v2_term() -> None:
    """Source 'version 2.1.313' vs editorial 'v2.1.313': no number error, no 'v2' term."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Claude Code Extension Issue",
        source_summary="The malicious update, version 2.1.313, was distributed widely.",
        editorial_text="tiện ích mở rộng phiên bản v2.1.313 đã bị khai thác",
    )
    report = validate_package(res, pkg, st, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED"]
    v2_warnings = [w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "v2"]
    assert len(num_errors) == 0
    assert len(v2_warnings) == 0
    assert report.passed is True


def test_ac2_numbered_list_enumerators_not_extracted_in_validation() -> None:
    """Numbered-list enumerators (1., 2., 3.) are dropped and not flagged as unsupported."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Security Updates Summary",
        source_summary="Major issues were addressed across multiple systems.",
        editorial_text="danh sách tổng hợp:\n1. Điểm tin một\n2. Điểm tin hai\n3. Điểm tin ba",
    )
    report = validate_package(res, pkg, st, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED"]
    assert len(num_errors) == 0
    assert report.passed is True


def test_ac2_term_trojan_supported_by_trojanized_source() -> None:
    """Source word 'Trojanized' supports editorial term 'Trojan' (prefix rule len >= 4)."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Claude Code Extension Trojanized with Backdoor",
        source_summary="Attackers distributed a backdoor inside the package.",
        editorial_text="tiện ích độc hại chứa mã Trojan nguy hiểm",
    )
    report = validate_package(res, pkg, st, llm=None)
    trojan_warnings = [w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "Trojan"]
    assert len(trojan_warnings) == 0


def test_ac2_term_usd_supported_by_dollar_sign() -> None:
    """Editorial 'USD' is supported when source contains '$'."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Binance Suffers $6.3bn Loss",
        source_summary="Massive heist reported on crypto exchange.",
        editorial_text="sự việc gây thiệt hại tài sản USD lớn",
    )
    report = validate_package(res, pkg, st, llm=None)
    usd_warnings = [w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "USD"]
    assert len(usd_warnings) == 0


def test_ac2_term_internet_builtin_allowlist() -> None:
    """Editorial 'Internet' is supported via _BUILTIN_TERMS even if absent from source."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Network Security Advisory",
        source_summary="New firewall rules deployed across data centers.",
        editorial_text="nguy cơ tấn công qua môi trường mạng Internet hiện nay",
    )
    report = validate_package(res, pkg, st, llm=None)
    internet_warnings = [w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED" and w.evidence == "Internet"]
    assert len(internet_warnings) == 0


# ==============================================================================
# Acceptance Criteria 3 & Amendments: Regression Guards
# ==============================================================================


def test_p3_editorial_6_3bn_vs_source_dollar_6_3bn() -> None:
    """P3 test: editorial '6.3bn' vs source '$6.3bn' -> no E_NUMBER, no W_TERM."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Record $6.3bn Cyber Attack",
        source_summary="Hackers targeted infrastructure totaling $6.3bn.",
        editorial_text="tổng thiệt hại được ước tính là 6.3bn quy mô lớn",
    )
    report = validate_package(res, pkg, st, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED"]
    term_warnings = [w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED"]
    assert len(num_errors) == 0
    assert len(term_warnings) == 0


def test_regression_guard_different_number_flagged() -> None:
    """AC3: Different number '6.4' vs source '6.3' is flagged as E_NUMBER_UNSUPPORTED."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Loss of $6.3bn Reported",
        source_summary="Heist of $6.3bn occurred.",
        editorial_text="con số thiệt hại lên tới 6,4 tỷ đồng",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "6.4"
    assert report.passed is False


def test_regression_guard_different_version_flagged() -> None:
    """AC3: Different version '2.1.314' vs source '2.1.313' is flagged."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Advisory for version 2.1.313",
        source_summary="Patches released in version 2.1.313 today.",
        editorial_text="người dùng cần cập nhật lên phiên bản v2.1.314 ngay",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "2.1.314"
    assert report.passed is False


def test_regression_guard_unsupported_month_10_flagged() -> None:
    """AC3: 'tháng 10' is still flagged when source states no month 10."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Critical Patch Released",
        source_summary="Vendors issued hotfixes yesterday.",
        editorial_text="bản cập nhật được tung ra vào tháng 10 gần đây",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "10"
    assert report.passed is False


def test_regression_guard_term_iot_flagged_when_absent() -> None:
    """AC3: 'IoT' is still flagged when absent from source."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Smart Device Security Advisory",
        source_summary="Vulnerabilities in connected hardware devices.",
        editorial_text="các thiết bị IoT trong gia đình bị nhắm tới",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED")
    assert issue.evidence == "IoT"


def test_p7_prefix_rule_short_term_3_chars_never_rescued() -> None:
    """P7: 3-character term 'IoT' is never rescued by prefix match against 'IoTree'."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Analysis of IoTree Malware Family",
        source_summary="Researchers tracked IoTree campaign across servers.",
        editorial_text="mã độc nhắm vào mạng IoT của doanh nghiệp",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED")
    assert issue.evidence == "IoT"


def test_regression_guard_cve_behavior_unchanged() -> None:
    """AC3: E_CVE_UNSUPPORTED behavior is unchanged."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Vulnerability Disclosed",
        source_summary="Patch issued for security flaw.",
        editorial_text="khai thác lỗ hổng CVE-2026-9999 trong hệ thống",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_CVE_UNSUPPORTED")
    assert issue.evidence == "CVE-2026-9999"


def test_regression_guard_number_in_unrelated_token_not_accepted() -> None:
    """AC3: '97229' present in source only as 'CVE-2026-97229' is not accepted as standalone number."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="GitLab Patches CVE-2026-97229",
        source_summary="Remote code execution vulnerability CVE-2026-97229 fixed.",
        editorial_text="số lượng vụ việc được báo cáo là 97229 trường hợp",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "97229"


def test_p6_tightening_63_vs_thousands_63000() -> None:
    """P6 tightening: 63 vs '63,000' -> 63 flagged (thousands leading group no longer accepted)."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Attack Attempts",
        source_summary="Security appliances detected 63,000 attack attempts.",
        editorial_text="khoảng 63 máy chủ bị ảnh hưởng nghiêm trọng",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "63"
    assert report.passed is False


def test_p6_tightening_313_vs_version_2_1_313() -> None:
    """P6 tightening: 313 vs 'version 2.1.313' -> 313 flagged (version subcomponents not accepted)."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Application Update",
        source_summary="Malicious update in version 2.1.313 distributed.",
        editorial_text="tổng cộng có 313 người dùng tải về bản cập nhật",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "313"
    assert report.passed is False


def test_p7_editorial_version_1_313_vs_source_2_1_313() -> None:
    """P7: editorial 'phiên bản 1.313' vs source 'version 2.1.313' -> E_NUMBER flagged."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Software Advisory",
        source_summary="Vulnerability fixed in version 2.1.313 by vendor.",
        editorial_text="người dùng phiên bản 1.313 cần chú ý rà soát",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    # '1.313' is classified as thousands '1313', which does not match source '2.1.313'
    assert issue.evidence == "1313"
    assert report.passed is False


def test_p7_editorial_usd_when_neither_dollar_nor_usd_in_source() -> None:
    """P7: editorial 'USD' when source contains neither '$' nor 'USD' -> W_TERM flagged."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Cryptocurrency Heist Overview",
        source_summary="Attacker stole 6.3bn tokens during the operation.",
        editorial_text="ước tính quy đổi ra USD có giá trị lớn",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(w for w in report.warnings if w.code == "W_TERM_UNSUPPORTED")
    assert issue.evidence == "USD"


def test_p7_decimal_10_1234_does_not_match_101234() -> None:
    """P7: '10.1234' stays a decimal and does not match '101234'."""
    kind, canon = _classify_and_canonicalize("10.1234")
    assert kind == "decimal"
    assert canon == "10.1234"
    assert canon != "101234"

    res, pkg, st = _make_synthetic_bundle(
        source_title="Metric Measurement",
        source_summary="Total transactions reached 101234 events.",
        editorial_text="tỷ lệ đo lường đạt mức 10.1234 chỉ số",
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "10.1234"
    assert report.passed is False


def test_p7_numbers_and_terms_checked_only_against_cited_articles() -> None:
    """P7: Number/term present only in a NON-cited article must still be flagged."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Cited Article",
        source_summary="First article about cybersecurity.",
        editorial_text="báo cáo ghi nhận 999 sự cố trong ngày",
        extra_articles=[
            ("2222333344445555", "Non-Cited Article with 999", "Contains number 999 here.")
        ],
        cited_ids=["1111222233334444"],  # Only cites article 1, NOT article 2
    )
    report = validate_package(res, pkg, st, llm=None)
    issue = next(e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED")
    assert issue.evidence == "999"
    assert report.passed is False


# ==============================================================================
# Task T4.2e: Tech Compound Masking, Citation & Builtin Terms Whitelisting Tests
# ==============================================================================


def test_t42e_extract_numbers_masks_tech_compounds() -> None:
    """Verify tech compounds (5G, 2FA, 10Gbps, v2, SHA256, IPv6) yield zero numbers."""
    phrase = "Chuan 5G va 2FA, bang thong 10Gbps, v2 moi, ma SHA256 va IPv6"
    extracted = _extract_numbers(phrase)
    assert extracted == []


def test_t42e_extract_numbers_various_tech_units_and_compounds() -> None:
    """Verify various bandwidth, frequency, storage, bit, cellular, auth, and version patterns."""
    samples = [
        "mang di dong 5G va 4G",
        "bao mat voi 2FA va 3fa",
        "bang thong 10Gbps, 100kbps, 1.5GHz, 2.4GHz",
        "dung luong 16GB, 512MB, 1TiB",
        "ma hoa 256-bit, 64bit, 128 bits, 1024 bytes",
        "cap nhat len v2 moi",
        "phien ban V3 da ra mat",
    ]
    for sample in samples:
        assert _extract_numbers(sample) == []


def test_t42e_real_numbers_preserved_alongside_tech_compounds() -> None:
    """Standalone numbers are still extracted when mixed with masked tech tokens."""
    text = "He thong 5G da ghi nhan 42 su co bao mat qua 2FA voi bang thong 10Gbps trong v2"
    assert _extract_numbers(text) == ["42"]


def test_t42e_subversion_tokens_not_masked_by_standalone_version() -> None:
    """Sub-versions like v2.1.313 or v2.0 are not masked by _STANDALONE_VERSION_PATTERN."""
    assert _extract_numbers("phien ban v2.1.313") == ["2.1.313"]
    assert _extract_numbers("phien ban v2.0") == ["2.0"]


def test_t42e_validation_package_zero_unsupported_numbers_for_tech_compounds() -> None:
    """End-to-end validate_package yields zero E_NUMBER_UNSUPPORTED for tech compound phrase."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="An ninh mang va cong nghe moi",
        source_summary="Bao cao danh gia ve xu huong tan cong mang tren the gioi.",
        editorial_text="Chuan 5G va 2FA, bang thong 10Gbps, v2 moi, ma SHA256 va IPv6",
    )
    report = validate_package(res, pkg, st, llm=None)
    num_errors = [e for e in report.errors if e.code == "E_NUMBER_UNSUPPORTED"]
    assert len(num_errors) == 0


def test_t42e_citation_tags_exemption_in_validation() -> None:
    """Citation tags [A1], A2, [A3], A4 do not trigger W_TERM_UNSUPPORTED."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Security Advisory Report",
        source_summary="Details on vulnerabilities and mitigations.",
        editorial_text="Theo thong tin tu bai viet [A1] va A2, cung nhu nguon [A3] va A4",
    )
    report = validate_package(res, pkg, st, llm=None)
    citation_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence.upper() in {"A1", "A2", "A3", "A4"}
    ]
    assert len(citation_warnings) == 0


def test_t42e_builtin_terms_expanded_allowlist() -> None:
    """CNTT, ATTT, CSDL, VND in editorial do not trigger W_TERM_UNSUPPORTED."""
    res, pkg, st = _make_synthetic_bundle(
        source_title="Bao mat he thong",
        source_summary="Cap nhat cac bien phap an ninh mang toan dien.",
        editorial_text="Nganh CNTT va linh vuc ATTT can bao ve CSDL voi chi phi hang ty VND",
    )
    report = validate_package(res, pkg, st, llm=None)
    builtin_warnings = [
        w for w in report.warnings
        if w.code == "W_TERM_UNSUPPORTED" and w.evidence.lower() in {"cntt", "attt", "csdl", "vnd"}
    ]
    assert len(builtin_warnings) == 0
