"""HTTP client for a local Ollama server."""

from __future__ import annotations

import httpx


class OllamaError(Exception):
    """A failed or unusable Ollama chat request."""


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(1200.0, connect=30.0),
        )

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        format_schema: dict,
        options: dict,
    ) -> str:
        """Run a single, non-streamed chat completion and return the text.

        The returned text is expected to be JSON when ``format_schema`` is
        given; parsing is the caller's responsibility.
        """
        try:
            response = self._http.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "format": format_schema,
                    "stream": False,
                    "options": options,
                },
            )
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc
        if response.status_code != 200:
            raise OllamaError(
                f"Ollama API {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        content = (data.get("message") or {}).get("content") or ""
        if not content:
            raise OllamaError("Ollama returned an empty completion")
        return content