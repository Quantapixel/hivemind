"""Claude Code hook: enforce the protocol even when the agent forgets it.

PreToolUse on Edit / Write / MultiEdit:
  * works out which functions the edit touches (by line range),
  * auto-claims them under the developer's current task (or a new one),
  * if that code conflicts with a teammate's work, denies the edit once
    with the conflict path as the reason. The agent sees why, tells the
    user, and a retry goes through: a warning, not a lock.

SessionStart: records Claude Code's session id in .claude/hivemind-session
so the MCP server attributes its writes to the same session.

SessionEnd: closes the tasks the hook opened automatically.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .brain import Brain
from .db import Settings, connect

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
SESSION_FILE = Path(".claude") / "hivemind-session"


def session_id_for(claude_session_id: str) -> str:
    return f"cc-{claude_session_id}"


def read_session_hint(root: Path) -> str | None:
    try:
        return (root / SESSION_FILE).read_text().strip() or None
    except OSError:
        return None


def _line_ranges(file_path: Path, tool: str, tool_input: dict) -> list[tuple[int, int]]:
    """Line ranges in the current file that this edit replaces."""
    if tool == "Write" or not file_path.exists():
        return [(1, 10**9)]
    text = file_path.read_text(encoding="utf-8", errors="replace")
    olds = [e.get("old_string", "") for e in tool_input.get("edits", [])] if tool == "MultiEdit" else [
        tool_input.get("old_string", "")
    ]
    ranges = []
    for old in olds:
        idx = text.find(old) if old else -1
        if idx < 0:
            return [(1, 10**9)]
        start = text.count("\n", 0, idx) + 1
        ranges.append((start, start + old.count("\n")))
    return ranges


def _ack_key(c: dict) -> str:
    return f"{c['intent_id']}|{c['target']}|{c['other_function']}"


def pre_tool_use(brain: Brain, event: dict, root: Path) -> dict | None:
    tool = event.get("tool_name")
    if tool not in EDIT_TOOLS:
        return None
    tool_input = event.get("tool_input", {})
    file_path = Path(tool_input.get("file_path", ""))
    if not file_path.is_absolute():
        file_path = Path(event.get("cwd", ".")) / file_path
    try:
        rel = file_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None  # outside the indexed repo

    qnames: list[str] = []
    for start, end in _line_ranges(file_path, tool, tool_input):
        for q in brain.functions_in_lines(rel, start, end):
            if q not in qnames:
                qnames.append(q)
    if not qnames:
        return None

    intent = brain.current_intent()
    if intent is None:
        intent = brain.start_task(f"Editing {rel}")["intent_id"]
        brain.q("MATCH (i:Intent {id: $iid}) SET i.auto = true, i.session = $sid", iid=intent, sid=brain.session_id)
    conflicts = brain.claim(intent, qnames)["conflicts"]

    acked = {
        r["key"]
        for r in brain.q("MATCH (a:Ack) WHERE a.dev = $dev RETURN a.key AS key", dev=brain.dev)
    }
    fresh = [c for c in conflicts if _ack_key(c) not in acked]
    if not fresh:
        return None
    brain.q(
        "UNWIND $keys AS k MERGE (:Ack {key: k, dev: $dev})",
        keys=[_ack_key(c) for c in fresh],
        dev=brain.dev,
    )

    lines = ["Hivemind: a teammate is working on code connected to this edit."]
    for c in fresh:
        path = " -> ".join(p.split("::")[-1] for p in c["path"])
        lines.append(f"- {c['other_dev']} is on \"{c['other_intent']}\" ({c['other_function']}).")
        lines.append(f"  Path: {path}. {c['meaning'].capitalize()}.")
    ctx = brain.context(qnames + [c["other_function"] for c in fresh])
    for a in ctx["attempts"]:
        if a["outcome"] == "failed":
            lines.append(f"- Past failed attempt by {a['dev']} on {a['function']}: {a['summary']} ({a['reason']}).")
    lines.append(
        "Tell the user about the overlap before continuing. If it is safe, retry the same edit "
        "and it will go through; use the hivemind tools for full context."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "\n".join(lines),
        }
    }


def session_end(brain: Brain, event: dict) -> None:
    brain.q(
        """
        MATCH (i:Intent {status: 'active'}) WHERE i.auto = true AND i.session = $sid
        SET i.status = 'done', i.closed = timestamp() / 1000.0
        """,
        sid=brain.session_id,
    )


def main() -> None:
    try:
        event = json.load(sys.stdin)
        settings = Settings.from_env()
        brain = Brain(connect(settings), settings.dev, "claude-code")
        if event.get("session_id"):
            brain.attach_session(session_id_for(event["session_id"]))
        root = Path(os.environ.get("HIVEMIND_ROOT") or event.get("cwd") or ".")
        name = event.get("hook_event_name")
        if name == "SessionStart" and brain.session_id:
            (root / SESSION_FILE).parent.mkdir(exist_ok=True)
            (root / SESSION_FILE).write_text(brain.session_id)
        elif name == "PreToolUse":
            out = pre_tool_use(brain, event, root)
            if out:
                print(json.dumps(out))
        elif name == "SessionEnd":
            session_end(brain, event)
    except Exception as exc:  # never break the user's editor over a hook
        print(f"hivemind hook: {exc}", file=sys.stderr)
    sys.exit(0)
