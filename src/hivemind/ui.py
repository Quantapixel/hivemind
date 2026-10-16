"""Live view of a team graph: code, who is working where, conflicts and memory.

Every endpoint takes ?team=<name> so one server can show any team graph in
the FalkorDB instance.
"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from falkordb import FalkorDB
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .brain import Brain
from .db import Settings, connect, graph_name, rows

STATIC = Path(__file__).parent / "static"
PREFIX = graph_name("")


class Teams:
    """One connection per team graph, plus cached graph-algorithm scores."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = FalkorDB(host=settings.host, port=settings.port, password=settings.password)
        self._brains: dict[str, Brain] = {}
        self._metrics: dict[str, tuple[tuple, dict]] = {}

    def names(self) -> list[str]:
        return sorted(g[len(PREFIX) :] for g in self.db.list_graphs() if g.startswith(PREFIX))

    def brain(self, team: str | None) -> Brain:
        team = team or self.settings.team
        if team not in self._brains:
            s = Settings(**{**self.settings.__dict__, "team": team})
            self._brains[team] = Brain(connect(s), s.dev, "ui")
        return self._brains[team]

    def metrics(self, team: str, brain: Brain) -> dict[str, dict]:
        """PageRank (how much depends on a function) and betweenness (how
        many call paths run through it), recomputed only when the code graph
        changes."""
        shape = rows(
            brain.g,
            "MATCH (f:Function) OPTIONAL MATCH (f)-[c:CALLS]->() RETURN count(DISTINCT f) AS f, count(c) AS c",
        )[0]
        key = (shape["f"], shape["c"])
        cached = self._metrics.get(team)
        if cached and cached[0] == key:
            return cached[1]
        out: dict[str, dict] = {}
        for r in rows(
            brain.g,
            "CALL algo.pageRank('Function', 'CALLS') YIELD node, score RETURN node.qname AS id, score",
        ):
            out.setdefault(r["id"], {})["rank"] = r["score"]
        for r in rows(
            brain.g,
            """
            CALL algo.betweenness({nodeLabels: ['Function'], relationshipTypes: ['CALLS']})
            YIELD node, score RETURN node.qname AS id, score
            """,
        ):
            out.setdefault(r["id"], {})["between"] = r["score"]
        for key_name in ("rank", "between"):
            top = max((m.get(key_name, 0) for m in out.values()), default=0) or 1
            for m in out.values():
                m[key_name] = round(m.get(key_name, 0) / top, 4)
        self._metrics[team] = (key, out)
        return out


def state(teams: Teams, team: str) -> dict:
    brain = teams.brain(team)
    g = brain.g
    metrics = teams.metrics(team, brain)
    nodes: list[dict] = []
    links: list[dict] = []

    failed = {
        r["id"]: r["n"]
        for r in rows(g, "MATCH (a:Attempt {outcome: 'failed'})-[:TOUCHED]->(f:Function) RETURN f.qname AS id, count(a) AS n")
    }
    for r in rows(
        g,
        """
        MATCH (f:Function)
        OPTIONAL MATCH (c:Function)-[:CALLS]->(f)
        RETURN f.qname AS id, f.name AS name, f.file AS file, f.lineno AS line, count(c) AS callers
        """,
    ):
        m = metrics.get(r["id"], {})
        nodes.append({**r, "kind": "function", "rank": m.get("rank", 0), "between": m.get("between", 0), "failed": failed.get(r["id"], 0)})
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
        RETURN a.id AS id, a.summary AS name, a.outcome AS outcome, a.dev AS dev,
               coalesce(a.resolved, false) AS resolved, coalesce(a.count, 1) AS times, collect(f.qname) AS targets
        """,
    ):
        targets = r.pop("targets")
        nodes.append({**r, "kind": "attempt"})
        links += [{"source": r["id"], "target": t, "kind": "touched"} for t in targets]

    for r in rows(
        g,
        """
        MATCH (x:Decision)-[:GOVERNS]->(n) WHERE coalesce(x.superseded, false) = false
        RETURN x.id AS id, x.title AS name, x.dev AS dev,
               collect(CASE WHEN n:Function THEN n.qname END) AS fns,
               collect(CASE WHEN n:File THEN n.path END) AS files
        """,
    ):
        fns, files = r.pop("fns"), r.pop("files")
        if files:
            fns += [x["q"] for x in rows(g, "MATCH (f:Function) WHERE f.file IN $p RETURN f.qname AS q", {"p": files})]
        nodes.append({**r, "kind": "decision"})
        links += [{"source": r["id"], "target": t, "kind": "governs"} for t in dict.fromkeys(f for f in fns if f)]

    tasks = brain.open_tasks()
    conflicts: list[dict] = []
    seen: set[tuple] = set()
    for task in tasks:
        if task["status"] != "active" or not task["claimed"]:
            continue
        for c in Brain(g, task["dev"]).conflicts(task["claimed"]):
            key = tuple(sorted([task["intent_id"], c["intent_id"]])) + tuple(sorted([c["target"], c["other_function"]]))
            if key in seen:
                continue
            seen.add(key)
            conflicts.append({**c, "dev": task["dev"], "my_intent": task["description"], "my_intent_id": task["intent_id"]})

    devs = rows(
        g,
        """
        MATCH (d:Developer)
        OPTIONAL MATCH (d)-[:OWNS]->(i:Intent) WHERE i.status IN ['active', 'handed_off']
        OPTIONAL MATCH (d)-[:RAN]->(s:Session)
        RETURN d.name AS name, count(DISTINCT i) AS open_tasks, count(DISTINCT s) AS sessions,
               [a IN collect(DISTINCT s.agent) WHERE a IS NOT NULL AND NOT a IN ['ui', 'agent']] AS agents
        ORDER BY name
        """,
    )
    last_seen = {r["dev"]: r["ts"] for r in rows(g, "MATCH (e:Event) RETURN e.dev AS dev, max(e.ts) AS ts")}
    for d in devs:
        d["last_seen"] = last_seen.get(d["name"])

    counts = rows(
        g,
        """
        OPTIONAL MATCH (f:File) WITH count(f) AS files
        OPTIONAL MATCH (a:Attempt) WITH files, count(a) AS attempts
        OPTIONAL MATCH (x:Decision) WITH files, attempts, count(x) AS decisions
        OPTIONAL MATCH (c:Convention) RETURN files, attempts, decisions, count(c) AS conventions
        """,
    )[0]
    stats = {
        **counts,
        "functions": sum(1 for n in nodes if n["kind"] == "function"),
        "calls": sum(1 for link in links if link["kind"] == "calls"),
        "active": sum(1 for t in tasks if t["status"] == "active"),
        "handed_off": sum(1 for t in tasks if t["status"] == "handed_off"),
        "conflicts": len(conflicts),
    }
    return {
        "team": team,
        "nodes": nodes,
        "links": links,
        "conflicts": conflicts,
        "tasks": tasks,
        "devs": devs,
        "events": brain.recent_events(60),
        "stats": stats,
    }


def function_detail(teams: Teams, team: str, fid: str) -> dict:
    brain = teams.brain(team)
    info = rows(
        brain.g,
        "MATCH (f:Function {qname: $id}) RETURN f.qname AS id, f.name AS name, f.file AS file, f.lineno AS line, f.end_lineno AS end",
        {"id": fid},
    )
    if not info:
        return {"error": "not found"}
    callers = [r["id"] for r in rows(brain.g, "MATCH (c:Function)-[:CALLS]->(:Function {qname: $id}) RETURN c.qname AS id ORDER BY id", {"id": fid})]
    callees = [r["id"] for r in rows(brain.g, "MATCH (:Function {qname: $id})-[:CALLS]->(c:Function) RETURN c.qname AS id ORDER BY id", {"id": fid})]
    claims = rows(
        brain.g,
        """
        MATCH (i:Intent)-[:CLAIMS]->(:Function {qname: $id}) WHERE i.status IN ['active', 'handed_off']
        RETURN i.id AS intent_id, i.dev AS dev, i.description AS description, i.status AS status
        """,
        {"id": fid},
    )
    ctx = brain.context([fid])
    m = teams.metrics(team, brain).get(fid, {})
    return {
        **info[0],
        "rank": m.get("rank", 0),
        "between": m.get("between", 0),
        "callers": callers,
        "callees": callees,
        "claims": claims,
        "blast_radius": brain.blast_radius(fid, 4),
        "decisions": ctx["decisions"],
        "attempts": ctx["attempts"],
        "conventions": ctx["conventions"],
        "experts": brain.experts(fid),
        "changes": rows(
            brain.g,
            """
            MATCH (c:Change)-[:CHANGED]->(:Function {qname: $id})
            RETURN c.dev AS dev, c.agent AS agent, c.tool AS tool, c.ts AS ts,
                   CASE WHEN $id IN c.added THEN 'added' ELSE 'changed' END AS kind
            ORDER BY ts DESC LIMIT 12
            """,
            {"id": fid},
        ),
    }


def find_path(teams: Teams, team: str, a: str, b: str) -> dict:
    """Shortest call path between two functions, in whichever direction exists."""
    g = teams.brain(team).g
    for src, dst, direction in ((a, b, "calls"), (b, a, "called_by")):
        r = rows(
            g,
            """
            MATCH (x:Function {qname: $a}), (y:Function {qname: $b})
            RETURN [n IN nodes(shortestPath((x)-[:CALLS*]->(y))) | n.qname] AS path
            """,
            {"a": src, "b": dst},
        )
        if r and r[0]["path"]:
            path = r[0]["path"] if direction == "calls" else list(reversed(r[0]["path"]))
            return {"path": path, "direction": direction}
    # No direct chain: find the closest shared caller, the most common hidden
    # coupling, and return both chains joined at it: a <- ... <- via -> ... -> b.
    r = rows(
        g,
        """
        MATCH (x:Function {qname: $a}), (y:Function {qname: $b}), (c:Function)
        WHERE c <> x AND c <> y
        WITH x, y, c,
             [n IN nodes(shortestPath((c)-[:CALLS*]->(x))) | n.qname] AS left,
             [n IN nodes(shortestPath((c)-[:CALLS*]->(y))) | n.qname] AS right
        WHERE left IS NOT NULL AND right IS NOT NULL AND size(left) > 0 AND size(right) > 0
        RETURN c.qname AS via, left, right
        ORDER BY size(left) + size(right) LIMIT 1
        """,
        {"a": a, "b": b},
    )
    if r:
        return {"path": list(reversed(r[0]["left"])) + r[0]["right"][1:], "direction": "shared_caller", "via": r[0]["via"]}
    return {"path": [], "direction": "none"}


def memory(teams: Teams, team: str) -> dict:
    g = teams.brain(team).g
    return {
        "decisions": rows(
            g,
            """
            MATCH (x:Decision) OPTIONAL MATCH (x)-[:GOVERNS]->(n)
            RETURN x.id AS id, x.title AS title, x.rationale AS rationale, x.dev AS dev, x.source AS source,
                   x.ts AS ts, coalesce(x.superseded, false) AS superseded, x.superseded_title AS superseded_by,
                   collect(coalesce(n.qname, n.path)) AS governs
            ORDER BY ts DESC
            """,
        ),
        "attempts": rows(
            g,
            """
            MATCH (a:Attempt) OPTIONAL MATCH (a)-[:TOUCHED]->(f:Function)
            RETURN a.id AS id, a.summary AS summary, a.outcome AS outcome, a.reason AS reason, a.dev AS dev,
                   a.ts AS ts, coalesce(a.count, 1) AS times, coalesce(a.devs, [a.dev]) AS seen_by,
                   coalesce(a.auto, false) AS auto, coalesce(a.resolved, false) AS resolved,
                   a.resolved_dev AS resolved_by, a.resolved_summary AS fix, a.agent AS agent,
                   collect(f.qname) AS touched
            ORDER BY ts DESC
            """,
        ),
        "conventions": rows(
            g,
            """
            MATCH (c:Convention) OPTIONAL MATCH (c)-[:APPLIES_TO]->(f:File)
            RETURN c.id AS id, c.rule AS rule, c.dev AS dev, c.scope AS scope, c.ts AS ts,
                   coalesce(c.count, 1) AS times, collect(f.path) AS files
            ORDER BY ts DESC
            """,
        ),
    }


def create_app(settings: Settings) -> Starlette:
    teams = Teams(settings)

    def team_of(request: Request) -> str:
        return request.query_params.get("team") or settings.team

    async def index(_: Request) -> FileResponse:
        return FileResponse(STATIC / "index.html")

    async def api_teams(_: Request) -> JSONResponse:
        return JSONResponse({"teams": teams.names(), "default": settings.team})

    async def api_state(request: Request) -> JSONResponse:
        return JSONResponse(state(teams, team_of(request)))

    async def api_function(request: Request) -> JSONResponse:
        return JSONResponse(function_detail(teams, team_of(request), request.query_params.get("id", "")))

    async def api_path(request: Request) -> JSONResponse:
        q = request.query_params
        return JSONResponse(find_path(teams, team_of(request), q.get("from", ""), q.get("to", "")))

    async def api_memory(request: Request) -> JSONResponse:
        return JSONResponse(memory(teams, team_of(request)))

    async def api_write(request: Request) -> JSONResponse:
        body = await request.json()
        dev = (body.get("dev") or "").strip()
        if not dev:
            return JSONResponse({"error": "who are you? set your name first"}, status_code=400)
        brain = Brain(teams.brain(team_of(request)).g, dev, "ui")
        kind = request.path_params["kind"]
        if kind == "decision":
            if not body.get("title"):
                return JSONResponse({"error": "title is required"}, status_code=400)
            out = brain.record_decision(body["title"], body.get("rationale", ""), body.get("targets") or [], body.get("source", ""))
        elif kind == "convention":
            if not body.get("rule"):
                return JSONResponse({"error": "rule is required"}, status_code=400)
            out = brain.add_convention(body["rule"], body.get("files") or None)
        elif kind == "release":
            out = brain.release(body["intent_id"], body.get("outcome", "abandoned"), body.get("summary", "released from the live view"))
        else:
            return JSONResponse({"error": f"unknown write {kind}"}, status_code=404)
        return JSONResponse(out)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/teams", api_teams),
            Route("/api/state", api_state),
            Route("/api/function", api_function),
            Route("/api/path", api_path),
            Route("/api/memory", api_memory),
            Route("/api/{kind}", api_write, methods=["POST"]),
            Mount("/static", StaticFiles(directory=STATIC)),
        ]
    )


def run(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"Hivemind live view: http://{host}:{port}/?team={settings.team}")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
