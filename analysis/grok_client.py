"""
Thin, reliable client for xAI Grok API.

Designed to be dependency-light in spirit but uses the official `openai` package
for best-in-class support of JSON mode, retries, and streaming (future).

Usage:
    from analysis.grok_client import GrokClient
    client = GrokClient()
    resp = client.chat(system="You are a sharp...", user="Analyze AAPL...", json_mode=True)
    data = resp["parsed"]  # if json_mode

Env:
    XAI_API_KEY   (required)
    XAI_MODEL     (optional, default "grok-3-latest")
    XAI_BASE_URL  (optional, default https://api.x.ai/v1)
"""

import json
import os
from typing import Any, Dict, List, Optional


class GrokClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key or os.environ.get("XAI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "XAI_API_KEY not set. Add it to your .env and `set -a && source .env && set +a`."
            )

        self.model = model or os.environ.get("XAI_MODEL", "grok-3-latest")
        self.base_url = base_url or os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
        self.timeout = timeout

        # Lazy import so the rest of Watchtower doesn't require openai until you actually use Grok features.
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "The `openai` package is required for Grok features. "
                "Install with: python -m pip install --user openai"
            ) from e

        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)

    def chat(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
        temperature: float = 0.4,
        max_tokens: int = 2000,
    ) -> Dict[str, Any]:
        """
        Simple chat completion.

        If json_mode=True, asks for JSON and returns {"text": ..., "parsed": dict or None}.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self._client.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
        except Exception as e:
            raise RuntimeError(f"Grok API call failed: {type(e).__name__}: {e}") from e

        result: Dict[str, Any] = {"text": text, "model": self.model, "usage": None}
        if hasattr(resp, "usage") and resp.usage:
            result["usage"] = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }

        if json_mode:
            try:
                result["parsed"] = json.loads(text)
            except Exception:
                result["parsed"] = None
                result["parse_error"] = "Failed to parse JSON from model output"

        return result

    def synthesize(
        self,
        system_prompt: str,
        user_prompt: str,
        expect_json: bool = True,
    ) -> Dict[str, Any]:
        """Convenience wrapper tuned for our synthesis use case."""
        return self.chat(
            system=system_prompt,
            user=user_prompt,
            json_mode=expect_json,
            temperature=0.35,  # A bit of creativity but stay grounded
            max_tokens=2200,
        )
