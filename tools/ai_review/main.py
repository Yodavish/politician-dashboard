"""CLI orchestration for the AI PR code reviewer.

Usage:
    python -m tools.ai_review --repo owner/name --pr 123 --token <token>
    python -m tools.ai_review   # all inputs from GitHub Actions environment

The runner's environment variables (``GITHUB_REPOSITORY``, ``GITHUB_REF``,
``GITHUB_TOKEN``, ``GITHUB_STEP_SUMMARY``) are used when run inside GitHub
Actions. The tool reads PR metadata and file diffs from the GitHub REST API
and only requests a local Ollama model; it never checks out or executes code
from the pull request.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from tools.ai_review.diff import build_anchors
from tools.ai_review.github_client import GitHubApiError, GitHubClient
from tools.ai_review.models import REVIEW_MARKER, REVIEW_JSON_SCHEMA
from tools.ai_review.ollama import OllamaClient, OllamaError
from tools.ai_review.post import (
    anchor_findings,
    build_inline_comments,
    build_review_body,
    can_post_pr_review,
    write_step_summary,
)
from tools.ai_review.reviewer import (
    MAX_FINDINGS,
    MAX_SOURCE_FILES,
    SOURCE_FETCH_CHANGE_LIMIT,
    is_source_like,
    run_review,
    should_skip,
)

_PR_REF_RE = re.compile(r"refs/pull/(\d+)(?:/.*)?")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ai_review",
        description="Advisory AI code review of a pull request.",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="Repository as owner/name (default: GITHUB_REPOSITORY).",
    )
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="Pull request number (default: from GITHUB_REF).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub token (default: GITHUB_TOKEN).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AI_REVIEW_MODEL", "qwen2.5-coder:7b"),
        help="Ollama model to use (default: qwen2.5-coder:7b).",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        help="Base URL of the Ollama server (default: local Ollama port).",
    )
    parser.add_argument(
        "--summary-file",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="Append the advisory markdown here (default: GITHUB_STEP_SUMMARY).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the review to the summary instead of posting it.",
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        default=MAX_FINDINGS,
        help=f"Maximum number of findings to report (default {MAX_FINDINGS}).",
    )
    return parser


def pr_number_from_ref(ref: str | None) -> int | None:
    match = _PR_REF_RE.fullmatch(ref or "")
    return int(match.group(1)) if match else None


def _load_sources(
    github: GitHubClient,
    pull: dict,
    changed_files: list[dict],
) -> dict[str, str]:
    """Fetch full content for reviewable source files at the PR head.

    The head repository is read from the pull request payload so files on fork
    pull requests are fetched from the fork itself.
    """
    head = pull.get("head") or {}
    repo_full_name = (head.get("repo") or {}).get("full_name")
    ref = head.get("sha")
    if not repo_full_name or not ref:
        return {}

    sources: dict[str, str] = {}
    for info in changed_files:
        filename = info["filename"]
        if should_skip(filename) or not is_source_like(filename):
            continue
        if (info.get("additions") or 0) + (info.get("deletions") or 0) > SOURCE_FETCH_CHANGE_LIMIT:
            continue
        content = github.get_file_contents(repo_full_name, filename, ref)
        if content is not None:
            sources[filename] = content
        if len(sources) >= MAX_SOURCE_FILES:
            break
    return sources


def _was_already_reviewed(
    github: GitHubClient,
    owner: str,
    repo: str,
    number: int,
    head_sha: str,
) -> bool:
    for review in github.list_reviews(owner, repo, number):
        if REVIEW_MARKER in (review.get("body") or "") and review.get("commit_id") == head_sha:
            return True
    return False


def main(
    argv: list[str] | None = None,
    *,
    _github: GitHubClient | None = None,
    _ollama: OllamaClient | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    if not args.repo or "/" not in args.repo:
        print("error: --repo must be provided as owner/name", file=sys.stderr)
        return 2
    if not args.token:
        print("error: --token or GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    pr_number = args.pr or pr_number_from_ref(os.environ.get("GITHUB_REF"))
    if not pr_number:
        print("error: --pr or GITHUB_REF (refs/pull/N/merge) is required", file=sys.stderr)
        return 2

    owner, repo = args.repo.split("/", 1)

    github = _github or GitHubClient(token=args.token)
    ollama = _ollama or OllamaClient(base_url=args.ollama_url)
    try:
        pull = github.get_pull(owner, repo, pr_number)
        head_sha = pull["head"]["sha"]

        if _was_already_reviewed(github, owner, repo, pr_number, head_sha):
            message = f"AI review already posted for {head_sha[:12]}; skipping."
            print(f"PR #{pr_number}: {message}")
            write_step_summary(args.summary_file, f"{REVIEW_MARKER}: {message}")
            return 0

        changed_files = github.list_changed_files(owner, repo, pr_number)
        anchors = build_anchors(changed_files)
        sources = _load_sources(github, pull, changed_files)

        def complete(model: str, messages: list[dict], options: dict) -> str:
            return ollama.chat(
                model=model,
                messages=messages,
                format_schema=REVIEW_JSON_SCHEMA,
                options=options,
            )

        result = run_review(
            pull=pull,
            changed_files=changed_files,
            anchors=anchors,
            sources=sources,
            complete=complete,
            model=args.model,
            max_findings=args.max_findings,
        )

        inline, unanchored = anchor_findings(result.review.findings, anchors)
        body = build_review_body(
            summary=result.review.summary,
            unanchored=unanchored,
            notes=result.warnings,
            model=args.model,
            reviewed_count=len(result.reviewed_names),
            skipped_names=result.skipped_names,
        )
        comments = build_inline_comments(inline)

        if args.dry_run:
            print(f"PR #{pr_number}: dry run - not posting {len(comments)} comment(s).")
            write_step_summary(args.summary_file, body)
            return 0

        if not can_post_pr_review(pull):
            print(
                f"PR #{pr_number}: read-only context (fork/Dependabot) - "
                "writing advisory review to the step summary instead."
            )
            write_step_summary(args.summary_file, body)
            return 0

        try:
            github.create_review(
                owner,
                repo,
                pr_number,
                commit_id=head_sha,
                body=body,
                comments=comments,
            )
            print(f"PR #{pr_number}: posted advisory review with {len(comments)} comment(s).")
        except GitHubApiError as exc:
            if exc.status == 403:
                print(
                    f"PR #{pr_number}: token cannot post (fork/Dependabot?) - "
                    "writing advisory review to the step summary instead."
                )
                write_step_summary(args.summary_file, body)
                return 0
            raise
        return 0
    except (GitHubApiError, OllamaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if _github is None:
            github.close()
        if _ollama is None:
            ollama.close()