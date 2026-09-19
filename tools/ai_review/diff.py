"""Unified-diff parsing to compute commentable lines in the new file."""

from __future__ import annotations

import re

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Headers that can appear before hunks in a patch and are not line content.
_IGNORED_PREFIXES = ("diff --git ", "index ", "--- ", "+++ ", "\\ No newline")


class PatchAnchors:
    """Changed-file line numbers inferred from a unified diff."""

    def __init__(self, *, right_lines: set[int], left_lines: set[int]) -> None:
        self.right_lines = right_lines
        self.left_lines = left_lines

    def anchors_line(self, line: int) -> bool:
        """Whether ``line`` is a commentable line in the new file.

        GitHub only permits inline comments on lines that appear inside a hunk
        of the new-file diff, so context and added lines qualify while removed
        lines do not.
        """
        return line in self.right_lines


def parse_patch(patch: str) -> PatchAnchors:
    """Parse a unified diff and return the new-file lines visible in hunks."""
    right_lines: set[int] = set()
    left_lines: set[int] = set()
    right_idx: int | None = None
    left_idx: int | None = None

    for raw in patch.splitlines():
        if raw.startswith("@@"):
            match = _HUNK_RE.match(raw)
            if match is None:
                continue
            left_idx = int(match.group(1))
            right_idx = int(match.group(2))
            continue
        if raw.startswith(_IGNORED_PREFIXES):
            continue
        if right_idx is None or left_idx is None:
            continue

        prefix = raw[:1]
        if prefix == "+":
            right_lines.add(right_idx)
            right_idx += 1
        elif prefix == "-":
            left_lines.add(left_idx)
            left_idx += 1
        elif prefix == " ":
            right_lines.add(right_idx)
            left_lines.add(left_idx)
            right_idx += 1
            left_idx += 1
        # Any other line (blank, malformed content) is ignored and intentionally
        # does not advance the position counters.

    return PatchAnchors(right_lines=right_lines, left_lines=left_lines)


def build_anchors(changed_files: list[dict]) -> dict[str, PatchAnchors]:
    """Build ``{filename: PatchAnchors}`` from a list of changed-file records."""
    anchors: dict[str, PatchAnchors] = {}
    for info in changed_files:
        patch = info.get("patch")
        if patch:
            anchors[info["filename"]] = parse_patch(patch)
    return anchors