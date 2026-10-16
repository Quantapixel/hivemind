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
    assert a["outcome"] == "failed" and a["auto"] and "Make tokens expire" in a["summary"]
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
