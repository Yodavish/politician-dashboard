"""Tests for the GitHub REST client with a mocked HTTP transport."""

from __future__ import annotations

import httpx
import pytest

from tools.ai_review.github_client import GitHubApiError, GitHubClient


def _client(handler) -> GitHubClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return GitHubClient(token="secret-token", http=http, base_url="https://api.github.com")


class TestGetPull:
    def test_returns_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/repos/o/r/pulls/5"
            return httpx.Response(200, json={"number": 5, "head": {"sha": "abc"}})

        client = _client(handler)
        pull = client.get_pull("o", "r", 5)
        assert pull["head"]["sha"] == "abc"

    def test_network_error_is_wrapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        client = _client(handler)
        with pytest.raises(GitHubApiError) as exc_info:
            client.get_pull("o", "r", 5)
        assert exc_info.value.status == 0


class TestListChangedFiles:
    def test_paginates_until_empty(self) -> None:
        pages: list[list[dict]] = [[{"filename": f"{i}.py"} for i in range(100)], []]

        def handler(request: httpx.Request) -> httpx.Response:
            assert "page=1" in str(request.url) or "page=2" in str(request.url)
            page = int(request.url.params.get("page"))
            return httpx.Response(200, json=pages[page - 1])

        client = _client(handler)
        files = client.list_changed_files("o", "r", 5)
        assert len(files) == 100


class TestReviews:
    def test_create_review_posts_payload(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == "/repos/o/r/pulls/5/reviews"
            captured["json"] = request.read()
            return httpx.Response(201, json={"id": 1})

        client = _client(handler)
        client.create_review(
            "o",
            "r",
            5,
            commit_id="sha",
            body="Body",
            comments=[{"path": "a.py", "line": 1, "side": "RIGHT", "body": "x"}],
        )
        import json as jsonlib

        payload = jsonlib.loads(captured["json"])
        assert payload == {
            "commit_id": "sha",
            "event": "COMMENT",
            "body": "Body",
            "comments": [{"path": "a.py", "line": 1, "side": "RIGHT", "body": "x"}],
        }

    def test_list_reviews(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/reviews")
            return httpx.Response(200, json=[{"commit_id": "x"}])

        client = _client(handler)
        assert client.list_reviews("o", "r", 5) == [{"commit_id": "x"}]


class TestGetFileContents:
    def test_returns_raw_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/o/r/contents/src/app.py"
            assert request.headers["Accept"] == "application/vnd.github.raw+json"
            return httpx.Response(200, content=b"def f():\n    pass")

        client = _client(handler)
        assert client.get_file_contents("o/r", "src/app.py", "headsha") == "def f():\n    pass"

    def test_not_found_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        client = _client(handler)
        assert client.get_file_contents("o/r", "gone.py", "headsha") is None

    def test_server_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="bad")

        client = _client(handler)
        with pytest.raises(GitHubApiError):
            client.get_file_contents("o/r", "a.py", "headsha")


class TestErrors:
    def test_unexpected_status_raises_detailed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Resource not accessible by integration")

        client = _client(handler)
        with pytest.raises(GitHubApiError) as exc_info:
            client.get_pull("o", "r", 1)
        assert exc_info.value.status == 403

    def test_authorization_header_sent_once(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer secret-token"
            seen.append(request.url.path)
            return httpx.Response(200, json={})

        client = _client(handler)
        client.get_pull("o", "r", 1)
        client.list_reviews("o", "r", 1)
        assert seen == ["/repos/o/r/pulls/1", "/repos/o/r/pulls/1/reviews"]