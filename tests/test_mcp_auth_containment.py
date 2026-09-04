"""MCP authorization containment (2026-09-04, the external review):
/authorize had auto-approved any allowlisted callback with no login and
handed every client the same ten-year token. Until the authenticated flow
ships: issuance is OFF by default (both endpoints refuse with the how-to-
reconnect message), held tokens keep working, and the /mcp wrapper fails
CLOSED when the secret is unset instead of waving everyone through.
Source-level pins (the server module imports the whole app, so this
reads the file).
"""
import os
import re

SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "server.py")).read()


def test_issuance_off_by_default_and_enforced_on_both_endpoints():
    assert re.search(r'OAUTH_ISSUANCE = os\.environ\.get\("OAUTH_ISSUANCE", "off"\)', SRC)
    auth = SRC.split('@mcp.custom_route("/authorize"')[1].split("@mcp.custom_route")[0]
    tok = SRC.split('@mcp.custom_route("/token"')[1].split("# ── Health check")[0]
    for body in (auth, tok):
        assert "if not OAUTH_ISSUANCE:" in body
        assert "return JSONResponse(_ISSUANCE_OFF, status_code=403)" in body
    assert "tokens keep working" in SRC and "OAUTH_ISSUANCE=on" in SRC


def test_wrapper_fails_closed_without_secret():
    wrap = SRC.split("class AuthASGIWrapper")[1]
    assert "and not MCP_AUTH_TOKEN:" in wrap
    assert 'await self._unauthorized(send, "")' in wrap
    # the fail-closed branch comes BEFORE the token check
    assert wrap.index("and not MCP_AUTH_TOKEN:") < wrap.index("hmac.compare_digest(token, MCP_AUTH_TOKEN)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
