"""The shared memory and coordination layer every agent talks to.

Graph model (on top of the code layer from indexer.py):

    (Developer)-[:RAN]->(Session)-[:WORKS_ON]->(Intent)-[:CLAIMS]->(Function)
    (Developer)-[:OWNS]->(Intent)                       current owner, moves on handoff
    (Session)-[:MADE]->(Attempt)-[:TOUCHED]->(Function)   episodic: what was tried
    (Developer)-[:DECIDED]->(Decision)-[:GOVERNS]->(Function|File)   semantic
    (Developer)-[:SET]->(Convention)-[:APPLIES_TO]->(File)           procedural
    (Session)-[:LOGGED]->(Event)                          activity feed
    (Session)-[:MADE]->(Change)-[:CHANGED]->(Function)    captured edits
    (Attempt)-[:RESOLVES]->(Attempt)                      a fix for an earlier failure
    (Decision)-[:SUPERSEDES]->(Decision)                  newer decision replaces older

Memory is consolidated on write: an attempt that repeats an earlier one is
merged into it (count, who saw it), a success on code with open failures
resolves them, and a decision that restates an older one on the same code
supersedes it.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable

from falkordb import Graph

from .db import rows


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _now() -> float:
    return round(time.time(), 3)


_STOP = set("the a an and or to of in on for with is was it this that be by as at from not no into".split())


def _tokens(*texts: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", " ".join(t or "" for t in texts).lower())
    return {w for w in words if len(w) > 1 and w not in _STOP}


def similarity(a: str, b: str) -> float:
    """Word-overlap (Jaccard) similarity, used to spot repeated memories."""
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


class Brain:
    def __init__(self, graph: Graph, dev: str, agent: str = "agent"):
        self.g = graph
        self.dev = dev
        self.agent = agent
        self.session_id: str | None = None
        # Optional source of an external session id (e.g. Claude Code's),
        # consulted when the first write needs a session.
        self.session_hint: Callable[[], str | None] | None = None

    # ------------------------------------------------------------------ helpers

    def q(self, query: str, **params: Any) -> list[dict]:
        return rows(self.g, query, params)

    def _session(self) -> str:
        """Lazily open a session so every write is attributed to one."""
        if self.session_id is None and self.session_hint:
            hinted = self.session_hint()
            if hinted:
                return self.attach_session(hinted)
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

    def session_intent(self) -> str | None:
        """The active task this session is working on, if any."""
        if not self.session_id:
            return None
        r = self.q(
            """
            MATCH (:Session {id: $sid})-[:WORKS_ON]->(i:Intent {status: 'active'})
            RETURN i.id AS id ORDER BY i.created DESC LIMIT 1
            """,
            sid=self.session_id,
        )
        return r[0]["id"] if r else None

    def ensure_intent(self, description: str) -> str:
        """The session's task, creating an automatic one named after what the
        user asked for if the agent never declared one."""
        iid = self.session_intent()
        if iid:
            return iid
        iid = self.start_task(description)["intent_id"]
        self.q("MATCH (i:Intent {id: $iid}) SET i.auto = true, i.session = $sid", iid=iid, sid=self._session())
        return iid

    def note_prompt(self, prompt: str) -> str:
        """Record what the user asked this session's agent to do."""
        prompt = " ".join(prompt.split())
        title = prompt if len(prompt) <= 90 else prompt[:89] + "…"
        iid = self.session_intent()
        if iid is None:
            return self.ensure_intent(title)
        # An automatic task named after a file edit gets the real request as its name.
        self.q(
            """
            MATCH (i:Intent {id: $iid})
            SET i.prompts = coalesce(i.prompts, [])[-4..] + [$p]
            WITH i WHERE i.auto = true AND i.description STARTS WITH 'Editing '
            SET i.description = $title
            """,
            iid=iid,
            p=prompt[:500],
            title=title,
        )
        return iid

    def set_progress(self, message: str) -> None:
        """Keep the agent's latest summary of its work on the session and task,
        so a handoff always has an up-to-date note even if nobody wrote one."""
        message = message.strip()
        if not message:
            return
        sid = self._session()
        self.q("MATCH (s:Session {id: $sid}) SET s.progress = $m, s.progress_ts = $ts", sid=sid, m=message[:2000], ts=_now())
        iid = self.session_intent()
        if iid:
            self.q("MATCH (i:Intent {id: $iid}) SET i.progress = $m, i.progress_ts = $ts", iid=iid, m=message[:2000], ts=_now())

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

    # ---------------------------------------------------------------- capture

    def record_change(self, path: str, diff: dict[str, list[str]], tool: str = "") -> dict:
        """An edit happened (seen by a hook or the watcher). Attribute the
        changed functions to this session, claim them, and queue them for the
        next test run."""
        touched = diff.get("added", []) + diff.get("changed", [])
        if not touched and not diff.get("removed"):
            return {"changed": [], "conflicts": []}
        sid = self._session()
        iid = self.ensure_intent(f"Editing {path}")
        conflicts = self.claim(iid, touched)["conflicts"] if touched else []
        cid = _id("chg")
        self.q(
            """
            MATCH (s:Session {id: $sid})
            CREATE (s)-[:MADE]->(c:Change {id: $cid, file: $file, dev: $dev, agent: $agent, tool: $tool, ts: $ts,
                                           added: $added, changed: $changed, removed: $removed})
            SET s.pending = [x IN coalesce(s.pending, []) WHERE NOT x IN $touched] + $touched
            WITH c
            UNWIND $touched AS qn
            MATCH (fn:Function {qname: qn})
            CREATE (c)-[:CHANGED]->(fn)
            """,
            sid=sid,
            cid=cid,
            file=path,
            dev=self.dev,
            agent=self.agent,
            tool=tool,
            ts=_now(),
            added=diff.get("added", []),
            changed=diff.get("changed", []),
            removed=diff.get("removed", []),
            touched=touched,
        )
        names = [q.split("::")[-1] for q in touched]
        parts = [f"edited {', '.join(names[:4])}" + (f" +{len(names) - 4}" if len(names) > 4 else "")] if names else []
        if diff.get("removed"):
            parts.append(f"removed {', '.join(q.split('::')[-1] for q in diff['removed'][:3])}")
        self._event("edit", "; ".join(parts) + f" in {path}", touched)
        return {"change_id": cid, "changed": touched, "conflicts": conflicts}

    def pending_changes(self) -> list[str]:
        if not self.session_id:
            return []
        r = self.q("MATCH (s:Session {id: $sid}) RETURN coalesce(s.pending, []) AS p", sid=self.session_id)
        return r[0]["p"] if r else []

    def record_test_run(self, command: str, passed: bool, failure: str = "", signature: str = "") -> dict:
        """A test command finished. If code changed since the last run, that
        is an attempt: a failure is remembered on the changed functions (merged
        with any identical earlier failure); a pass resolves open failures."""
        pending = self.pending_changes()
        self._event("test", f"{'passed' if passed else 'failed'}: {command[:120]}" + (f" ({failure[:160]})" if failure and not passed else ""), pending)
        if not pending:
            return {"recorded": None, "reason": "no code changed since the last test run"}
        self.q("MATCH (s:Session {id: $sid}) SET s.pending = []", sid=self.session_id)
        names = ", ".join(q.split("::")[-1] for q in pending[:4]) + (f" +{len(pending) - 4}" if len(pending) > 4 else "")
        iid = self.session_intent()
        task = ""
        if iid:
            r = self.q("MATCH (i:Intent {id: $iid}) RETURN i.description AS d", iid=iid)
            task = r[0]["d"] if r else ""
        if passed:
            summary = f"Changed {names}; tests pass" + (f" ({task})" if task else "")
            out = self.record_attempt(pending, summary, "succeeded", f"`{command}` passed", iid, auto=True)
            return {"recorded": "succeeded", **out}
        summary = f"Changed {names}; tests fail" + (f" ({task})" if task else "")
        out = self.record_attempt(pending, summary, "failed", failure or f"`{command}` failed", iid, auto=True, signature=signature)
        return {"recorded": "failed", **out}

    # ------------------------------------------------------------------ memory

    def record_attempt(
        self,
        targets: list[str],
        summary: str,
        outcome: str,
        reason: str = "",
        intent_id: str | None = None,
        auto: bool = False,
        signature: str = "",
    ) -> dict:
        """Episodic memory: what was tried on this code and how it went.
        outcome is 'failed', 'succeeded' or 'abandoned'.

        Repeats are merged: if this matches an earlier attempt with the same
        outcome on the same code (same failing tests, or similar wording),
        that attempt's count goes up instead of a duplicate being stored."""
        qnames = self.resolve(targets)
        sid = self._session()
        dup = self._similar_attempt(qnames, outcome, summary, reason, signature)
        if dup:
            self.q(
                """
                MATCH (a:Attempt {id: $aid}), (s:Session {id: $sid})
                SET a.count = coalesce(a.count, 1) + 1, a.last_ts = $ts,
                    a.devs = [d IN coalesce(a.devs, [a.dev]) WHERE d <> $dev] + [$dev]
                MERGE (s)-[:MADE]->(a)
                WITH a
                UNWIND $q AS qn
                MATCH (fn:Function {qname: qn})
                MERGE (a)-[:TOUCHED]->(fn)
                """,
                aid=dup["id"],
                sid=sid,
                dev=self.dev,
                ts=_now(),
                q=qnames,
            )
            self._event("attempt", f"[{outcome}] {summary} (seen {dup['count'] + 1}×)", qnames)
            return {"attempt_id": dup["id"], "touched": qnames, "merged": True, "times": dup["count"] + 1}
        aid = _id("att")
        self.q(
            """
            MATCH (s:Session {id: $sid})
            CREATE (s)-[:MADE]->(a:Attempt {id: $aid, summary: $summary, outcome: $outcome, reason: $reason,
                                             dev: $dev, devs: [$dev], agent: $agent, ts: $ts, last_ts: $ts,
                                             count: 1, auto: $auto, signature: $sig, resolved: false})
            WITH a
            UNWIND $q AS qn
            MATCH (fn:Function {qname: qn})
            CREATE (a)-[:TOUCHED]->(fn)
            """,
            sid=sid,
            aid=aid,
            summary=summary,
            outcome=outcome,
            reason=reason,
            dev=self.dev,
            agent=self.agent,
            ts=_now(),
            auto=auto,
            sig=signature,
            q=qnames,
        )
        if intent_id:
            self.q(
                "MATCH (i:Intent {id: $iid}), (a:Attempt {id: $aid}) CREATE (a)-[:PART_OF]->(i)",
                iid=intent_id,
                aid=aid,
            )
        resolved: list[str] = []
        if outcome == "succeeded" and qnames:
            # A success on code with open failures is the fix for them.
            resolved = [
                r["id"]
                for r in self.q(
                    """
                    MATCH (f:Attempt {outcome: 'failed'})-[:TOUCHED]->(fn:Function)
                    WHERE fn.qname IN $q AND coalesce(f.resolved, false) = false
                    WITH DISTINCT f
                    MATCH (a:Attempt {id: $aid})
                    CREATE (a)-[:RESOLVES]->(f)
                    SET f.resolved = true, f.resolved_by = $aid, f.resolved_dev = $dev, f.resolved_summary = $summary
                    RETURN f.id AS id
                    """,
                    q=qnames,
                    aid=aid,
                    dev=self.dev,
                    summary=summary,
                )
            ]
        self._event("attempt", f"[{outcome}] {summary}" + (f" (resolves {len(resolved)} earlier failure{'s' if len(resolved) > 1 else ''})" if resolved else ""), qnames)
        return {"attempt_id": aid, "touched": qnames, "merged": False, "resolves": resolved}

    def _similar_attempt(self, qnames: list[str], outcome: str, summary: str, reason: str, signature: str) -> dict | None:
        if not qnames:
            return None
        candidates = self.q(
            """
            MATCH (a:Attempt {outcome: $outcome})-[:TOUCHED]->(fn:Function)
            WHERE fn.qname IN $q AND coalesce(a.resolved, false) = false
            RETURN DISTINCT a.id AS id, a.summary AS summary, a.reason AS reason,
                   coalesce(a.signature, '') AS signature, coalesce(a.count, 1) AS count
            """,
            outcome=outcome,
            q=qnames,
        )
        for c in candidates:
            if signature and c["signature"]:
                if signature == c["signature"]:
                    return c
                continue
            if similarity(f"{summary} {reason}", f"{c['summary']} {c['reason']}") >= 0.55:
                return c
        return None

    def record_decision(
        self, title: str, rationale: str, targets: list[str] | None = None, source: str = "", supersedes: str = ""
    ) -> dict:
        """Semantic memory: a team decision and the code it governs.

        If it restates a current decision on the same code (or `supersedes`
        names one), the older decision is marked superseded so agents only
        see what is true now. Other current decisions on that code are
        returned as `related` so the agent can check they still agree."""
        did = _id("dec")
        qnames = self.resolve(targets or [])
        files = [f["path"] for f in self._match_files(targets or [])]
        self.q(
            """
            MERGE (d:Developer {name: $dev})
            CREATE (d)-[:DECIDED]->(x:Decision {id: $did, title: $title, rationale: $rationale,
                                                 source: $source, dev: $dev, ts: $ts, superseded: false})
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
            files=files,
        )
        self.q("MATCH (s:Session {id: $sid}), (x:Decision {id: $did}) CREATE (s)-[:PRODUCED]->(x)", sid=self._session(), did=did)
        code_files = list({*files, *(q.split("::")[0] for q in qnames)})
        current = self.q(
            """
            MATCH (x:Decision)-[:GOVERNS]->(n)
            WHERE x.id <> $did AND coalesce(x.superseded, false) = false
              AND ((n:Function AND (n.qname IN $q OR n.file IN $files)) OR (n:File AND n.path IN $files))
            RETURN DISTINCT x.id AS id, x.title AS title, x.rationale AS rationale, x.dev AS dev
            """,
            did=did,
            q=qnames,
            files=code_files,
        )
        replaced = [c for c in current if c["id"] == supersedes or similarity(f"{title} {rationale}", f"{c['title']} {c['rationale']}") >= 0.5]
        for c in replaced:
            self.q(
                """
                MATCH (new:Decision {id: $did}), (old:Decision {id: $old})
                CREATE (new)-[:SUPERSEDES]->(old)
                SET old.superseded = true, old.superseded_by = $did, old.superseded_title = $title
                """,
                did=did,
                old=c["id"],
                title=title,
            )
        self._event("decision", title + (f" (supersedes: {replaced[0]['title']})" if replaced else ""), qnames)
        return {
            "decision_id": did,
            "governs": qnames + files,
            "supersedes": [c["title"] for c in replaced],
            "related": [c for c in current if c not in replaced],
        }

    def add_convention(self, rule: str, files: list[str] | None = None) -> dict:
        """Procedural memory: how we do things here. No files means repo-wide.
        A rule that restates an existing one is merged into it."""
        for c in self.q("MATCH (c:Convention) RETURN c.id AS id, c.rule AS rule"):
            if similarity(rule, c["rule"]) >= 0.6:
                self.q(
                    """
                    MATCH (c:Convention {id: $cid})
                    SET c.count = coalesce(c.count, 1) + 1, c.devs = [d IN coalesce(c.devs, [c.dev]) WHERE d <> $dev] + [$dev]
                    """,
                    cid=c["id"],
                    dev=self.dev,
                )
                return {"convention_id": c["id"], "merged_into": c["rule"]}
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
                   CASE WHEN fn.qname IN $q THEN 'same' ELSE 'neighbour' END AS relation,
                   coalesce(a.count, 1) AS times, coalesce(a.devs, [a.dev]) AS seen_by,
                   coalesce(a.auto, false) AS auto, coalesce(a.resolved, false) AS resolved,
                   a.resolved_dev AS resolved_by, a.resolved_summary AS fix
            ORDER BY resolved, ts DESC
            LIMIT 20
            """,
            q=qnames,
        )
        decisions = self.q(
            """
            MATCH (x:Decision)-[:GOVERNS]->(n)
            WHERE ((n:Function AND n.qname IN $q) OR (n:File AND n.path IN $files))
              AND coalesce(x.superseded, false) = false
            RETURN DISTINCT x.id AS id, x.title AS title, x.rationale AS rationale,
                   x.dev AS dev, x.source AS source, x.ts AS ts
            ORDER BY ts DESC
            """,
            q=qnames,
            files=files,
        )
        recent_edits = self.q(
            """
            MATCH (c:Change)-[:CHANGED]->(fn:Function) WHERE fn.qname IN $q AND c.dev <> $dev
            RETURN c.dev AS dev, c.agent AS agent, fn.qname AS function, max(c.ts) AS ts
            ORDER BY ts DESC LIMIT 10
            """,
            q=qnames,
            dev=self.dev,
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
            "recent_edits_by_others": recent_edits,
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
        superseded = self.q(
            """
            MATCH (x:Decision {superseded: true})-[:GOVERNS]->(n)
            WHERE (n:Function AND n.qname IN $q) OR (n:File AND n.path IN $files)
            RETURN DISTINCT x.title AS title, x.dev AS dev, x.superseded_title AS replaced_by
            """,
            q=ctx["functions"],
            files=list({q.split("::")[0] for q in ctx["functions"]}),
        )
        return {
            "target": target,
            "functions": ctx["functions"],
            "decisions": ctx["decisions"],
            "superseded_decisions": superseded,
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
