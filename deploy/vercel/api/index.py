"""Vercel entry point for the hosted Hivemind dashboard.

Environment (set in the Vercel project):
  FALKORDB_URL             redis[s]://user:password@host:port of the team database
  HIVEMIND_DASHBOARD_KEY   access code teammates type to open the dashboard
  HIVEMIND_TEAMS           optional, comma-separated teams to show
"""

import os
import sys
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hivemind.ui import app_from_env  # noqa: E402


def original_paths(app):
    """vercel.json rewrites every URL to /api/index?__path=<original>; give the
    app back the path the browser asked for."""

    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            qs = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
            path = next((v for k, v in qs if k == "__path"), None)
            if path is not None:
                path = "/" + path.lstrip("/")
                rest = urlencode([(k, v) for k, v in qs if k != "__path"]).encode()
                scope = dict(scope, path=path, raw_path=path.encode(), query_string=rest)
        await app(scope, receive, send)

    return wrapped


app = original_paths(app_from_env())
