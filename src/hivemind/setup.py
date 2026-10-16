"""`hivemind init`: connect a repo's Claude Code to the team brain."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from .db import Settings

CLAUDE_MD_MARKER = "<!-- hivemind -->"
CLAUDE_MD = f"""
{CLAUDE_MD_MARKER}
## Hivemind (shared team memory)

Other developers' agents are working in this repo at the same time. The
`hivemind` MCP tools are how you coordinate with them:

- Call `start_task` before editing, with the functions or files you will touch.
  Read the conflicts and past attempts it returns before you write code.
- Call `record_attempt` when an approach fails (or works in a non-obvious way),
  `record_decision` for design choices, `add_convention` for repo rules.
- Call `handoff` if you stop mid-task, `finish_task` when done.
- Ask `why`, `who_knows` or `blast_radius` before changing unfamiliar code.

Edits that touch a teammate's in-progress code are blocked once with an
explanation. Tell the user about the overlap; retrying the edit lets it through.
"""


def _merge_json(path: Path, update) -> None:
    data = json.loads(path.read_text()) if path.exists() else {}
    update(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def init(repo: Path, settings: Settings) -> list[str]:
    repo = repo.resolve()
    env = {
        "HIVEMIND_TEAM": settings.team,
        "HIVEMIND_ROOT": str(repo),
        "FALKORDB_HOST": settings.host,
        "FALKORDB_PORT": str(settings.port),
    }
    python = sys.executable
    done: list[str] = []

    def add_mcp(data: dict) -> None:
        data.setdefault("mcpServers", {})["hivemind"] = {
            "command": python,
            "args": ["-m", "hivemind.cli", "serve", "--agent", "claude-code"],
            # HIVEMIND_DEV is read from each developer's own environment ($USER by default).
            "env": env,
        }

    _merge_json(repo / ".mcp.json", add_mcp)
    done.append(".mcp.json: hivemind MCP server")

    hook_cmd = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + f" {shlex.quote(python)} -m hivemind.cli hook"

    def add_hooks(data: dict) -> None:
        hooks = data.setdefault("hooks", {})
        for event, entry in (
            ("PreToolUse", {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": hook_cmd}]}),
            ("SessionEnd", {"hooks": [{"type": "command", "command": hook_cmd}]}),
        ):
            existing = [
                e for e in hooks.get(event, []) if not any("hivemind.cli hook" in h.get("command", "") for h in e.get("hooks", []))
            ]
            hooks[event] = [*existing, entry]

    # Local settings: the hook command embeds this machine's python path.
    _merge_json(repo / ".claude" / "settings.local.json", add_hooks)
    done.append(".claude/settings.local.json: PreToolUse + SessionEnd hooks")

    claude_md = repo / "CLAUDE.md"
    text = claude_md.read_text() if claude_md.exists() else ""
    if CLAUDE_MD_MARKER not in text:
        claude_md.write_text(text.rstrip() + "\n" + CLAUDE_MD if text else CLAUDE_MD.lstrip())
        done.append("CLAUDE.md: coordination protocol")
    return done
