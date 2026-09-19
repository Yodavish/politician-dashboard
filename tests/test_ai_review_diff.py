"""Tests for unified-diff parsing and line anchoring."""

from __future__ import annotations

from tools.ai_review.diff import build_anchors, parse_patch


class TestParsePatch:
    def test_mixed_hunk_tracks_right_and_left_lines(self) -> None:
        patch = "\n".join(
            [
                "@@ -1,5 +1,6 @@",
                " def greet(name):",
                "-    return 'hello ' + name",
                "+    person = sanitize(name)",
                "+    return f'hello {person}'",
                " ",
                "+print(greet('bob'))",
            ]
        )
        anchors = parse_patch(patch)
        assert anchors.right_lines == {1, 2, 3, 4, 5}
        assert anchors.left_lines == {1, 2, 3}

    def test_removed_lines_are_not_right_anchors(self) -> None:
        patch = "\n".join(
            [
                "@@ -10,2 +10,1 @@",
                "-old_free_code(1)",
                "-old_free_code(2)",
                "+new_single_call()",
            ]
        )
        anchors = parse_patch(patch)
        assert anchors.anchors_line(10)

    def test_all_added_file(self) -> None:
        patch = "\n".join(["@@ -0,0 +1,4 @@", "+line1", "+line2", "+line3", "+line4"])
        anchors = parse_patch(patch)
        assert anchors.right_lines == {1, 2, 3, 4}
        assert anchors.left_lines == set()

    def test_hunk_header_without_counts(self) -> None:
        patch = "\n".join(["@@ -5 +5 @@", "+replacement"])
        anchors = parse_patch(patch)
        assert anchors.anchors_line(5)

    def test_api_style_headers_are_ignored(self) -> None:
        patch = "\n".join(
            [
                "diff --git a/src/app.py b/src/app.py",
                "index abc123..def456 100644",
                "--- a/src/app.py",
                "+++ b/src/app.py",
                "@@ -1,2 +1,2 @@",
                "-a",
                "+b",
            ]
        )
        anchors = parse_patch(patch)
        assert anchors.right_lines == {1}

    def test_garbage_lines_do_not_advance_position(self) -> None:
        patch = "\n".join(
            [
                "@@ -1,1 +1,1 @@",
                "+first",
                "this is not diff content",
                "+second",
            ]
        )
        anchors = parse_patch(patch)
        assert anchors.right_lines == {1, 2}

    def test_empty_patch_produces_no_anchors(self) -> None:
        anchors = parse_patch("")
        assert anchors.right_lines == set()
        assert anchors.left_lines == set()
        assert not anchors.anchors_line(1)


class TestBuildAnchors:
    def test_skips_files_without_patch(self) -> None:
        files = [
            {"filename": "src/a.py", "patch": "@@ -1 +1 @@\n-x\n+y\n"},
            {"filename": "src/b.pdf", "patch": None},
            {"filename": "src/c.py", "patch": ""},
        ]
        anchors = build_anchors(files)
        assert set(anchors) == {"src/a.py"}
        assert anchors["src/a.py"].anchors_line(1)