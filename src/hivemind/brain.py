"""The shared memory and coordination layer every agent talks to.

Graph model (on top of the code layer from indexer.py):

    (Developer)-[:RAN]->(Session)-[:WORKS_ON]->(Intent)-[:CLAIMS]->(Function)
    (Developer)-[:OWNS]->(Intent)                       current owner, moves on handoff
    (Session)-[:MADE]->(Attempt)-[:TOUCHED]->(Function)   episodic: what was tried
    (Developer)-[:DECIDED]->(Decision)-[:GOVERNS]->(Function|File)   semantic
    (Developer)-[:SET]->(Convention)-[:APPLIES_TO]->(File)           procedural
    (Session)-[:LOGGED]->(Event)                          activity feed
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from falkordb import Graph

from .db import rows


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _now() -> float:
    return round(time.time(), 3)


class Brain:
    def __init__(self, graph: Graph, dev: str, agent: str = "agent"):
        self.g = graph
        self.dev = dev
        self.agent = agent
        self.session_id: str | None = None

    # ------------------------------------------------------------------ helpers

    def q(self, query: str, **params: Any) -> list[dict]:
        return rows(self.g, query, params)

    def _session(self) -> str:
        """Lazily open a session so every write is attributed to one."""
        if self.session_id is None:
            self.session_id = _id("ses")
            self.q(
                """
                MERGE (d:Developer {name: $dev})
                CREATE (d)-[:RAN]->(:Session {id: $sid, agent: $agent, started: $ts, status: 'open'})
                """,
                dev=self.dev,
                sid=self.session_id,
                agent=self.agent,
                ts=_now(),
            )
        return self.session_id

    def attach_session(self, session_id: str) -> str:
        """Reuse an external session id (e.g. Claude Code's) across processes."""
        self.q(
            """
            MERGE (d:Developer {name: $dev})
            MERGE (s:Session {id: $sid})
            ON CREATE SET s.agent = $agent, s.started = $ts, s.status = 'open'
            MERGE (d)-[:RAN]->(s)
            """,
            dev=self.dev,
            sid=session_id,
            agent=self.agent,
            ts=_now(),
        )
        self.session_id = session_id
        return session_id

    def current_intent(self) -> str | None:
        """This developer's most recent active intent, if any."""
        r = self.q(
            """
            MATCH (i:Intent {status: 'active'}) WHERE i.dev = $dev
            RETURN i.id AS id ORDER BY i.created DESC LIMIT 1
            """,
            dev=self.dev,
        )
        return r[0]["id"] if r else None

    def functions_in_lines(self, path: str, start: int, end: int) -> list[str]:
        return [
            r["qname"]
            for r in self.q(
                """
                MATCH (fn:Function {file: $path})
                WHERE fn.lineno <= $end AND fn.end_lineno >= $start
                RETURN fn.qname AS qname
                """,
                path=path,
                start=start,
                end=end,
            )
        ]

    def _event(self, kind: str, text: str, targets: list[str] | None = None) -> None:
        self.q(
            """
            MATCH (s:Session {id: $sid})
            CREATE (s)-[:LOGGED]->(:Event {ts: $ts, dev: $dev, kind: $kind, text: $text, targets: $targets})
            """,
            sid=self._session(),
            ts=_now(),
            dev=self.dev,
            kind=kind,
            text=text,
            targets=targets or [],
        )

    def resolve(self, targets: list[str]) -> list[str]:
        """Map loose references to function qnames.

        Accepts a full qname ("app/auth.py::login"), a dotted name inside a
        file ("Session.refresh"), a bare function name ("login") or a file
        path ("app/auth.py", meaning every function in it).
        """
        found: list[str] = []
        for t in targets:
            hits = self.q(
                """
                MATCH (fn:Function)
                WHERE fn.qname = $t OR fn.file = $t OR fn.name = $t
                   OR fn.qname ENDS WITH $suffix OR fn.file ENDS WITH $file_suffix
                RETURN fn.qname AS qname
                """,
                t=t,
                suffix=f"::{t}",
                file_suffix=f"/{t}",
            )
            for h in hits:
                if h["qname"] not in found:
                    found.append(h["qname"])
        return found

    def _match_files(self, targets: list[str]) -> list[dict]:
        return self.q(
            """
            MATCH (f:File)
            WHERE f.path IN $t OR any(x IN $suffixes WHERE f.path ENDS WITH x)
            RETURN f.path AS path
            """,
            t=targets,
            suffixes=[f"/{t}" for t in targets],
        )

    def _files_for(self, targets: list[str]) -> list[str]:
        files = [r["path"] for r in self._match_files(targets)]
        for qn in self.resolve(targets):
            path = qn.split("::", 1)[0]
            if path not in files:
                files.append(path)
        return files

    # ------------------------------------------------------------ coordination

    def start_task(self, intent: str, targets: list[str] | None = None, hops: int = 3) -> dict:
        """Declare what this agent is about to do, claim the code it will touch,
        and get back everything the team's agents already know about it."""
        sid = self._session()
        iid = _id("int")
        self.q(
            """
            MATCH (d:Developer {name: $dev}), (s:Session {id: $sid})
            CREATE (i:Intent {id: $iid, description: $desc, status: 'active', dev: $dev, created: $ts})
            CREATE (d)-[:OWNS]->(i)
            CREATE (s)-[:WORKS_ON]->(i)
            """,
            dev=self.dev,
            sid=sid,
            iid=iid,
            desc=intent,
            ts=_now(),
        )
        self._event("start", intent)
        result: dict[str, Any] = {"intent_id": iid, "session_id": sid}
        if targets:
            result.update(self.claim(iid, targets, hops=hops))
            result["context"] = self.context(targets)
        return result

    def claim(self, intent_id: str, targets: list[str], hops: int = 3) -> dict:
        qnames = self.resolve(targets)
        conflicts = self.conflicts(qnames, hops=hops)
        self.q(
            """
            MATCH (i:Intent {id: $iid})
            UNWIND $q AS qn
            MATCH (fn:Function {qname: qn})
            MERGE (i)-[c:CLAIMS]->(fn)
            ON CREATE SET c.ts = $ts
            """,
            iid=intent_id,
            q=qnames,
            ts=_now(),
        )
        self._event("claim", f"claimed {len(qnames)} function(s)", qnames)
        return {"claimed": qnames, "unresolved": [t for t in targets if not self.resolve([t])], "conflicts": conflicts}

    # Each direction is a different kind of risk, so they are searched
    # separately rather than with one undirected traversal (which would also
    # flag unrelated functions that merely share a caller).
    _DIRECTIONS = {
        "same": ("(t)", "same function"),
        "depends_on_theirs": ("(t)-[:CALLS*1..{h}]->", "your code calls code they are changing"),
        "theirs_depends_on_yours": ("(t)<-[:CALLS*1..{h}]-", "their code calls code you are changing"),
    }

    def conflicts(self, targets: list[str], hops: int = 3) -> list[dict]:
        """Find other developers' active claims linked to these functions
        through the call graph (up to `hops` calls away, in one direction).
        Returns the shortest path for each pair so the agent can explain it."""
        qnames = self.resolve(targets)
        hops = max(1, min(int(hops), 6))
        best: dict[tuple[str, str, str], dict] = {}
        for relation, (pattern, meaning) in self._DIRECTIONS.items():
            if relation == "same":
                match = "MATCH p = (t)<-[:CLAIMS]-(i:Intent {status: 'active'})"
                other = "t.qname"
            else:
                match = f"MATCH p = {pattern.format(h=hops)}(o:Function)<-[:CLAIMS]-(i:Intent {{status: 'active'}})"
                other = "o.qname"
            for r in self.q(
                f"""
                MATCH (t:Function) WHERE t.qname IN $q
                {match}
                WHERE i.dev <> $dev
                RETURN t.qname AS target, {other} AS other_function, i.id AS intent_id,
                       i.dev AS other_dev, i.description AS other_intent,
                       [n IN nodes(p) | n.qname] AS path
                """,
                q=qnames,
                dev=self.dev,
            ):
                # nodes(p) ends with the Intent; keep only the code path.
                r["path"] = [n for n in r["path"] if n]
                r["distance"] = len(r["path"]) - 1
                r["relation"] = relation
                r["meaning"] = meaning
                r["severity"] = "direct" if r["distance"] <= 1 else "nearby"
                key = (r["target"], r["other_function"], r["intent_id"])
                if key not in best or r["distance"] < best[key]["distance"]:
                    best[key] = r
        return sorted(best.values(), key=lambda r: r["distance"])

    def release(self, intent_id: str, outcome: str = "done", summary: str = "") -> dict:
        self.q(
            "MATCH (i:Intent {id: $iid}) SET i.status = $status, i.summary = $summary, i.closed = $ts",
            iid=intent_id,
            status=outcome,
            summary=summary,
            ts=_now(),
        )
        self._event("release", f"{outcome}: {summary}" if summary else outcome)
        return {"intent_id": intent_id, "status": outcome}

    def handoff(self, intent_id: str, note: str, to_dev: str | None = None) -> dict:
        """Pause a task so anyone (or a named teammate) can pick it up."""
        self.q(
            """
            MATCH (i:Intent {id: $iid})
            SET i.status = 'handed_off', i.handoff_note = $note, i.handoff_to = $to,
                i.handoff_from = $dev, i.handoff_ts = $ts
            """,
            iid=intent_id,
            note=note,
            to=to_dev or "",
            dev=self.dev,
            ts=_now(),
        )
        self._event("handoff", note)
        return {"intent_id": intent_id, "status": "handed_off", "to": to_dev or "anyone"}

    def resume(self, intent_id: str) -> dict:
        """Take over an intent: ownership moves to this developer, claims stay,
        and the full history of the task comes back as context."""
        sid = self._session()
        self.q(
            """
            MATCH (i:Intent {id: $iid})
            OPTIONAL MATCH (:Developer)-[o:OWNS]->(i)
            DELETE o
            WITH DISTINCT i
            MATCH (d:Developer {name: $dev}), (s:Session {id: $sid})
            CREATE (d)-[:OWNS]->(i)
            CREATE (s)-[:WORKS_ON]->(i)
            SET i.status = 'active', i.dev = $dev
            """,
            iid=intent_id,
            dev=self.dev,
            sid=sid,
        )
        self._event("resume", f"resumed {intent_id}")
        return self.task_history(intent_id)

    def task_history(self, intent_id: str) -> dict:
        intent = self.q(
            """
            MATCH (i:Intent {id: $iid})
            OPTIONAL MATCH (i)-[:CLAIMS]->(fn:Function)
            RETURN i.description AS description, i.status AS status, i.dev AS owner,
                   i.handoff_note AS handoff_note, i.handoff_from AS handoff_from,
                   collect(fn.qname) AS claimed
            """,
            iid=intent_id,
        )
        sessions = self.q(
            """
            MATCH (d:Developer)-[:RAN]->(s:Session)-[:WORKS_ON]->(:Intent {id: $iid})
            OPTIONAL MATCH (s)-[:MADE]->(a:Attempt)
            RETURN d.name AS dev, s.id AS session, s.agent AS agent, s.started AS started,
                   collect({summary: a.summary, outcome: a.outcome, reason: a.reason}) AS attempts
            ORDER BY started
            """,
            iid=intent_id,
        )
        for s in sessions:
            s["attempts"] = [a for a in s["attempts"] if a.get("summary")]
        info = intent[0] if intent else {}
        claimed = info.get("claimed", [])
        return {"intent_id": intent_id, **info, "sessions": sessions, "context": self.context(claimed) if claimed else {}}

    def open_tasks(self) -> list[dict]:
        return self.q(
            """
            MATCH (i:Intent) WHERE i.status IN ['active', 'handed_off']
            OPTIONAL MATCH (i)-[:CLAIMS]->(fn:Function)
            RETURN i.id AS intent_id, i.dev AS dev, i.description AS description, i.status AS status,
                   i.handoff_note AS handoff_note, i.handoff_to AS handoff_to, i.created AS created,
                   collect(fn.qname) AS claimed
            ORDER BY created
            """
        )

    # ------------------------------------------------------------------ memory

    def record_attempt(
        self,
        targets: list[str],
        summary: str,
        outcome: str,
        reason: str = "",
        intent_id: str | None = None,
    ) -> dict:
        """Episodic memory: what was tried on this code and how it went.
        outcome is 'failed', 'succeeded' or 'abandoned'."""
        qnames = self.resolve(targets)
        aid = _id("att")
        self.q(
            """
            MATCH (s:Session {id: $sid})
            CREATE (s)-[:MADE]->(a:Attempt {id: $aid, summary: $summary, outcome: $outcome,
                                             reason: $reason, dev: $dev, ts: $ts})
            WITH a
            UNWIND $q AS qn
            MATCH (fn:Function {qname: qn})
            CREATE (a)-[:TOUCHED]->(fn)
            """,
            sid=self._session(),
            aid=aid,
            summary=summary,
            outcome=outcome,
            reason=reason,
            dev=self.dev,
            ts=_now(),
            q=qnames,
        )
        if intent_id:
            self.q(
                "MATCH (i:Intent {id: $iid}), (a:Attempt {id: $aid}) CREATE (a)-[:PART_OF]->(i)",
                iid=intent_id,
                aid=aid,
            )
        self._event("attempt", f"[{outcome}] {summary}", qnames)
        return {"attempt_id": aid, "touched": qnames}

    def record_decision(self, title: str, rationale: str, targets: list[str] | None = None, source: str = "") -> dict:
        """Semantic memory: a team decision and the code it governs."""
        did = _id("dec")
        qnames = self.resolve(targets or [])
        files = self._match_files(targets or [])
        self.q(
            """
            MERGE (d:Developer {name: $dev})
            CREATE (d)-[:DECIDED]->(x:Decision {id: $did, title: $title, rationale: $rationale,
                                                 source: $source, dev: $dev, ts: $ts})
            WITH x
            OPTIONAL MATCH (fn:Function) WHERE fn.qname IN $q
            FOREACH (_ IN CASE WHEN fn IS NULL THEN [] ELSE [1] END | CREATE (x)-[:GOVERNS]->(fn))
            WITH DISTINCT x
            OPTIONAL MATCH (f:File) WHERE f.path IN $files
            FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | CREATE (x)-[:GOVERNS]->(f))
            """,
            dev=self.dev,
            did=did,
            title=title,
            rationale=rationale,
            source=source,
            ts=_now(),
            q=qnames,
            files=[f["path"] for f in files],
        )
        self.q("MATCH (s:Session {id: $sid}), (x:Decision {id: $did}) CREATE (s)-[:PRODUCED]->(x)", sid=self._session(), did=did)
        self._event("decision", title, qnames)
        return {"decision_id": did, "governs": qnames + [f["path"] for f in files]}

    def add_convention(self, rule: str, files: list[str] | None = None) -> dict:
        """Procedural memory: how we do things here. No files means repo-wide."""
        cid = _id("cnv")
        self.q(
            """
            MERGE (d:Developer {name: $dev})
            CREATE (d)-[:SET]->(c:Convention {id: $cid, rule: $rule, scope: $scope, dev: $dev, ts: $ts})
            WITH c
            UNWIND $files AS p
            MATCH (f:File {path: p})
            CREATE (c)-[:APPLIES_TO]->(f)
            """,
            dev=self.dev,
            cid=cid,
            rule=rule,
            scope="files" if files else "repo",
            files=files or [],
            ts=_now(),
        )
        self._event("convention", rule)
        return {"convention_id": cid}

    def context(self, targets: list[str]) -> dict:
        """Everything the team knows that matters before touching this code:
        past attempts on it or its direct neighbours, decisions that govern it,
        conventions for its files, and who else is working nearby."""
        qnames = self.resolve(targets)
        files = self._files_for(targets)
        attempts = self.q(
            """
            MATCH (t:Function) WHERE t.qname IN $q
            MATCH (t)-[:CALLS*0..1]-(fn:Function)<-[:TOUCHED]-(a:Attempt)
            RETURN DISTINCT a.summary AS summary, a.outcome AS outcome, a.reason AS reason,
                   a.dev AS dev, a.ts AS ts, fn.qname AS function,
                   CASE WHEN fn.qname IN $q THEN 'same' ELSE 'neighbour' END AS relation
            ORDER BY ts DESC
            LIMIT 20
            """,
            q=qnames,
        )
        decisions = self.q(
            """
            MATCH (x:Decision)-[:GOVERNS]->(n)
            WHERE (n:Function AND n.qname IN $q) OR (n:File AND n.path IN $files)
            RETURN DISTINCT x.id AS id, x.title AS title, x.rationale AS rationale,
                   x.dev AS dev, x.source AS source, x.ts AS ts
            ORDER BY ts DESC
            """,
            q=qnames,
            files=files,
        )
        conventions = self.q(
            """
            MATCH (c:Convention)
            OPTIONAL MATCH (c)-[:APPLIES_TO]->(f:File)
            WITH c, collect(f.path) AS paths
            WHERE c.scope = 'repo' OR any(p IN paths WHERE p IN $files)
            RETURN c.rule AS rule, c.dev AS dev, c.scope AS scope
            """,
            files=files,
        )
        return {
            "functions": qnames,
            "attempts": attempts,
            "decisions": decisions,
            "conventions": conventions,
            "others_nearby": self.conflicts(qnames, hops=2),
        }

    # ------------------------------------------------------------ explanation

    def why(self, target: str) -> dict:
        """Trace a piece of code back to the decisions, attempts and people
        that shaped it."""
        ctx = self.context([target])
        owners = self.q(
            """
            MATCH (fn:Function) WHERE fn.qname IN $q
            MATCH (d:Developer)-[:RAN]->(:Session)-[:MADE]->(:Attempt)-[:TOUCHED]->(fn)
            RETURN d.name AS dev, count(*) AS attempts ORDER BY attempts DESC
            """,
            q=ctx["functions"],
        )
        return {
            "target": target,
            "functions": ctx["functions"],
            "decisions": ctx["decisions"],
            "history": [a for a in ctx["attempts"] if a["relation"] == "same"],
            "people": owners,
        }

    def blast_radius(self, target: str, hops: int = 3) -> list[dict]:
        """What depends on this code: transitive callers up to `hops` away."""
        hops = max(1, min(int(hops), 6))
        return self.q(
            f"""
            MATCH (t:Function) WHERE t.qname IN $q
            MATCH p = (caller:Function)-[:CALLS*1..{hops}]->(t)
            RETURN caller.qname AS caller, min(length(p)) AS distance
            ORDER BY distance, caller
            """,
            q=self.resolve([target]),
        )

    def experts(self, target: str) -> list[dict]:
        """Who has worked on this code or the code around it."""
        q = self.resolve([target])
        scores: dict[str, dict] = {}
        for kind, query in (
            ("attempts", "MATCH (a:Attempt)-[:TOUCHED]->(fn) RETURN a.dev AS dev, count(*) AS n"),
            ("claims", "MATCH (i:Intent)-[:CLAIMS]->(fn) RETURN i.dev AS dev, count(*) AS n"),
            ("decisions", "MATCH (x:Decision)-[:GOVERNS]->(fn) RETURN x.dev AS dev, count(*) AS n"),
        ):
            for r in self.q(
                f"""
                MATCH (t:Function) WHERE t.qname IN $q
                MATCH (t)-[:CALLS*0..1]-(fn:Function)
                WITH DISTINCT fn
                {query}
                """,
                q=q,
            ):
                s = scores.setdefault(r["dev"], {"dev": r["dev"], "attempts": 0, "claims": 0, "decisions": 0})
                s[kind] += r["n"]
        return sorted(scores.values(), key=lambda s: -(s["attempts"] + s["claims"] + 2 * s["decisions"]))

    def recent_events(self, limit: int = 30) -> list[dict]:
        return self.q(
            """
            MATCH (e:Event)
            RETURN e.ts AS ts, e.dev AS dev, e.kind AS kind, e.text AS text, e.targets AS targets
            ORDER BY e.ts DESC LIMIT $limit
            """,
            limit=limit,
        )
