"""FalkorDB connection and schema. One graph per team: memofull_<team>.

Where to connect comes from, in order: environment variables
(FALKORDB_URL, or FALKORDB_HOST/PORT/PASSWORD), then the private per-user
file written by `memofull connect` (~/.config/memofull/config.json), then
localhost. Credentials never go into the repo.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from falkordb import FalkorDB, Graph

# Node labels and the property each one is looked up by.
INDEXES = [
    ("File", "path"),
    ("Function", "qname"),
    ("Function", "name"),
    ("Developer", "name"),
    ("Session", "id"),
    ("Intent", "id"),
    ("Intent", "status"),
    ("Attempt", "id"),
    ("Decision", "id"),
    ("Convention", "id"),
]


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "memofull" / "config.json"


def load_user_config() -> dict:
    try:
        return json.loads(user_config_path().read_text())
    except (OSError, ValueError):
        return {}


def save_user_config(data: dict) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


@dataclass
class Settings:
    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    team: str = "demo"
    dev: str = "anonymous"
    url: str | None = None          # redis:// or rediss:// (TLS); wins over host/port
    username: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        user = load_user_config()
        return cls(
            host=os.environ.get("FALKORDB_HOST", "localhost"),
            port=int(os.environ.get("FALKORDB_PORT", "6379")),
            password=os.environ.get("FALKORDB_PASSWORD") or None,
            team=os.environ.get("MEMOFULL_TEAM") or user.get("team") or "demo",
            dev=os.environ.get("MEMOFULL_DEV", os.environ.get("USER", "anonymous")),
            url=os.environ.get("FALKORDB_URL") or (None if os.environ.get("FALKORDB_HOST") else user.get("url")),
            username=os.environ.get("FALKORDB_USERNAME") or None,
        )

    def where(self) -> str:
        """Location without credentials, safe to print."""
        if self.url:
            u = urlparse(self.url)
            return f"{u.scheme}://{u.hostname}:{u.port or 6379}"
        return f"{self.host}:{self.port}"


def graph_name(team: str) -> str:
    return f"memofull_{team}"


def open_db(settings: Settings) -> FalkorDB:
    timeouts = {"socket_connect_timeout": 8, "socket_timeout": 20}
    if settings.url:
        return FalkorDB.from_url(settings.url, **timeouts)
    return FalkorDB(
        host=settings.host, port=settings.port, password=settings.password, username=settings.username, **timeouts
    )


def connect(settings: Settings) -> Graph:
    graph = open_db(settings).select_graph(graph_name(settings.team))
    ensure_schema(graph)
    return graph


def ensure_schema(graph: Graph) -> None:
    # One round trip to see what exists, so hooks stay fast against a remote DB.
    try:
        existing = {
            (r[0], p)
            for r in graph.query("CALL db.indexes() YIELD label, properties RETURN label, properties").result_set
            for p in r[1]
        }
    except Exception:  # graph does not exist yet
        existing = set()
    for label, prop in INDEXES:
        if (label, prop) in existing:
            continue
        try:
            graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception as exc:  # created concurrently by a teammate
            if "already indexed" not in str(exc):
                raise


def rows(graph: Graph, query: str, params: dict | None = None) -> list[dict]:
    """Run a query and return each row as a dict keyed by the RETURN aliases."""
    result = graph.query(query, params or {})
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in result.header]
    return [dict(zip(cols, r)) for r in result.result_set]
