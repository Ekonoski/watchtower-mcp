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
    XAI_API_KEY      (required)
    XAI_MODEL        (optional, default "grok-3-latest")
    XAI_SEARCH_MODEL (optional, default "grok-4.3") — model used for live
                     web/X search via the Agent Tools / Responses API.
    XAI_BASE_URL     (optional, default https://api.x.ai/v1)
"""

import json
import os
from typing import Any, Dict, List, Optional


import logging as _logging
import time

log = _logging.getLogger("watchtower.grok")
COOLDOWN_S = 30 * 60
_COOLDOWN_UNTIL = 0.0
_CREDIT_MARKERS = ("credits", "spending limit", "permission-denied", "permissiondenied")


def _is_credits_error(msg: str) -> bool:
    m = (msg or "").lower()
    return any(k in m for k in _CREDIT_MARKERS)


def cooldown_remaining() -> float:
    """Seconds left on the credits cooldown (0 when live)."""
    return max(0.0, _COOLDOWN_UNTIL - time.time())


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
        # Agent Tools (server-side web_search / x_search) require a grok-4-class model.
        self.search_model = os.environ.get("XAI_SEARCH_MODEL", "grok-4.3")
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

        # Circuit breaker (2026-09-03): a dry xAI account answered every
        # call with 403 "used all available credits or reached its monthly
        # spending limit" and the scanners retried it every few seconds
        # per ticker. On that class of error the client stands down for
        # COOLDOWN_S, logs ONCE, and callers get their usual exception —
        # which they already render as *unavailable*, never as data.
        global _COOLDOWN_UNTIL
        now = time.time()
        if now < _COOLDOWN_UNTIL:
            raise RuntimeError(
                f"Grok unavailable: credits/permission cooldown until "
                f"{time.strftime('%H:%M:%S', time.localtime(_COOLDOWN_UNTIL))}")
        try:
            resp = self._client.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if _is_credits_error(msg):
                _COOLDOWN_UNTIL = now + COOLDOWN_S
                log.warning("[grok] account refused (credits/spending limit) — "
                            "standing down for %d min. Full error: %s",
                            COOLDOWN_S // 60, msg[:600])
            raise RuntimeError(f"Grok API call failed: {msg}") from e

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

    def search_chat(
        self,
        system: str,
        user: str,
        max_output_tokens: int = 800,
    ) -> Dict[str, Any]:
        """
        Real-time answer using xAI Agent Tools — server-side web_search + x_search
        via the Responses API. Grok runs the searches on xAI infrastructure and
        returns a final answer, so this is genuinely live (unlike a plain chat,
        which only knows its training cutoff). Requires a grok-4-class model
        (self.search_model).

        Returns {"text": ..., "parsed": dict or None, ...}. Raises on API error so
        callers can fall back to a plain chat().

        NOTE: xAI deprecated the old Live Search `search_parameters` field (410) in
        favour of these Agent Tools. Tool type strings: "web_search", "x_search".
        """
        resp = self._client.responses.create(
            model=self.search_model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[{"type": "web_search"}, {"type": "x_search"}],
            max_output_tokens=max_output_tokens,
        )

        text = getattr(resp, "output_text", None) or ""
        out: Dict[str, Any] = {"text": text, "model": self.search_model, "source": "agent_tools"}

        parsed = None
        try:
            parsed = json.loads(text)
        except Exception:
            # Tool-using responses can wrap JSON in prose/code fences — extract the
            # outermost {...} and retry before giving up.
            try:
                snippet = text[text.index("{"): text.rindex("}") + 1]
                parsed = json.loads(snippet)
            except Exception:
                parsed = None
        out["parsed"] = parsed
        return out

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
