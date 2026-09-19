"""Run the advisory AI code reviewer as a module:

    python -m tools.ai_review
"""

from __future__ import annotations

import sys

from tools.ai_review.main import main

if __name__ == "__main__":
    sys.exit(main())