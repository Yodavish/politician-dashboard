"""Tests for the Pydantic review models and the Ollama JSON schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tools.ai_review.models import Finding, Review, REVIEW_JSON_SCHEMA


def _finding(**overrides) -> dict:
    defaults = {
        "file": "src/app.py",
        "line": 12,
        "severity": "high",
        "category": "security",
        "title": "SQL injection",
        "body": "The query interpolates user input.",
    }
    defaults.update(overrides)
    return defaults


class TestFinding:
    def test_valid_finding(self) -> None:
        finding = Finding.model_validate(_finding())
        assert finding.line == 12
        assert finding.severity == "high"

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Finding.model_validate(_finding(severity="critical"))

    def test_missing_required_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Finding.model_validate({key: value for key, value in _finding().items() if key != "title"})


class TestReview:
    def test_round_trip(self) -> None:
        data = {
            "summary": "Solid change.",
            "findings": [_finding(), _finding(line=13, severity="low", category="maintainability")],
        }
        review = Review.model_validate(data)
        assert len(review.findings) == 2
        assert review.findings[1].category == "maintainability"

    def test_findings_required(self) -> None:
        with pytest.raises(ValidationError):
            Review.model_validate({"summary": "no findings key"})


class TestReviewJsonSchema:
    def test_schema_structure(self) -> None:
        properties = REVIEW_JSON_SCHEMA["properties"]
        assert REVIEW_JSON_SCHEMA["type"] == "object"
        assert set(REVIEW_JSON_SCHEMA["required"]) == {"summary", "findings"}
        assert properties["findings"]["items"]["type"] == "object"
        assert set(properties["findings"]["items"]["required"]) == {
            "file",
            "line",
            "severity",
            "category",
            "title",
            "body",
        }

    def test_schema_is_self_contained(self) -> None:
        assert "$defs" not in REVIEW_JSON_SCHEMA
        assert "$ref" not in str(REVIEW_JSON_SCHEMA)