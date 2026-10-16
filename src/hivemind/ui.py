"""Live view of the team graph: code, who is working where, conflicts and memory."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .brain import Brain
from .db import Settings, connect, rows

STATIC = Path(__file__).parent / "static"


def snapshot(brain: Brain) -> dict:
    g = brain.g
    nodes: list[dict] = []
    links: list[dict] = []

    for r in rows(g, "MATCH (f:Function) RETURN f.qname AS id, f.name AS name, f.file AS file"):
        nodes.append({**r, "kind": "function"})
    for r in rows(g, "MATCH (a:Function)-[:CALLS]->(b:Function) RETURN a.qname AS source, b.qname AS target"):
        links.append({**r, "kind": "calls"})

    for r in rows(
        g,
        """
        MATCH (i:Intent) WHERE i.status IN ['active', 'handed_off']
        RETURN i.id AS id, i.description AS name, i.dev AS dev, i.status AS status
        """,
    ):
        nodes.append({**r, "kind": "intent"})
    for r in rows(
        g,
        """
        MATCH (i:Intent)-[:CLAIMS]->(f:Function) WHERE i.status IN ['active', 'handed_off']
        RETURN i.id AS source, f.qname AS target, i.dev AS dev
        """,
    ):
        links.append({**r, "kind": "claims"})

    for r in rows(
        g,
        """
        MATCH (a:Attempt)-[:TOUCHED]->(f:Function)
        RETURN a.id AS id, a.summary AS name, a.outcome AS outcome, a.reason AS reason,
               a.dev AS dev, collect(f.qname) AS targets
        """,
    ):
        targets = r.pop("targets")
        nodes.append({**r, "kind": "attempt"})
        links += [{"source": r["id"], "target": t, "kind": "touched"} for t in targets]

    for r in rows(
        g,
        """
        MATCH (x:Decision)-[:GOVERNS]->(n)
        RETURN x.id AS id, x.title AS name, x.rationale AS rationale, x.dev AS dev,
               collect(CASE WHEN n:Function THEN n.qname END) AS fns,
               collect(CASE WHEN n:File THEN n.path END) AS files
        """,
    ):
        fns, files = r.pop("fns"), r.pop("files")
        # A decision on a file points at every function in it.
        if files:
            fns += [x["q"] for x in rows(g, "MATCH (f:Function) WHERE f.file IN $p RETURN f.qname AS q", {"p": files})]
        nodes.append({**r, "kind": "decision"})
        links += [{"source": r["id"], "target": t, "kind": "governs"} for t in dict.fromkeys(f for f in fns if f)]

    # Conflict paths across the whole team, highlighted in the view.
    conflicts: list[dict] = []
    seen: set[tuple] = set()
    for task in brain.open_tasks():
        if task["status"] != "active" or not task["claimed"]:
            continue
        viewer = Brain(g, task["dev"])
        for c in viewer.conflicts(task["claimed"]):
            key = tuple(sorted([task["intent_id"], c["intent_id"]])) + tuple(sorted([c["target"], c["other_function"]]))
            if key in seen:
                continue
            seen.add(key)
            conflicts.append({**c, "dev": task["dev"], "my_intent": task["description"]})

    devs = sorted({n["dev"] for n in nodes if n.get("dev")})
    return {"nodes": nodes, "links": links, "conflicts": conflicts, "devs": devs}


def create_app(settings: Settings) -> Starlette:
    brain = Brain(connect(settings), settings.dev, "ui")

    async def index(_: Request) -> FileResponse:
        return FileResponse(STATIC / "index.html")

    async def graph(_: Request) -> JSONResponse:
        return JSONResponse(snapshot(brain))

    async def feed(_: Request) -> JSONResponse:
        return JSONResponse({"events": brain.recent_events(40), "tasks": brain.open_tasks(), "team": settings.team})

    async def node(request: Request) -> JSONResponse:
        target = request.query_params.get("id", "")
        return JSONResponse(
            {"why": brain.why(target), "blast_radius": brain.blast_radius(target), "experts": brain.experts(target)}
        )

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/graph", graph),
            Route("/api/feed", feed),
            Route("/api/node", node),
        ]
    )


def run(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"Hivemind live view for team '{settings.team}': http://{host}:{port}")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
