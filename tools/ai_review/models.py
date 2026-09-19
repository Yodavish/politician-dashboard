"""Pydantic models and JSON schema for the AI review output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Marker used to detect reviews that were already posted for a head commit.
REVIEW_MARKER = "AI Code Review (advisory)"


class Finding(BaseModel):
    """A single code review finding produced by the model."""

    file: str
    line: int
    severity: Literal["low", "medium", "high"]
    category: str
    title: str
    body: str


class Review(BaseModel):
    """Structured review output produced by the model."""

    summary: str
    findings: list[Finding]


# Flat JSON Schema handed to Ollama's structured-output (`format`) mode.
# `$defs` references are deliberately avoided because Ollama's format support
# expects a plain, self-contained schema.
_FINDING_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {"type": "string"},
        "line": {"type": "integer"},
        "severity": {"enum": ["low", "medium", "high"]},
        "category": {"type": "string"},
        "title": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["file", "line", "severity", "category", "title", "body"],
    "additionalProperties": False,
}

REVIEW_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": _FINDING_ITEM_SCHEMA},
    },
    "required": ["summary", "findings"],
    "additionalProperties": False,
}