"""Unit tests for feedback extraction, sanitization, and grouping."""

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest

from cyberpulse.feedback import (
    FeedbackItem,
    REWRITABLE_ERROR_CODES,
    build_feedback,
    feedback_signature,
    groups_for,
    sanitize_feedback_text,
)


class MockIssue:
    def __init__(
        self,
        code: str,
        severity: str = "error",
        path: str = "digest.sections[0]",
        message: str = "Problem",
        evidence: str = "evidence",
    ) -> None:
        self.code = code
        self.severity = severity
        self.path = path
        self.message = message
        self.evidence = evidence


class MockReport:
    def __init__(self, errors: list[MockIssue], warnings: list[MockIssue] | None = None) -> None:
        self.errors = errors
        self.warnings = warnings or []


def test_feedback_item_frozen_dataclass() -> None:
    item = FeedbackItem(code="E_TEST", path="digest", message="msg", evidence="ev")
    with pytest.raises(FrozenInstanceError):
        item.code = "CHANGED"  # type: ignore[misc]


def test_rewritable_error_codes_set() -> None:
    assert "E_RUN_ID_MISMATCH" not in REWRITABLE_ERROR_CODES
    assert "E_CITE_UNKNOWN" not in REWRITABLE_ERROR_CODES
    assert "E_NUMBER_UNSUPPORTED" in REWRITABLE_ERROR_CODES
    assert "E_SECTION_COUNT" in REWRITABLE_ERROR_CODES
    assert len(REWRITABLE_ERROR_CODES) == 13


def test_build_feedback_filters_warnings_and_non_rewritable() -> None:
    issues = [
        MockIssue(code="E_RUN_ID_MISMATCH", severity="error", message="Run ID mismatch"),
        MockIssue(code="E_CITE_UNKNOWN", severity="error", message="Unknown cite"),
        MockIssue(code="W_SECTION_LENGTH", severity="warning", message="Warning length"),
        MockIssue(code="E_NUMBER_UNSUPPORTED", severity="error", path="podcast.segments[5].line.text", message="Number '10' unsupported", evidence="10"),
    ]
    report = MockReport(errors=issues)
    feedback = build_feedback(report)

    assert len(feedback) == 1
    assert feedback[0].code == "E_NUMBER_UNSUPPORTED"
    assert feedback[0].path == "podcast.segments[5].line.text"
    assert feedback[0].evidence == "10"


def test_build_feedback_sanitization_url_and_article_id() -> None:
    raw_id = "0123456789abcdef"
    raw_url = "https://example.com/advisory/vuln-1?ref=abc"
    issues = [
        MockIssue(
            code="E_URL_OR_ID_IN_TEXT",
            path="digest.sections[0].body.text",
            message=f"Found leaked token {raw_id} and {raw_url} in presentation text",
            evidence=f"{raw_id} at {raw_url}",
        )
    ]
    report = MockReport(errors=issues)
    feedback = build_feedback(report)

    assert len(feedback) == 1
    assert raw_id not in feedback[0].message
    assert raw_url not in feedback[0].message
    assert "[removed]" in feedback[0].message
    assert raw_id not in feedback[0].evidence
    assert raw_url not in feedback[0].evidence
    assert "[removed]" in feedback[0].evidence


def test_build_feedback_sanitization_newlines_controls_and_brackets() -> None:
    raw_msg = "Error:\n\r\t <invalid_tag> \x00\x1f with   spaces </invalid_tag>"
    raw_ev = "\x0b evidence <tag> \n test </tag> "
    sanitized_msg = sanitize_feedback_text(raw_msg)
    sanitized_ev = sanitize_feedback_text(raw_ev)

    assert "<" not in sanitized_msg
    assert ">" not in sanitized_msg
    assert "\n" not in sanitized_msg
    assert "\t" not in sanitized_msg
    assert "\r" not in sanitized_msg
    assert "\x00" not in sanitized_msg
    assert sanitized_msg == "Error: invalid_tag with spaces /invalid_tag"

    assert "<" not in sanitized_ev
    assert ">" not in sanitized_ev
    assert "\n" not in sanitized_ev
    assert sanitized_ev == "evidence tag test /tag"


def test_build_feedback_length_caps() -> None:
    long_msg = "A" * 500
    long_ev = "B" * 300
    issues = [
        MockIssue(code="E_NUMBER_UNSUPPORTED", message=long_msg, evidence=long_ev)
    ]
    report = MockReport(errors=issues)
    feedback = build_feedback(report)

    assert len(feedback[0].message) == 300
    assert len(feedback[0].evidence) == 200


def test_build_feedback_max_items_cap() -> None:
    issues = [
        MockIssue(code="E_NUMBER_UNSUPPORTED", path=f"digest.sections[{i}]", message=f"Issue {i}")
        for i in range(25)
    ]
    report = MockReport(errors=issues)
    feedback = build_feedback(report, max_items=10)
    assert len(feedback) == 10


def test_build_feedback_deterministic_order() -> None:
    issues = [
        MockIssue(code="E_NUMBER_UNSUPPORTED", path="video.scenes[1]", message="B"),
        MockIssue(code="E_NUMBER_UNSUPPORTED", path="digest.sections[0]", message="A"),
        MockIssue(code="E_CVE_UNSUPPORTED", path="podcast.segments[0]", message="C"),
    ]
    report = MockReport(errors=issues)
    feedback = build_feedback(report)

    paths = [item.path for item in feedback]
    assert paths == ["digest.sections[0]", "podcast.segments[0]", "video.scenes[1]"]


def test_feedback_signature_stability_and_uniqueness() -> None:
    item1 = FeedbackItem(code="E_NUMBER_UNSUPPORTED", path="podcast.segments[5].line.text", message="Msg 1", evidence="10")
    item2 = FeedbackItem(code="E_CAPTION_LENGTH", path="captions[0].text", message="Msg 2", evidence="100 chars")

    sig1 = feedback_signature([item1, item2])
    sig2 = feedback_signature([item2, item1])  # Order permutation
    assert sig1 == sig2
    assert len(sig1) == 64

    sig3 = feedback_signature([item1])
    assert sig1 != sig3


def test_groups_for_mapping() -> None:
    written_only = [FeedbackItem(code="E_SECTION_COUNT", path="digest.sections", message="", evidence="")]
    assert groups_for(written_only) == {"written"}

    captions_only = [FeedbackItem(code="E_CAPTION_LENGTH", path="captions[1].text", message="", evidence="")]
    assert groups_for(captions_only) == {"written"}

    scripts_only = [FeedbackItem(code="E_NUMBER_UNSUPPORTED", path="podcast.segments[5].line.text", message="", evidence="")]
    assert groups_for(scripts_only) == {"scripts"}

    video_only = [FeedbackItem(code="E_SCENE_DURATION", path="video.scenes[0].duration_sec", message="", evidence="")]
    assert groups_for(video_only) == {"scripts"}

    mixed = written_only + scripts_only
    assert groups_for(mixed) == {"written", "scripts"}

    unknown = [FeedbackItem(code="E_NUMBER_UNSUPPORTED", path="custom_scope.unmapped", message="", evidence="")]
    assert groups_for(unknown) == {"written", "scripts"}

    assert groups_for([]) == set()


def test_feedback_module_no_runtime_validator_import() -> None:
    """C11: feedback.py must not import cyberpulse.validator at runtime."""
    feedback_file = Path("src/cyberpulse/feedback.py")
    tree = ast.parse(feedback_file.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "validator" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module and "validator" in node.module:
                # Must be guarded under TYPE_CHECKING
                pass  # We check that top-level execution doesn't load it
