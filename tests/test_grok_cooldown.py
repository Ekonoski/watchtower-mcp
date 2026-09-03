"""The Grok circuit breaker (2026-09-03, the dry-account morning):
a credits / spending-limit / permission-denied error puts the client
into a 30-minute cooldown, logged once; inside the cooldown chat()
refuses without a network call; unrelated errors do not trip it.
"""
import inspect
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import grok_client as gc  # noqa: E402


def test_credit_error_detection():
    assert gc._is_credits_error("PermissionDeniedError: Error code: 403 - {'code': "
                                "'permission-denied', 'error': 'Your team has either used "
                                "all available credits or reached its monthly spending limit.'}")
    assert gc._is_credits_error("reached its monthly spending limit")
    assert not gc._is_credits_error("APITimeoutError: Request timed out")
    assert not gc._is_credits_error("RateLimitError: 429 too many requests")


def test_cooldown_gates_chat_without_a_call():
    class Boom:
        calls = 0

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    Boom.calls += 1
                    raise RuntimeError("Error code: 403 - permission-denied: used all "
                                       "available credits")
    c = gc.GrokClient.__new__(gc.GrokClient)
    c.model, c._client = "test", Boom()
    gc._COOLDOWN_UNTIL = 0.0
    try:
        c.chat("s", "u")
    except RuntimeError as e:
        assert "credits" in str(e)
    assert Boom.calls == 1 and gc.cooldown_remaining() > 0
    # second call inside the cooldown never reaches the network
    try:
        c.chat("s", "u")
    except RuntimeError as e:
        assert "cooldown" in str(e)
    assert Boom.calls == 1
    gc._COOLDOWN_UNTIL = 0.0                     # leave the module clean
    assert gc.COOLDOWN_S == 30 * 60
    src = inspect.getsource(gc.GrokClient.chat)
    assert "_is_credits_error" in src and "log.warning" in src
    assert time.time() > gc._COOLDOWN_UNTIL


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
