"""Advisory AI-assisted PR code reviewer.

Runs on GitHub-hosted runners using the GitHub REST API and a local
Ollama model. It never checks out or executes pull request code.
"""

from __future__ import annotations

from tools.ai_review.models import REVIEW_MARKER

__all__ = ["REVIEW_MARKER"]