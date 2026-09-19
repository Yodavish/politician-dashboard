"""Minimal GitHub REST API client used by the AI code reviewer.

The client talks to the public REST API with a scoped ``GITHUB_TOKEN``. The
token is only sent as an ``Authorization`` header and is never logged or
exposed through any other channel.
"""

from __future__ import annotations

import httpx

GITHUB_API = "https://api.github.com"
_RAW_ACCEPT = "application/vnd.github.raw+json"


class GitHubApiError(Exception):
    """A failed GitHub REST API request."""

    def __init__(self, status: int, path: str, detail: str) -> None:
        super().__init__(f"GitHub API {status} on {path}: {detail}")
        self.status = status
        self.path = path
        self.detail = detail


class GitHubClient:
    def __init__(
        self,
        *,
        token: str,
        http: httpx.Client | None = None,
        base_url: str = GITHUB_API,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._owns_http = http is None
        self._http = http or httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "politician-dashboard-ai-review/0.1",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        # Attach the token on every request so it is sent even when :param http:
        # is an injected client, and so it is never leaked through other channels.
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self._base_url}{path}"
        headers = dict(self._auth_headers)
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self._http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubApiError(0, path, str(exc)) from exc
        if not 200 <= response.status_code < 300:
            raise GitHubApiError(response.status_code, path, response.text[:500])
        return response

    def get_pull(self, owner: str, repo: str, number: int) -> dict:
        response = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return response.json()

    def list_reviews(self, owner: str, repo: str, number: int) -> list[dict]:
        response = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews")
        return response.json()

    def list_changed_files(self, owner: str, repo: str, number: int) -> list[dict]:
        files: list[dict] = []
        page = 1
        while True:
            response = self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            batch = response.json()
            if not batch:
                return files
            files.extend(batch)
            if len(batch) < 100:
                return files
            page += 1

    def get_file_contents(self, full_name: str, path: str, ref: str) -> str | None:
        """Return a file's raw contents at ``ref``, or ``None`` if unavailable.

        This is used to give the model surrounding source context. The file may
        live in a fork for cross-repository pull requests, so the head
        repository full name is supplied by the caller.
        """
        try:
            response = self._request(
                "GET",
                f"/repos/{full_name}/contents/{path}",
                params={"ref": ref},
                headers={"Accept": _RAW_ACCEPT},
            )
        except GitHubApiError as exc:
            if exc.status in (404, 403):
                return None
            raise
        return response.text

    def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[dict],
    ) -> dict:
        payload = {
            "commit_id": commit_id,
            "event": "COMMENT",
            "body": body,
            "comments": comments,
        }
        response = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            json=payload,
        )
        return response.json()