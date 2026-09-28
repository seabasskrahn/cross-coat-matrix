"""Test helper: act like the dashboard page in a browser. Loads "/" once per server to get the
session cookie + the token embedded in the page, then sends both (plus a matching Origin) on POSTs.
Only ever used against test servers (in-memory store, ephemeral port)."""
import re
import urllib.request

TOKEN_RE = re.compile(r'<meta name="keeper-token" content="([^"]+)">')


def base(srv) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}"


def new_session(srv) -> dict:
    """Headers a real page would send: Cookie + X-Keeper-Token + Origin (fresh session each call)."""
    try:
        r = urllib.request.urlopen(base(srv) + "/", timeout=5)
    except urllib.error.HTTPError as e:  # e.g. the 503 "no database" page still hands out a session
        r = e
    cookie = r.headers.get("Set-Cookie", "").split(";", 1)[0]
    token = TOKEN_RE.search(r.read().decode("utf-8")).group(1)
    return {"Cookie": cookie, "X-Keeper-Token": token, "Origin": base(srv)}


def auth(srv) -> dict:
    """The same session for every POST to this server."""
    if not hasattr(srv, "_test_auth"):
        srv._test_auth = new_session(srv)
    return dict(srv._test_auth)
