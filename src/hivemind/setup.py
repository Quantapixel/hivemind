"""`hivemind init`: connect a repo's coding agents to the team brain.

Supported agents and what gets installed:

  claude  .mcp.json (MCP server), .claude/settings.local.json (hooks), CLAUDE.md
  codex   `codex mcp add hivemind`, a managed hooks block in ~/.codex/config.toml
          (Codex asks you to trust new hooks the first time), AGENTS.md
  kiro    .kiro/agents/hivemind.json: an agent with the MCP server and hooks
          (start it with `kiro-cli chat --agent hivemind`), AGENTS.md

Hooks find the project through .hivemind/config.json, so the global Codex
hooks do nothing outside Hivemind projects.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .db import Settings
from .project import write_config

AGENTS = ("claude", "codex", "kiro")
MD_MARKER = "<!-- hivemind -->"
PROTOCOL = f"""
{MD_MARKER}
## Hivemind (shared team memory)

Other developers' coding agents work in this repo at the same time, and
they all share one memory through the `hivemind` MCP tools. Your edits and
test runs are recorded automatically; use the tools for what hooks cannot see.

- Call `start_task` before editing, with the functions or files you will touch.
  Read the conflicts and past attempts it returns before you write code, and
  never repeat an approach recorded as failed unless you know why it works now.
- Call `record_decision` when the user makes a design choice, `add_convention`
  for a rule about how this repo works, `record_attempt` for anything tests
  would not show (e.g. an approach you abandoned and why).
- Call `handoff` if you stop mid-task, `finish_task` when done.
- Ask `why`, `who_knows` or `blast_radius` before changing unfamiliar code.

An edit that touches a teammate's in-progress code is blocked once with an
explanation. Tell the user about the overlap; retrying the edit lets it through.
"""


def _python() -> str:
    return sys.executable


def _cmd(*args: str) -> str:
    return " ".join(shlex.quote(a) for a in (_python(), "-m", "hivemind.cli", *args))


def _merge_json(path: Path, update) -> None:
    data = json.loads(path.read_text()) if path.exists() else {}
    update(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _append_protocol(path: Path) -> bool:
    text = path.read_text() if path.exists() else ""
    if MD_MARKER in text:
        return False
    path.write_text(text.rstrip() + "\n" + PROTOCOL if text else PROTOCOL.lstrip())
    return True


# ------------------------------------------------------------------ claude

CLAUDE_HOOKS = {
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PreToolUse": "Edit|Write|MultiEdit",
    "PostToolUse": "Edit|Write|MultiEdit|Bash",
    "PostToolUseFailure": "Bash",
    "Stop": None,
    "SessionEnd": None,
}


def init_claude(repo: Path) -> list[str]:
    def add_mcp(data: dict) -> None:
        data.setdefault("mcpServers", {})["hivemind"] = {
            "command": _python(),
            "args": ["-m", "hivemind.cli", "serve", "--agent", "claude"],
        }

    _merge_json(repo / ".mcp.json", add_mcp)
    hook = _cmd("hook", "--agent", "claude")

    def add_hooks(data: dict) -> None:
        hooks = data.setdefault("hooks", {})
        for event, matcher in CLAUDE_HOOKS.items():
            keep = [e for e in hooks.get(event, []) if not any("hivemind.cli hook" in h.get("command", "") for h in e.get("hooks", []))]
            entry = {"hooks": [{"type": "command", "command": hook, "timeout": 20}]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            hooks[event] = [*keep, entry]

    _merge_json(repo / ".claude" / "settings.local.json", add_hooks)
    done = [".mcp.json: hivemind MCP server", f".claude/settings.local.json: {len(CLAUDE_HOOKS)} hooks"]
    if _append_protocol(repo / "CLAUDE.md"):
        done.append("CLAUDE.md: coordination protocol")
    return done


# ------------------------------------------------------------------- codex

CODEX_BEGIN, CODEX_END = "# >>> HIVEMIND MANAGED HOOKS >>>", "# <<< HIVEMIND MANAGED HOOKS <<<"
CODEX_HOOKS = {"SessionStart": None, "UserPromptSubmit": None, "PreToolUse": ".*", "PostToolUse": ".*", "Stop": None}


def codex_hooks_block() -> str:
    hook = _cmd("hook", "--agent", "codex").replace("\\", "\\\\").replace('"', '\\"')
    parts = [CODEX_BEGIN]
    for event, matcher in CODEX_HOOKS.items():
        parts.append(f"[[hooks.{event}]]")
        if matcher:
            parts.append(f'matcher = "{matcher}"')
        parts += [f"[[hooks.{event}.hooks]]", 'type = "command"', f'command = "{hook}"', "timeout = 20", ""]
    parts.append(CODEX_END)
    return "\n".join(parts) + "\n"


def init_codex(repo: Path, codex_home: Path | None = None) -> list[str]:
    done: list[str] = []
    if shutil.which("codex"):
        subprocess.run(["codex", "mcp", "remove", "hivemind"], capture_output=True)
        r = subprocess.run(
            ["codex", "mcp", "add", "hivemind", "--", _python(), "-m", "hivemind.cli", "serve", "--agent", "codex"],
            capture_output=True,
            text=True,
        )
        done.append("codex mcp: hivemind server" if r.returncode == 0 else f"codex mcp add failed: {r.stderr.strip()[:200]}")
    else:
        done.append("codex not found: skipped `codex mcp add`")
    home = codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    cfg = home / "config.toml"
    text = cfg.read_text() if cfg.exists() else ""
    if text:
        shutil.copy(cfg, cfg.with_name("config.toml.hivemind-backup"))
    # Hivemind's tools only read and write the team graph, so let Codex call
    # them without a prompt (otherwise `codex exec` rejects every call).
    header = "[mcp_servers.hivemind]"
    if header in text and "default_tools_approval_mode" not in text.split(header, 1)[1].split("\n[", 1)[0]:
        text = text.replace(header, header + '\ndefault_tools_approval_mode = "approve"', 1)
        done.append("codex mcp: hivemind tools pre-approved")
    text = re.sub(re.escape(CODEX_BEGIN) + r".*?" + re.escape(CODEX_END) + r"\n?", "", text, flags=re.S)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(text.rstrip() + "\n\n" + codex_hooks_block())
    done.append(f"{cfg}: hooks block (approve it when Codex asks; until then run `hivemind watch`)")
    if _append_protocol(repo / "AGENTS.md"):
        done.append("AGENTS.md: coordination protocol")
    return done


# -------------------------------------------------------------------- kiro

def init_kiro(repo: Path) -> list[str]:
    hook = _cmd("hook", "--agent", "kiro")
    agent = {
        "name": "hivemind",
        "description": "Coding agent connected to the team's shared Hivemind memory",
        "prompt": "You work in a repo shared with other developers' coding agents. Follow the Hivemind protocol in AGENTS.md: "
        "use the hivemind tools to start tasks, read context before editing, record decisions and hand off.",
        "mcpServers": {"hivemind": {"command": _python(), "args": ["-m", "hivemind.cli", "serve", "--agent", "kiro"]}},
        "tools": ["read", "write", "shell", "grep", "glob", "@hivemind"],
        "allowedTools": ["read", "grep", "glob", "@hivemind"],
        "resources": ["file://AGENTS.md"],
        "hooks": {
            "agentSpawn": [{"command": hook}],
            "userPromptSubmit": [{"command": hook}],
            "preToolUse": [{"command": hook, "matcher": "*"}],
            "postToolUse": [{"command": hook, "matcher": "*"}],
            "stop": [{"command": hook}],
        },
    }
    path = repo / ".kiro" / "agents" / "hivemind.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agent, indent=2) + "\n")
    done = [".kiro/agents/hivemind.json: agent with MCP server and hooks (kiro-cli chat --agent hivemind)"]
    if _append_protocol(repo / "AGENTS.md"):
        done.append("AGENTS.md: coordination protocol")
    return done


def init(repo: Path, settings: Settings, agents: list[str] | None = None) -> list[str]:
    repo = repo.resolve()
    done = [f"{write_config(repo, settings).relative_to(repo)}: team '{settings.team}' at {settings.where()}"]
    for agent in agents or ["claude"]:
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r}; choose from {', '.join(AGENTS)}")
        done += {"claude": init_claude, "codex": init_codex, "kiro": init_kiro}[agent](repo)
    return done
