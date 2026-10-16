"""FalkorDB connection and schema. One graph per team: hivemind_<team>."""

from __future__ import annotations

import os
from dataclasses import dataclass

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


@dataclass
class Settings:
    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    team: str = "demo"
    dev: str = "anonymous"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("FALKORDB_HOST", "localhost"),
            port=int(os.environ.get("FALKORDB_PORT", "6379")),
            password=os.environ.get("FALKORDB_PASSWORD") or None,
            team=os.environ.get("HIVEMIND_TEAM", "demo"),
            dev=os.environ.get("HIVEMIND_DEV", os.environ.get("USER", "anonymous")),
        )


def graph_name(team: str) -> str:
    return f"hivemind_{team}"


def connect(settings: Settings) -> Graph:
    db = FalkorDB(host=settings.host, port=settings.port, password=settings.password)
    graph = db.select_graph(graph_name(settings.team))
    ensure_schema(graph)
    return graph


def ensure_schema(graph: Graph) -> None:
    for label, prop in INDEXES:
        try:
            graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception as exc:  # already indexed
            if "already indexed" not in str(exc):
                raise


def rows(graph: Graph, query: str, params: dict | None = None) -> list[dict]:
    """Run a query and return each row as a dict keyed by the RETURN aliases."""
    result = graph.query(query, params or {})
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in result.header]
    return [dict(zip(cols, r)) for r in result.result_set]
