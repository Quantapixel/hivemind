"""Automatic capture and memory consolidation (needs a running FalkorDB)."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from hivemind.brain import Brain, similarity
from hivemind.db import Settings, connect
from hivemind.indexer import index_file, index_repo

SHOPAPP = Path(__file__).resolve().parents[1] / "examples" / "shopapp"


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(SHOPAPP, root)
    try:
        g = connect(Settings(team=f"test_{uuid.uuid4().hex[:8]}"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"FalkorDB not reachable: {exc}")
    index_repo(g, root)
    yield g, root
    g.delete()


def edit(root: Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    p.write_text(p.read_text().replace(old, new))


def test_similarity_is_word_overlap():
    assert similarity("PyJWT decode breaks refresh", "pyjwt decode breaks the refresh flow") > 0.6
    assert similarity("PyJWT decode", "rate limit checkout") == 0


def test_edit_then_failing_tests_becomes_a_failed_attempt(repo):
    g, root = repo
    alice = Brain(g, "alice", "claude")
    alice.attach_session("s-alice")
    alice.note_prompt("Make tokens expire at the exact second")
    edit(root, "shop/tokens.py", "int(expires) < time.time()", "int(expires) <= time.time()")
    change = alice.record_change("shop/tokens.py", index_file(g, root, "shop/tokens.py"), "Edit")
    assert change["changed"] == ["shop/tokens.py::validate_token"]

    out = alice.record_test_run("pytest", passed=False, failure="test_refresh: AssertionError", signature="test_refresh")
    assert out["recorded"] == "failed"
    [a] = Brain(g, "bob").context(["validate_token"])["attempts"]
    assert a["outcome"] == "failed" and a["auto"] and a["summary"] == "validate_token changed · tests fail"
    assert a["task"] == "Make tokens expire at the exact second"
    # Nothing changed since, so a re-run records no new attempt.
    assert alice.record_test_run("pytest", passed=False)["recorded"] is None


def test_same_failure_from_two_people_is_merged(repo):
    g, _ = repo
    for dev in ("alice", "bob"):
        b = Brain(g, dev)
        b.attach_session(f"s-{dev}")
        b.record_attempt(["validate_token"], "x", "failed", "boom", auto=True, signature="test_refresh")
    [a] = Brain(g, "carol").context(["validate_token"])["attempts"]
    assert a["times"] == 2 and sorted(a["seen_by"]) == ["alice", "bob"]


def test_similar_wording_is_merged_but_different_failures_are_not(repo):
    g, _ = repo
    b = Brain(g, "alice")
    b.record_attempt(["validate_token"], "Replaced HMAC with PyJWT decode", "failed", "old tokens rejected")
    assert b.record_attempt(["validate_token"], "Swapped HMAC for a PyJWT decode", "failed", "old tokens get rejected")["merged"]
    assert not b.record_attempt(["validate_token"], "Cached signatures in Redis", "failed", "stale cache")["merged"]


def test_success_resolves_open_failures(repo):
    g, _ = repo
    alice, bob = Brain(g, "alice"), Brain(g, "bob")
    alice.record_attempt(["validate_token"], "PyJWT decode", "failed", "old tokens rejected")
    out = bob.record_attempt(["validate_token"], "Accept both formats", "succeeded", "tests pass")
    assert len(out["resolves"]) == 1
    [f] = [a for a in Brain(g, "carol").context(["validate_token"])["attempts"] if a["outcome"] == "failed"]
    assert f["resolved"] and f["resolved_by"] == "bob" and f["fix"] == "Accept both formats"


def test_restated_decision_supersedes_the_old_one(repo):
    g, _ = repo
    b = Brain(g, "alice")
    b.record_decision("Tokens stay HMAC until mobile v3", "mobile v2 parses tokens", ["shop/tokens.py"])
    out = b.record_decision("Tokens stay HMAC until mobile v4", "mobile v2 and v3 parse tokens", ["validate_token"])
    assert out["supersedes"] == ["Tokens stay HMAC until mobile v3"]
    why = Brain(g, "bob").why("validate_token")
    assert [d["title"] for d in why["decisions"]] == ["Tokens stay HMAC until mobile v4"]
    assert why["superseded_decisions"][0]["replaced_by"] == "Tokens stay HMAC until mobile v4"


def test_unrelated_decision_is_returned_as_related_not_superseded(repo):
    g, _ = repo
    b = Brain(g, "alice")
    b.record_decision("Tokens stay HMAC until mobile v3", "mobile v2 parses tokens", ["shop/tokens.py"])
    out = b.record_decision("Log every failed validation", "security audit needs it", ["validate_token"])
    assert out["supersedes"] == [] and out["related"][0]["title"].startswith("Tokens stay HMAC")


def test_duplicate_convention_is_merged(repo):
    g, _ = repo
    first = Brain(g, "alice").add_convention("All DB access goes through shop/db.py helpers")
    again = Brain(g, "bob").add_convention("DB access must go through the shop/db.py helpers")
    assert again["convention_id"] == first["convention_id"]


def test_prompt_names_the_automatic_task(repo):
    g, root = repo
    b = Brain(g, "alice", "kiro")
    b.attach_session("s1")
    edit(root, "shop/cart.py", "qty: int = 1", "qty: int = 2")
    b.record_change("shop/cart.py", index_file(g, root, "shop/cart.py"))
    b.note_prompt("Default cart quantity should be two")
    [t] = b.open_tasks()
    assert t["description"] == "Default cart quantity should be two"
    assert t["claimed"] == ["shop/cart.py::add_item"]


def test_start_task_adopts_the_automatic_task_from_the_prompt(repo):
    g, _ = repo
    b = Brain(g, "kira", "kiro")
    b.attach_session("kiro-s9")
    auto = b.note_prompt("add token revocation")
    declared = b.start_task("Add revoke_token and reject revoked tokens", ["validate_token"])
    assert declared["intent_id"] == auto
    [t] = b.open_tasks()
    assert t["description"] == "Add revoke_token and reject revoked tokens"


def test_function_missing_from_one_copy_keeps_its_memory(repo):
    g, root = repo
    p = root / "shop" / "tokens.py"
    original = p.read_text()
    p.write_text(original + "\n\ndef revoke(token):\n    sign(token)\n\ndef scratch():\n    pass\n")
    kira = Brain(g, "kira")
    kira.record_change("shop/tokens.py", index_file(g, root, "shop/tokens.py"))
    kira.record_attempt(["revoke"], "Blocklist in memory", "succeeded", "tests pass")
    # Carl's copy never had kira's functions; re-indexing it must not erase her memory.
    p.write_text(original)
    diff = index_file(g, root, "shop/tokens.py")
    assert "shop/tokens.py::revoke" not in diff["removed"]
    assert Brain(g, "x").context(["revoke"])["attempts"][0]["summary"] == "Blocklist in memory"


def test_agent_explanation_enriches_the_captured_attempt(repo):
    g, _ = repo
    b = Brain(g, "alice", "claude")
    b.attach_session("s-x")
    b.record_change("shop/tokens.py", {"changed": ["shop/tokens.py::validate_token"]})
    b.record_test_run("pytest", False, "test_refresh failed: boom", "test_refresh")
    out = b.record_attempt(["validate_token"], "Replaced HMAC with PyJWT", "failed", "old tokens rejected")
    assert out["explained"]
    [a] = Brain(g, "bob").context(["validate_token"])["attempts"]
    assert a["summary"] == "Replaced HMAC with PyJWT" and "test_refresh failed" in a["reason"]


def test_explaining_a_fix_updates_the_resolved_failure(repo):
    g, _ = repo
    alice, bob = Brain(g, "alice"), Brain(g, "bob")
    alice.attach_session("a"); bob.attach_session("b")
    alice.record_attempt(["validate_token"], "PyJWT decode", "failed", "old tokens rejected")
    bob.record_change("shop/tokens.py", {"changed": ["shop/tokens.py::validate_token"]})
    bob.record_test_run("pytest", True)
    bob.record_attempt(["validate_token"], "Accept both token formats", "succeeded", "old sessions survive")
    [f] = [a for a in Brain(g, "x").context(["validate_token"])["attempts"] if a["outcome"] == "failed"]
    assert f["fix"] == "Accept both token formats"


def test_timestamps_come_from_the_database_clock(repo, monkeypatch):
    import time as _time

    g, _ = repo
    real = _time.time
    monkeypatch.setattr("hivemind.brain.time.time", lambda: real() + 5.5 * 3600)  # a laptop 5.5h fast
    b = Brain(g, "alice")
    b.attach_session("s-clock")
    b.note_prompt("anything")
    ts = b.q("MATCH (e:Event) RETURN max(e.ts) AS ts")[0]["ts"]
    server = g.query("RETURN timestamp()").result_set[0][0] / 1000
    assert abs(ts - server) < 5
