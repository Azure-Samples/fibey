"""System instructions shared by local and hosted agents."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def load_system_prompt() -> str:
    prompt = (Path(__file__).parent / "system_prompt.md").read_text()
    dashboard = os.getenv("STATUS_DASHBOARD_URL", "").strip()
    if not dashboard:
        return prompt + (
            "\n\nSTATUS_DASHBOARD_URL is not configured. For network-status requests, "
            "say you cannot read the Status Dashboard; do not browse or guess."
        )
    parsed = urlsplit(dashboard)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or any(character.isspace() for character in dashboard)
    ):
        raise ValueError("STATUS_DASHBOARD_URL must be an HTTP(S) URL without credentials, query, or fragment")
    return prompt + (
        f"\n\nConfigured Status Dashboard URL: {json.dumps(dashboard)}\n"
        "After reading this page and closing the browser session, end the final "
        "network-status answer with this exact source line (also required when "
        "other sources are included):\n"
        f"Source: [Status Dashboard]({dashboard})\n"
        "Do not omit this citation or replace it with an offer to help further.\n"
    )
