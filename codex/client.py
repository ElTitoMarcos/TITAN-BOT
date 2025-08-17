"""Simple Codex client used only for credential verification."""
from __future__ import annotations

import requests
from typing import Optional


class CodexClient:
    """Client wrapper to check Codex API key validity."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.openai.com") -> None:
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/")

    def check_credentials(self) -> bool:
        """Performs a minimal authenticated request.

        The endpoint is intentionally generic so this client can operate even if
        the real Codex service is mocked in tests. Any error returns ``False``.
        """
        if not self.api_key:
            return False
        try:
            resp = requests.get(
                f"{self.base_url}/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
