"""End-to-end tests of the CLI pipeline with mocked GitHub and Ollama HTTP."""

from __future__ import annotations

import json

import httpx

from tools.ai_review.github_client import GitHubClient
from tools.ai_review.main import main, pr_number_from_ref
from tools.ai_review.models import REVIEW_MARKER
from tools.ai_review.ollama import OllamaClient

HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"

PATCH = "\n".join(
    [
        "@@ -1,5 +1,6 @@",
        " def greet(name):",
        "-    return 'hello ' + name",
        "+    person = sanitize(name)",
        "+    return f'hello {person}'",
    ]
)

REVIEW_JSON = {
    "summary": "Good change, one security concern.",
    "findings": [
        {
            "file": "src/app.py",
            "line": 2,
            "severity": "high",
            "category": "security",
            "title": "Untrusted input",
            "body": "sanitize() must validate the argument.",
        }
    ],
}

ARGS = [
    "--repo",
    "o/r",
    "--pr",
    "5",
    "--token",
    "t",
    "--ollama-url",
    "http://ollama:11434",
]


def _pull(*, fork: bool = False, user: str = "bob") -> dict:
    return {
        "number": 5,
        "title": "Greet safely",
        "body": "Update greeting.",
        "user": {"login": user},
        "head": {"sha": HEAD_SHA, "repo": {"full_name": "o/r", "fork": fork}},
    }


def _file_entry() -> dict:
    return {
        "filename": "src/app.py",
        "status": "modified",
        "additions": 2,
        "deletions": 1,
        "changes": 3,
        "patch": PATCH,
    }


def github_transport(pull, *, reviews=None, posted=None):
    posted = posted if posted is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/repos/o/r/pulls/5":
            return httpx.Response(200, json=pull)
        if request.method == "GET" and path == "/repos/o/r/pulls/5/reviews":
            return httpx.Response(200, json=reviews or [])
        if request.method == "GET" and path == "/repos/o/r/pulls/5/files":
            if int(request.url.params.get("page", 1)) > 1:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[_file_entry()])
        if request.method == "GET" and path == "/repos/o/r/contents/src/app.py":
            return httpx.Response(
                200,
                content=b"def greet(name):\n    person = sanitize(name)\n    return f'hello {person}'\n",
            )
        if request.method == "POST" and path == "/repos/o/r/pulls/5/reviews":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": 77})
        return httpx.Response(404, text=f"unexpected {request.method} {path}")

    return handler, posted


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_clients(pull, *, reviews=None, model_content):
    github_transport_fn, posted = github_transport(pull, reviews=reviews)
    github = GitHubClient(
        token="t", http=_mock_client(github_transport_fn), base_url="https://api.github.com"
    )

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(
                200, json={"message": {"content": model_content}}
            )
        return httpx.Response(404, text="unexpected ollama route")

    ollama = OllamaClient(
        base_url="http://ollama:11434",
        http=_mock_client(ollama_handler),
    )
    return github, ollama, posted


class TestPipeline:
    def _run(self, github, ollama, *, summary_file=None, dry_run=False):
        args = list(ARGS)
        if summary_file is not None:
            args += ["--summary-file", str(summary_file)]
        if dry_run:
            args += ["--dry-run"]
        return main(args, _github=github, _ollama=ollama)

    def test_posts_review_on_same_repo_pr(self, tmp_path) -> None:
        target = tmp_path / "summary.md"
        github, ollama, posted = _make_clients(
            _pull(), model_content=json.dumps(REVIEW_JSON)
        )
        result = self._run(github, ollama, summary_file=target)
        assert result == 0
        assert not target.exists()
        assert len(posted) == 1
        payload = posted[0]
        assert payload["event"] == "COMMENT"
        assert payload["commit_id"] == HEAD_SHA
        assert REVIEW_MARKER in payload["body"]
        assert payload["comments"] == [
            {
                "path": "src/app.py",
                "line": 2,
                "side": "RIGHT",
                "body": "**[high] security** - Untrusted input\n\n"
                "sanitize() must validate the argument.",
            }
        ]

    def test_model_was_called_with_schema_and_seed(self, tmp_path) -> None:
        class RecordingTransport(httpx.MockTransport):
            def __init__(self, handler):
                super().__init__(handler)
                self.requests = []

            def handle_request(self, request):
                self.requests.append(request)
                return super().handle_request(request)

        github_transport_fn, _ = github_transport(_pull())
        github = GitHubClient(
            token="t",
            http=httpx.Client(transport=RecordingTransport(github_transport_fn)),
            base_url="https://api.github.com",
        )

        seen_payload = {}

        def ollama_handler(request: httpx.Request) -> httpx.Response:
            seen_payload["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"message": {"content": json.dumps(REVIEW_JSON)}}
            )

        ollama_transport = RecordingTransport(ollama_handler)
        ollama = OllamaClient(
            base_url="http://ollama:11434",
            http=httpx.Client(transport=ollama_transport),
        )
        result = self._run(github, ollama, summary_file=tmp_path / "s.md")
        assert result == 0

        (request,) = ollama_transport.requests
        assert request.url.path == "/api/chat"
        body = seen_payload["body"]
        assert body["model"] == "qwen2.5-coder:7b"
        assert body["stream"] is False
        assert body["format"]["type"] == "object"
        assert body["options"]["temperature"] == 0
        assert body["options"]["seed"] == 1

    def test_fork_pr_writes_step_summary_and_does_not_post(self, tmp_path) -> None:
        target = tmp_path / "summary.md"
        github, ollama, posted = _make_clients(
            _pull(fork=True), model_content=json.dumps(REVIEW_JSON)
        )
        result = self._run(github, ollama, summary_file=target)
        assert result == 0
        assert posted == []
        assert target.exists()
        assert REVIEW_MARKER in target.read_text(encoding="utf-8")

    def test_dependabot_pr_writes_step_summary(self, tmp_path) -> None:
        target = tmp_path / "summary.md"
        github, ollama, posted = _make_clients(
            _pull(user="dependabot[bot]"), model_content=json.dumps(REVIEW_JSON)
        )
        result = self._run(github, ollama, summary_file=target)
        assert result == 0
        assert posted == []
        assert REVIEW_MARKER in target.read_text(encoding="utf-8")

    def test_already_reviewed_head_sha_skips_model(self, tmp_path) -> None:
        target = tmp_path / "summary.md"
        reviews = [{"commit_id": HEAD_SHA, "body": f"**{REVIEW_MARKER}** - done"}]
        github, ollama, posted = _make_clients(
            _pull(), reviews=reviews, model_content="must not be consumed"
        )
        result = self._run(github, ollama, summary_file=target)
        assert result == 0
        assert posted == []
        assert "skipping" in target.read_text(encoding="utf-8").lower()

    def test_malformed_model_output_posts_fallback(self, tmp_path) -> None:
        target = tmp_path / "summary.md"
        github, ollama, posted = _make_clients(_pull(), model_content="{not json")
        result = self._run(github, ollama, summary_file=target)
        assert result == 0
        assert len(posted) == 1
        assert "could not be interpreted" in posted[0]["body"]

    def test_dry_run_writes_summary_only(self, tmp_path) -> None:
        target = tmp_path / "summary.md"
        github, ollama, posted = _make_clients(
            _pull(), model_content=json.dumps(REVIEW_JSON)
        )
        result = self._run(github, ollama, summary_file=target, dry_run=True)
        assert result == 0
        assert posted == []
        assert target.exists()
        assert REVIEW_MARKER in target.read_text(encoding="utf-8")

    def test_missing_args_return_usage_error(self, tmp_path) -> None:
        assert main([], _github=None, _ollama=None) == 2


class TestPrNumberFromRef:
    def test_merge_ref(self) -> None:
        assert pr_number_from_ref("refs/pull/123/merge") == 123

    def test_head_ref(self) -> None:
        assert pr_number_from_ref("refs/pull/7/head") == 7

    def test_no_match(self) -> None:
        assert pr_number_from_ref("refs/heads/main") is None
        assert pr_number_from_ref(None) is None