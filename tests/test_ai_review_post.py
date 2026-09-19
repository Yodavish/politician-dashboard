"""Tests for converting review results into comments and summaries."""

from __future__ import annotations

from tools.ai_review.diff import parse_patch
from tools.ai_review.models import Finding, Review, REVIEW_MARKER
from tools.ai_review.post import (
    anchor_findings,
    build_inline_comments,
    build_review_body,
    can_post_pr_review,
    render_finding_body,
    write_step_summary,
)


def _finding(file="src/app.py", line=2, **overrides) -> Finding:
    values = {
        "file": file,
        "line": line,
        "severity": "high",
        "category": "security",
        "title": "Unsanitized input",
        "body": "Validate before use.",
    }
    values.update(overrides)
    return Finding.model_validate(values)


class TestAnchorFindings:
    def test_anchored_and_unanchored_split(self) -> None:
        patch = "@@ -1,2 +1,3 @@\n def a():\n+    b = sanitize(x)\n+    return b"
        anchors = {"src/app.py": parse_patch(patch)}
        on_line = _finding(line=2)
        off_line = _finding(line=99)
        off_file = _finding(file="src/other.py", line=2)

        inline, unanchored = anchor_findings([on_line, off_line, off_file], anchors)
        assert inline == [on_line]
        assert unanchored == [off_line, off_file]

    def test_missing_file_anchor_is_unanchored(self) -> None:
        inline, unanchored = anchor_findings([_finding()], {})
        assert inline == []
        assert len(unanchored) == 1


class TestInlineComments:
    def test_builds_comment_payloads(self) -> None:
        comments = build_inline_comments([_finding()])
        assert comments == [
            {
                "path": "src/app.py",
                "line": 2,
                "side": "RIGHT",
                "body": "**[high] security** - Unsanitized input\n\nValidate before use.",
            }
        ]

    def test_body_capped(self) -> None:
        finding = _finding(body="x" * 70_000)
        body = render_finding_body(finding)
        assert len(body) <= 65_000
        assert body.endswith("\u2026")


class TestBuildReviewBody:
    def test_includes_marker_and_summary_and_notes(self) -> None:
        review = Review(summary="LGTM.", findings=[])
        body = build_review_body(
            summary=review.summary,
            unanchored=[],
            notes=["dropped malformed finding"],
            model="qwen2.5-coder:7b",
            reviewed_count=3,
            skipped_names=["uv.lock"],
        )
        assert REVIEW_MARKER in body
        assert "LGTM." in body
        assert "dropped malformed finding" in body
        assert "not a substitute for human review" in body
        assert "Reviewed 3 file(s)" in body
        assert "uv.lock" in body

    def test_lists_unanchored_findings(self) -> None:
        body = build_review_body(
            summary="ok",
            unanchored=[_finding(line=88)],
            notes=[],
            model="m",
            reviewed_count=1,
            skipped_names=[],
        )
        assert "around line 88" in body
        assert "Unsanitized input" in body


class TestCanPost:
    def test_normal_pr(self) -> None:
        assert can_post_pr_review(
            {"head": {"repo": {"fork": False}}, "user": {"login": "bob"}}
        )

    def test_fork_pr(self) -> None:
        assert not can_post_pr_review(
            {"head": {"repo": {"fork": True}}, "user": {"login": "bob"}}
        )

    def test_dependabot_pr(self) -> None:
        assert not can_post_pr_review(
            {"head": {"repo": {"fork": False}}, "user": {"login": "dependabot[bot]"}}
        )

    def test_missing_fields(self) -> None:
        assert can_post_pr_review({})


class TestWriteStepSummary:
    def test_appends_to_file(self, tmp_path) -> None:
        target = tmp_path / "summary.md"
        write_step_summary(str(target), "hello")
        write_step_summary(str(target), "world")
        assert target.read_text(encoding="utf-8") == "hello\nworld\n"

    def test_prints_when_no_path(self, capsys) -> None:
        write_step_summary(None, "line")
        captured = capsys.readouterr()
        assert "line" in captured.out