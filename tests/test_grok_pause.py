"""The Grok kill switch (2026-09-18, Eric: "stop the system temporarily
that pulls from grok api... it's just wasting money").

  1. Default OFF: with GROK_ENABLED unset, constructing the client raises
     with the pause reason — before it even looks for an API key.
  2. GROK_ENABLED=on restores the old behaviour (a missing key is then the
     usual XAI_API_KEY error).
  3. Every caller builds the client inside its own try/except and treats a
     failure as *unavailable* — pinned by source for the four call sites.
  4. The daily social scan writes NO placeholder rows while Grok is off.

Standalone:  python3 tests/test_grok_pause.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import grok_client as gc  # noqa: E402


def test_default_is_paused():
    os.environ.pop("GROK_ENABLED", None)
    os.environ["XAI_API_KEY"] = "not-a-real-key"
    assert gc.grok_enabled() is False
    try:
        gc.GrokClient()
    except RuntimeError as e:
        assert "GROK_ENABLED" in str(e) and "paused" in str(e).lower()
    else:
        raise AssertionError("paused client must not construct")


def test_on_restores_the_key_check():
    os.environ["GROK_ENABLED"] = "on"
    os.environ.pop("XAI_API_KEY", None)
    assert gc.grok_enabled() is True
    try:
        gc.GrokClient()
    except RuntimeError as e:
        assert "XAI_API_KEY" in str(e)
    else:
        raise AssertionError("no key must still refuse")
    for v in ("off", "0", "false", ""):
        os.environ["GROK_ENABLED"] = v
        assert gc.grok_enabled() is False
    os.environ.pop("GROK_ENABLED", None)


def test_every_caller_falls_back():
    from analysis import grok_synthesizer, news_scanner, social_buzz
    assert "except" in inspect.getsource(news_scanner._get_grok_client)
    assert "except" in inspect.getsource(social_buzz._get_grok)
    assert "except RuntimeError" in inspect.getsource(grok_synthesizer.synthesize_screen_results)
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "server.py")) as f:
        src = f.read()
    i = src.index("grok = GrokClient()")
    assert "try:" in src[i - 200:i]


def test_social_scan_writes_nothing_when_paused():
    from analysis import social_buzz
    src = inspect.getsource(social_buzz.run_social_buzz_scan)
    assert "if grok is None:" in src and "return []" in src
    assert src.index("if grok is None:") < src.index("_upsert_sentiment(")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
