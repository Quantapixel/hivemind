"""Hook payload normalization, checked against real agent payloads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hivemind.capture import is_test_command, normalize, parse_failure

FIX = Path(__file__).parent / "fixtures" / "hooks"


def load(name: str):
    d = json.loads((FIX / f"{name}.json").read_text())
    agent = name.split("-")[0]
    return normalize(d["raw"], agent, env=d.get("env", {}))


def test_claude_edit():
    ev = load("claude-PreToolUse-Edit")
    assert (ev.kind, ev.action, ev.session_id) == ("pre_tool", "edit", "claude-session-1")
    assert ev.edits[0].path == "/repo/calc.py" and ev.edits[0].old_text.startswith("def add")


def test_claude_bash_success_and_failure():
    ok = load("claude-PostToolUse-Bash")
    assert (ok.action, ok.exit_code, ok.command) == ("shell", 0, "python3 -m unittest test_calc -v")
    bad = load("claude-PostToolUseFailure-Bash")
    assert (bad.kind, bad.action, bad.exit_code) == ("post_tool", "shell", 1)


def test_claude_prompt_and_stop():
    assert load("claude-UserPromptSubmit").prompt.startswith("Add a function mul")
    assert "test_mul fails" in load("claude-Stop").message


def test_kiro_payloads():
    w = load("kiro-preToolUse-fs_write")
    assert (w.kind, w.action, w.session_id, w.edits[0].path) == ("pre_tool", "edit", "kiro-session-1", "/repo/calc.py")
    sh = load("kiro-postToolUse-execute_bash")
    assert (sh.action, sh.exit_code) == ("shell", 1) and "test_mul" in sh.output
    assert load("kiro-postToolUse-fs_read").action == "other"
    assert load("kiro-agentSpawn").kind == "session_start"
    assert load("kiro-stop").kind == "stop"


def test_codex_patch_and_shell_assumed_shapes():
    p = load("codex-PreToolUse-apply_patch")
    assert p.action == "edit" and [e.path for e in p.edits] == ["/repo/calc.py"]
    sh = load("codex-PostToolUse-Bash")
    assert (sh.action, sh.exit_code) == ("shell", 1) and is_test_command(sh.command)


@pytest.mark.parametrize("cmd,expected", [
    ("pytest -q", True), ("python3 -m unittest test_calc -v", True), ("npm test", True), ("npm run test -- --watch=false", True),
    ("go test ./...", True), ("cargo test", True), ("uv run pytest tests/", True), ("cd app && yarn test", True),
    ("ls -la", False), ("git commit -m 'fix tests'", False), ("cat pytest.ini", False), ("pip install pytest-cov", False),
])
def test_test_command_detection(cmd, expected):
    assert is_test_command(cmd) is expected


def test_parse_failure_unittest_and_pytest():
    summary, sig = parse_failure(load("kiro-postToolUse-execute_bash").output)
    assert sig == "test_mul" and "AssertionError: 6 != 7" in summary
    out = "FAILED tests/test_a.py::test_x - AssertionError: boom\nFAILED tests/test_a.py::test_y - KeyError: 'k'\n"
    summary, sig = parse_failure(out)
    assert sig == "tests/test_a.py::test_x|tests/test_a.py::test_y" and summary.startswith("tests/test_a.py::test_x, tests/test_a.py::test_y failed")
