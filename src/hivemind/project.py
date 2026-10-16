"""Find the Hivemind project a command runs in, and who is running it.

`hivemind init` writes .hivemind/config.json at the repo root (team and
FalkorDB location). Hooks and MCP servers walk up from their working
directory to find it, so hooks installed globally (Codex) stay inert in
repos that are not Hivemind projects.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .db import Settings

MARKER = Path(".hivemind") / "config.json"


def find_root(start: str | Path | None = None) -> Path | None:
    env = os.environ.get("HIVEMIND_ROOT")
    if env and (Path(env) / MARKER).exists():
        return Path(env).resolve()
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / MARKER).exists():
            return d
    return None


def default_dev(cwd: str | Path | None = None) -> str:
    """HIVEMIND_DEV, else the git user name, else the login name."""
    if os.environ.get("HIVEMIND_DEV"):
        return os.environ["HIVEMIND_DEV"]
    try:
        name = subprocess.run(
            ["git", "config", "user.name"], cwd=cwd or None, capture_output=True, text=True, timeout=2
        ).stdout.strip()
        if name:
            return name
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("USER", "anonymous")


def load_settings(root: Path) -> Settings:
    cfg = json.loads((root / MARKER).read_text())
    return Settings(
        host=os.environ.get("FALKORDB_HOST", cfg.get("host", "localhost")),
        port=int(os.environ.get("FALKORDB_PORT", cfg.get("port", 6379))),
        password=os.environ.get("FALKORDB_PASSWORD") or cfg.get("password") or None,
        team=os.environ.get("HIVEMIND_TEAM", cfg.get("team", "demo")),
        dev=default_dev(root),
    )


def write_config(root: Path, settings: Settings) -> Path:
    path = root / MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"team": settings.team, "host": settings.host, "port": settings.port}, indent=2) + "\n")
    (path.parent / ".gitignore").write_text("session.*\n")
    return path


def session_file(root: Path, agent: str) -> Path:
    return root / ".hivemind" / f"session.{agent}"


def read_session(root: Path, agent: str) -> str | None:
    try:
        return session_file(root, agent).read_text().strip() or None
    except OSError:
        return None
