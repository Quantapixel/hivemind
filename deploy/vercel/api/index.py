"""Vercel entry point for the hosted Hivemind dashboard.

Environment (set in the Vercel project):
  FALKORDB_URL             rediss://user:password@host:port of the team database
  HIVEMIND_DASHBOARD_KEY   access code teammates type to open the dashboard
  HIVEMIND_TEAMS           optional, comma-separated teams to show
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hivemind.ui import app_from_env  # noqa: E402

app = app_from_env()
