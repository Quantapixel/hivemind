"""Hook entry point for Claude Code, Codex and Kiro: `hivemind hook --agent X`.

Every agent action that matters becomes shared memory without the agent
having to call a tool:

  session start   remember the session; give the agent a short team brief
  user prompt     the request names this session's task
  before an edit  claim the functions being edited; if a teammate is on
                  connected code, block the edit once and explain why
  after an edit   re-index the file, record exactly which functions changed
  after tests     changed code + failing tests = a failed attempt (merged with
                  identical earlier failures); passing tests resolve them
  agent stops     its own summary becomes the task's progress note
  session end     close the tasks that were opened automatically

A hook must never break the agent, so all errors are swallowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .brain import Brain
from .capture import HookEvent, is_test_command, normalize, parse_failure
from .db import connect
from .indexer import index_file
from .project import find_root, load_settings, session_file


def _rel(root: Path, path: str) -> str | None:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _line_ranges(path: Path, old_text: str | None) -> list[tuple[int, int]]:
    if not old_text or not path.exists():
        return [(1, 10**9)]
    text = path.read_text(encoding="utf-8", errors="replace")
    idx = text.find(old_text)
    if idx < 0:
        return [(1, 10**9)]
    start = text.count("\n", 0, idx) + 1
    return [(start, start + old_text.count("\n"))]


def _ack_key(c: dict) -> str:
    return f"{c['intent_id']}|{c['target']}|{c['other_function']}"


def team_brief(brain: Brain) -> str:
    """A few lines of team context for the start of a session."""
    others = [t for t in brain.open_tasks() if t["dev"] != brain.dev]
    failures = brain.q(
        """
        MATCH (a:Attempt {outcome: 'failed'})-[:TOUCHED]->(fn:Function)
        WHERE coalesce(a.resolved, false) = false
        RETURN a.summary AS summary, a.reason AS reason, coalesce(a.count, 1) AS n,
               collect(DISTINCT fn.name)[..3] AS fns, max(a.last_ts) AS ts
        ORDER BY ts DESC LIMIT 4
        """
    )
    if not others and not failures:
        return ""
    lines = ["Hivemind (shared memory for this repo):"]
    for t in others[:5]:
        where = ", ".join(c.split("::")[-1] for c in t["claimed"][:4])
        status = "handed off, waiting for someone" if t["status"] == "handed_off" else "working on"
        lines.append(f"- {t['dev']} is {status}: \"{t['description']}\"" + (f" ({where})" if where else ""))
    for f in failures:
        times = f" ×{f['n']}" if f["n"] > 1 else ""
        lines.append(f"- Unresolved failure on {', '.join(f['fns'])}{times}: {f['summary']} - {f['reason']}")
    lines.append("Check `get_context` from the hivemind tools before changing that code.")
    return "\n".join(lines)


def on_pre_edit(brain: Brain, ev: HookEvent, root: Path) -> str | None:
    """Claim what is about to change; return a reason to block, or None."""
    exact: list[str] = []      # functions whose text is being replaced: claim now
    whole: list[str] = []      # whole-file rewrites: only check, claim after the diff
    for e in ev.edits:
        rel = _rel(root, e.path)
        if not rel:
            continue
        for start, end in _line_ranges(Path(e.path), e.old_text):
            for q in brain.functions_in_lines(rel, start, end):
                bucket = exact if e.old_text and end < 10**9 else whole
                if q not in bucket:
                    bucket.append(q)
    if not exact and not whole:
        return None
    conflicts = brain.conflicts(whole) if whole else []
    if exact:
        intent = brain.ensure_intent(f"Editing {_rel(root, ev.edits[0].path)}")
        conflicts += brain.claim(intent, exact, quiet=True)["conflicts"]
    qnames = exact + whole
    acked = {r["key"] for r in brain.q("MATCH (a:Ack) WHERE a.dev = $dev RETURN a.key AS key", dev=brain.dev)}
    fresh = [c for c in conflicts if _ack_key(c) not in acked]
    if not fresh:
        return None
    brain.q("UNWIND $keys AS k MERGE (:Ack {key: k, dev: $dev})", keys=[_ack_key(c) for c in fresh], dev=brain.dev)
    lines = ["Hivemind: a teammate is working on code connected to this edit."]
    for c in fresh:
        path = " -> ".join(p.split("::")[-1] for p in c["path"])
        lines.append(f"- {c['other_dev']} is on \"{c['other_intent']}\" ({c['other_function']}).")
        lines.append(f"  Path: {path}. {c['meaning'].capitalize()}.")
    for a in brain.context(qnames + [c["other_function"] for c in fresh])["attempts"]:
        if a["outcome"] == "failed" and not a["resolved"]:
            lines.append(f"- Past failed attempt by {a['dev']} on {a['function']}: {a['summary']} ({a['reason']}).")
    lines.append(
        "Tell the user about the overlap before continuing. If it is safe, retry the same edit "
        "and it will go through; use the hivemind tools for full context."
    )
    return "\n".join(lines)


def on_post_edit(brain: Brain, ev: HookEvent, root: Path) -> None:
    for e in ev.edits:
        rel = _rel(root, e.path)
        if rel and rel.endswith(".py"):
            brain.record_change(rel, index_file(brain.g, root, rel), ev.tool)


def on_post_shell(brain: Brain, ev: HookEvent) -> None:
    if ev.exit_code is None or not is_test_command(ev.command):
        return
    passed = ev.exit_code == 0
    summary, signature = ("", "") if passed else parse_failure(ev.output)
    brain.record_test_run(ev.command, passed, summary, signature)


def handle(ev: HookEvent, brain: Brain, root: Path) -> tuple[str, str, int]:
    """Process one event. Returns (stdout, stderr, exit code) for the agent."""
    if ev.kind == "session_start":
        session_file(root, ev.agent).parent.mkdir(exist_ok=True)
        session_file(root, ev.agent).write_text(brain.session_id or "")
        return team_brief(brain), "", 0
    if ev.kind == "prompt" and ev.prompt:
        brain.note_prompt(ev.prompt)
    elif ev.kind == "pre_tool" and ev.action == "edit":
        reason = on_pre_edit(brain, ev, root)
        if reason:
            if ev.agent == "kiro":  # Kiro blocks a tool when the hook exits with 2
                return "", reason, 2
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
            return json.dumps(out), "", 0
    elif ev.kind == "post_tool" and ev.action == "edit":
        on_post_edit(brain, ev, root)
    elif ev.kind == "post_tool" and ev.action == "shell":
        on_post_shell(brain, ev)
    elif ev.kind == "stop" and ev.message:
        brain.set_progress(ev.message)
    elif ev.kind == "session_end":
        brain.q(
            """
            MATCH (i:Intent {status: 'active'}) WHERE i.auto = true AND i.session = $sid
            SET i.status = 'done', i.closed = timestamp() / 1000.0
            """,
            sid=brain.session_id,
        )
    return "", "", 0


def main(agent: str = "claude") -> None:
    code = 0
    try:
        raw = json.load(sys.stdin)
        ev = normalize(raw, agent)
        root = find_root(ev.cwd)
        if root is None:
            sys.exit(0)  # not a Hivemind project
        settings = load_settings(root)
        brain = Brain(connect(settings), settings.dev, agent)
        if ev.session_id:
            brain.attach_session(f"{agent}-{ev.session_id}")
        out, err, code = handle(ev, brain, root)
        if out:
            print(out)
        if err:
            print(err, file=sys.stderr)
    except Exception as exc:  # never break the user's agent over a hook
        print(f"hivemind hook: {exc}", file=sys.stderr)
        code = 0
    sys.exit(code)
