"""Find the Memofull project a command runs in, and who is running it.

`memofull init` writes .memofull/config.json at the repo root (team and
FalkorDB location). Hooks and MCP servers walk up from their working
directory to find it, so hooks installed globally (Codex) stay inert in
repos that are not Memofull projects.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .db import Settings

MARKER = Path(".memofull") / "config.json"


def find_root(start: str | Path | None = None) -> Path | None:
    env = os.environ.get("MEMOFULL_ROOT")
    if env and (Path(env) / MARKER).exists():
        return Path(env).resolve()
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / MARKER).exists():
            return d
    return None


def default_dev(cwd: str | Path | None = None) -> str:
    """MEMOFULL_DEV, else the git user name, else the login name."""
    if os.environ.get("MEMOFULL_DEV"):
        return os.environ["MEMOFULL_DEV"]
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
    """Team from the project file; where to connect from the environment or
    the developer's private `memofull connect` config, else the project file."""
    cfg = json.loads((root / MARKER).read_text())
    s = Settings.from_env()
    s.team = os.environ.get("MEMOFULL_TEAM") or cfg.get("team") or s.team
    if not s.url and "FALKORDB_HOST" not in os.environ:
        s.host = cfg.get("host", s.host)
        s.port = int(cfg.get("port", s.port))
    s.dev = default_dev(root)
    return s


def write_config(root: Path, settings: Settings) -> Path:
    """Only non-secret settings go in the repo: the team, and a host when
    connecting without a URL (a URL carries the password, so it stays in the
    developer's private config)."""
    path = root / MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {"team": settings.team}
    if not settings.url:
        data.update(host=settings.host, port=settings.port)
    path.write_text(json.dumps(data, indent=2) + "\n")
    (path.parent / ".gitignore").write_text("session.*\n")
    return path


def session_file(root: Path, agent: str) -> Path:
    return root / ".memofull" / f"session.{agent}"


def read_session(root: Path, agent: str) -> str | None:
    try:
        return session_file(root, agent).read_text().strip() or None
    except OSError:
        return None
