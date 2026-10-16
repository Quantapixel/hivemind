"""Normalize hook payloads from different coding agents into one shape.

Formats were taken from real payloads (see tests/fixtures/hooks):

  Claude Code  PascalCase events. Edit/Write/MultiEdit/Bash. A command that
               exits non-zero fires PostToolUseFailure with error="Exit code N\\n...".
  Kiro CLI     camelCase events (agentSpawn, preToolUse, postToolUse, stop).
               fs_write/execute_bash; exit_status in tool_response.result[0].
               Session id comes from $KIRO_SESSION_ID.
  Codex CLI    Claude-compatible event names. Tool names vary by version
               (Bash, shell, exec_command, apply_patch), so edits and commands
               are recognized by their inputs rather than by name alone.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

KINDS = {
    "sessionstart": "session_start",
    "agentspawn": "session_start",
    "userpromptsubmit": "prompt",
    "pretooluse": "pre_tool",
    "posttooluse": "post_tool",
    "posttoolusefailure": "post_tool",
    "stop": "stop",
    "sessionend": "session_end",
}

EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "fs_write", "apply_patch", "write_file", "edit_file", "str_replace"}
SHELL_TOOLS = {"bash", "shell", "execute_bash", "exec_command", "local_shell", "run_command", "terminal"}


@dataclass
class Edit:
    path: str                 # absolute
    old_text: str | None      # text being replaced, if known (for line ranges)


@dataclass
class HookEvent:
    agent: str
    kind: str                 # session_start | prompt | pre_tool | post_tool | stop | session_end | other
    session_id: str
    cwd: str
    tool: str = ""
    action: str = "other"     # edit | shell | other
    edits: list[Edit] = field(default_factory=list)
    command: str = ""
    exit_code: int | None = None
    output: str = ""
    prompt: str = ""
    message: str = ""


def _get(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _abs(path: str, cwd: str) -> str:
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(cwd, path))


def _patch_files(patch: str, cwd: str) -> list[Edit]:
    """Files named in an apply_patch style patch (Codex)."""
    files = re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", patch, flags=re.M)
    files += re.findall(r"^\+\+\+ b/(.+)$", patch, flags=re.M)
    return [Edit(_abs(f.strip(), cwd), None) for f in dict.fromkeys(files)]


def normalize(raw: dict, agent: str, env: dict | None = None) -> HookEvent:
    env = env if env is not None else dict(os.environ)
    name = str(_get(raw, "hook_event_name", "hookEventName", default=""))
    kind = KINDS.get(name.lower(), "other")
    cwd = str(_get(raw, "cwd", default="") or os.getcwd())
    session = str(_get(raw, "session_id", "sessionId", default="") or env.get("KIRO_SESSION_ID") or env.get("CODEX_SESSION_ID") or "")
    ev = HookEvent(agent=agent, kind=kind, session_id=session, cwd=cwd)
    ev.prompt = str(_get(raw, "prompt", default="") or "")
    ev.message = str(_get(raw, "last_assistant_message", "assistant_response", "last-assistant-message", default="") or "")
    if kind not in ("pre_tool", "post_tool"):
        return ev

    tool = str(_get(raw, "tool_name", "toolName", default=""))
    ti = _get(raw, "tool_input", "toolInput", default={}) or {}
    tr = _get(raw, "tool_response", "toolResponse", default=None)
    ev.tool = tool
    low = tool.lower()
    if isinstance(ti, str):
        ti = {"input": ti}

    # ---- edits
    path = _get(ti, "file_path", "path", "notebook_path", "filePath", default=None)
    patch = _get(ti, "patch", "input", default="") if isinstance(_get(ti, "patch", "input", default=""), str) else ""
    if low in EDIT_TOOLS or (path and any(w in low for w in ("write", "edit", "patch", "replace"))) or "*** Begin Patch" in patch:
        ev.action = "edit"
        if path:
            if low == "multiedit":
                ev.edits = [Edit(_abs(path, cwd), e.get("old_string")) for e in ti.get("edits", [])] or [Edit(_abs(path, cwd), None)]
            else:
                old = _get(ti, "old_string", "old_str", default=None)
                ev.edits = [Edit(_abs(path, cwd), old if old else None)]
        elif patch:
            ev.edits = _patch_files(patch, cwd)

    # ---- shell commands
    cmd = _get(ti, "command", "cmd", default=None)
    if ev.action != "edit" and (low in SHELL_TOOLS or (cmd is not None and not path)):
        ev.action = "shell"
        ev.command = " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")
        # A Codex shell call that is really apply_patch
        if "*** Begin Patch" in ev.command:
            ev.action, ev.edits = "edit", _patch_files(ev.command, cwd)

    # ---- results
    if kind == "post_tool":
        if name.lower() == "posttoolusefailure":
            err = str(_get(raw, "error", default=""))
            m = re.match(r"Exit code (\d+)", err)
            ev.exit_code = int(m.group(1)) if m else 1
            ev.output = err
        elif isinstance(tr, dict):
            res = tr.get("result")
            first = res[0] if isinstance(res, list) and res and isinstance(res[0], dict) else {}
            code = _get(first, "exit_status", "exit_code", default=None)
            if code is None:
                code = _get(tr, "exit_code", "exitCode", "exit_status", "returncode", default=None)
            if code is None and ev.action == "shell":
                # Claude only reports PostToolUse for commands that succeeded.
                code = 1 if tr.get("interrupted") else 0
            ev.exit_code = int(code) if code is not None and str(code).lstrip("-").isdigit() else None
            ev.output = "\n".join(str(x) for x in (_get(first, "stdout", default=""), _get(first, "stderr", default=""),
                                                    tr.get("stdout", ""), tr.get("stderr", ""), tr.get("output", "")) if x)
        elif isinstance(tr, str):
            ev.output = tr
            m = re.search(r"(?:exit code|Exit code|exited with code)[: ]+(\d+)", tr)
            ev.exit_code = int(m.group(1)) if m else None
    return ev


# --------------------------------------------------------------- test runs

TEST_COMMAND = re.compile(
    r"(?:^|[\s;&|(/])(?:"
    r"pytest|py\.test|python3? -m (?:pytest|unittest)|nose2|tox|nox|"
    r"jest|vitest|mocha|ava|bun test|deno test|"
    r"(?:npm|pnpm|yarn)(?: run)? test|"
    r"go test|cargo (?:test|nextest)|mvn(?: \S+)* (?:test|verify)|gradlew? (?:\S+ )*test|"
    r"rspec|phpunit|dotnet test|ctest|make (?:test|check)"
    r")(?=$|[\s;&|)])"
)
_INSTALL = re.compile(r"\b(?:install|add|uninstall|remove|cat|less|head|tail|vim?|nano|code|echo|grep|rg|find|ls)\b")

_FAIL_PATTERNS = [
    re.compile(r"^FAILED ([^\s(]\S*?)(?: - .*)?$", re.M),            # pytest summary
    re.compile(r"^(?:FAIL|ERROR): (\w+) \(", re.M),                   # unittest
    re.compile(r"^--- FAIL: (\S+)", re.M),                           # go
    re.compile(r"^test (\S+) \.\.\. FAILED$", re.M),                 # cargo
    re.compile(r"^\s*[✕×] (.+?)(?: \(\d+ ?m?s\))?$", re.M),          # jest / vitest
    re.compile(r"^FAIL (\S+\.(?:js|ts|jsx|tsx))", re.M),             # jest file
]
_ERROR_LINE = re.compile(r"^\s*(?:E\s+)?((?:[A-Z]\w*(?:Error|Exception)|assert)\b.*\S)", re.M)


def is_test_command(command: str) -> bool:
    """True if any segment of a shell command runs a test suite."""
    for segment in re.split(r"&&|\|\||[;|\n]", command or ""):
        m = TEST_COMMAND.search(segment)
        if m and not _INSTALL.search(segment[: m.start() + 1]):
            return True
    return False


def parse_failure(output: str) -> tuple[str, str]:
    """(human summary, stable signature) for a failed test run."""
    names: list[str] = []
    for pat in _FAIL_PATTERNS:
        for m in pat.finditer(output or ""):
            n = m.group(1).strip()
            if n not in names:
                names.append(n)
    err = ""
    for m in _ERROR_LINE.finditer(output or ""):
        err = m.group(1)
        if "Error" in err or "assert" in err:
            break
    shown = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
    summary = (f"{shown} failed" if names else "tests failed") + (f": {err[:200]}" if err else "")
    return summary, "|".join(sorted(names))[:400]


_PASS_MARKERS = re.compile(r"^(?:OK(?: \(.*\))?|=+ .*\bpassed\b.*=+|ok\s+\S+.*|test result: ok\..*|Tests:\s+\d+ passed.*)$", re.M)
_FAIL_MARKERS = re.compile(r"^(?:FAILED(?: \(.*\))?|=+ .*\bfailed\b.*=+|FAIL\b.*|--- FAIL:.*|test result: FAILED\..*|Tests:.*\d+ failed.*)$", re.M)


def test_outcome(command: str, exit_code: int | None, output: str) -> bool | None:
    """Did a test command pass? Exit codes lie when output is piped
    (`pytest | tail` reports tail's status), so piped runs are judged by
    the test runner's own summary line. None when it cannot tell."""
    piped = bool(re.search(r"(?<![|])\|(?![|])", command)) and "pipefail" not in command
    if not piped and exit_code is not None:
        return exit_code == 0
    if _FAIL_MARKERS.search(output or ""):
        return False
    if _PASS_MARKERS.search(output or ""):
        return True
    return None if piped else (exit_code == 0 if exit_code is not None else None)
