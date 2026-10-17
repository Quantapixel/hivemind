"""End-to-end hook pipeline with real payload shapes, per agent."""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from pathlib import Path

import pytest

from hivemind.brain import Brain
from hivemind.capture import normalize
from hivemind.db import Settings, connect
from hivemind.hooks import handle
from hivemind.indexer import index_repo

FIX = Path(__file__).parent / "fixtures" / "hooks"
SHOPAPP = Path(__file__).resolve().parents[1] / "examples" / "shopapp"


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(SHOPAPP, root)
    try:
        g = connect(Settings(team=f"test_{uuid.uuid4().hex[:8]}"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"FalkorDB not reachable: {exc}")
    index_repo(g, root)
    yield g, root
    g.delete()


def fixture(name: str, root: Path, **changes) -> dict:
    raw = json.loads((FIX / f"{name}.json").read_text())["raw"]
    raw = json.loads(json.dumps(raw).replace("/repo", str(root)))
    for k, v in changes.items():
        raw[k] = v
    return raw


def run(brain, root, raw, agent, env=None):
    ev = normalize(raw, agent, env=env or {})
    return handle(ev, brain, root)


def edit_payload(agent, root, old, new):
    path = str(root / "shop" / "tokens.py")
    if agent == "kiro":
        ti = {"command": "str_replace", "path": path, "old_str": old, "new_str": new}
        return {"hook_event_name": "postToolUse", "cwd": str(root), "tool_name": "fs_write", "tool_input": ti, "tool_response": {"success": True}}
    if agent == "codex":
        patch = f"*** Begin Patch\n*** Update File: shop/tokens.py\n-{old}\n+{new}\n*** End Patch"
        return {"hook_event_name": "PostToolUse", "session_id": "s1", "cwd": str(root), "tool_name": "apply_patch", "tool_input": {"input": patch}, "tool_response": {}}
    ti = {"file_path": path, "old_string": old, "new_string": new}
    return {"hook_event_name": "PostToolUse", "session_id": "s1", "cwd": str(root), "tool_name": "Edit", "tool_input": ti, "tool_response": {}}


def test_post_edit_and_failed_tests_record_an_attempt_for_every_agent(project):
    g, root = project
    old, new = "if int(expires) < time.time():", "if int(expires) <= time.time():"
    kiro_fail = json.loads((FIX / "kiro-postToolUse-execute_bash.json").read_text())["raw"]
    for agent in ("claude", "kiro", "codex"):
        brain = Brain(g, f"dev-{agent}", agent)
        brain.attach_session(f"{agent}-s1")
        run(brain, root, {"hook_event_name": "UserPromptSubmit" if agent != "kiro" else "userPromptSubmit",
                          "session_id": "s1", "cwd": str(root), "prompt": f"tighten token expiry ({agent})"}, agent, {"KIRO_SESSION_ID": "s1"})
        p = root / "shop" / "tokens.py"
        p.write_text(p.read_text().replace(old, new))
        run(brain, root, edit_payload(agent, root, old, new), agent, {"KIRO_SESSION_ID": "s1"})
        assert brain.pending_changes() == ["shop/tokens.py::validate_token"], agent
        if agent == "claude":
            fail = fixture("claude-PostToolUseFailure-Bash", root, error="Exit code 1\nFAIL: test_expiry (t.T.test_expiry)\nAssertionError: boom")
        elif agent == "kiro":
            fail = copy.deepcopy(kiro_fail)
        else:
            fail = fixture("codex-PostToolUse-Bash", root)
        run(brain, root, fail, agent, {"KIRO_SESSION_ID": "s1"})
        assert brain.pending_changes() == [], agent
        old, new = new, old  # next agent flips it back, so there is a real change again

    attempts = Brain(g, "x").context(["validate_token"])["attempts"]
    assert all(a["outcome"] == "failed" and a["auto"] for a in attempts)
    # kiro and codex both hit test_mul -> merged into one memory seen by both
    merged = [a for a in attempts if a["times"] == 2]
    assert merged and sorted(merged[0]["seen_by"]) == ["dev-codex", "dev-kiro"]


def test_passing_tests_after_a_fix_resolve_the_failure(project):
    g, root = project
    b = Brain(g, "alice", "claude")
    b.attach_session("claude-s1")
    p = root / "shop" / "tokens.py"
    p.write_text(p.read_text().replace("< time.time()", "<= time.time()"))
    run(b, root, edit_payload("claude", root, "<", "<="), "claude")
    run(b, root, fixture("claude-PostToolUseFailure-Bash", root, error="Exit code 1\nFAIL: test_x (a.B.test_x)\nAssertionError: no"), "claude")
    p.write_text(p.read_text().replace("<= time.time()", "< time.time()"))
    run(b, root, edit_payload("claude", root, "<=", "<"), "claude")
    run(b, root, fixture("claude-PostToolUse-Bash", root), "claude")
    attempts = Brain(g, "bob").context(["validate_token"])["attempts"]
    failed = [a for a in attempts if a["outcome"] == "failed"]
    assert failed[0]["resolved"] and failed[0]["resolved_by"] == "alice"


def test_stop_message_becomes_task_progress(project):
    g, root = project
    b = Brain(g, "alice", "kiro")
    b.attach_session("kiro-s1")
    run(b, root, {"hook_event_name": "userPromptSubmit", "cwd": str(root), "prompt": "add revoke"}, "kiro", {"KIRO_SESSION_ID": "s1"})
    run(b, root, {"hook_event_name": "stop", "cwd": str(root), "assistant_response": "Added revoke(); tests pass."}, "kiro", {"KIRO_SESSION_ID": "s1"})
    r = b.q("MATCH (i:Intent) RETURN i.description AS d, i.progress AS p")
    assert r == [{"d": "add revoke", "p": "Added revoke(); tests pass."}]


def test_kiro_conflict_blocks_with_exit_code_2(project):
    g, root = project
    Brain(g, "alice").start_task("jwt", ["validate_token"])
    bob = Brain(g, "bob", "kiro")
    bob.attach_session("kiro-s2")
    raw = {"hook_event_name": "preToolUse", "cwd": str(root), "tool_name": "fs_write",
           "tool_input": {"command": "str_replace", "path": str(root / "shop" / "orders.py"), "old_str": '    total = cart.cart_total(user["id"])', "new_str": "x"}}
    out, err, code = run(bob, root, raw, "kiro", {"KIRO_SESSION_ID": "s2"})
    assert code == 2 and "validate_token" in err
