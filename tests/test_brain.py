"""Integration tests against a real FalkorDB (skipped if none is running)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from memofull.brain import Brain
from memofull.db import Settings, connect
from memofull.indexer import index_repo, parse_repo, resolve

SHOPAPP = Path(__file__).resolve().parents[1] / "examples" / "shopapp"


@pytest.fixture()
def graph():
    try:
        g = connect(Settings(team=f"test_{uuid.uuid4().hex[:8]}"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"FalkorDB not reachable: {exc}")
    index_repo(g, SHOPAPP)
    yield g
    g.delete()


def test_parse_finds_calls_across_files():
    calls, imports = resolve(parse_repo(SHOPAPP))
    assert ("shop/auth.py::current_user", "shop/tokens.py::validate_token") in calls
    assert ("shop/orders.py", "shop/auth.py") in imports


def test_resolve_accepts_loose_targets(graph):
    b = Brain(graph, "alice")
    assert b.resolve(["validate_token"]) == ["shop/tokens.py::validate_token"]
    assert b.resolve(["shop/tokens.py::validate_token"]) == ["shop/tokens.py::validate_token"]
    assert set(b.resolve(["tokens.py"])) == {
        "shop/tokens.py::sign",
        "shop/tokens.py::issue_token",
        "shop/tokens.py::validate_token",
    }


def test_conflict_found_through_call_graph(graph):
    alice, bob = Brain(graph, "alice"), Brain(graph, "bob")
    alice.start_task("change token format", ["validate_token"])
    result = bob.start_task("rate limit checkout", ["checkout"])
    [c] = result["conflicts"]
    assert c["other_dev"] == "alice"
    assert c["relation"] == "depends_on_theirs"
    assert c["path"] == [
        "shop/orders.py::checkout",
        "shop/auth.py::current_user",
        "shop/tokens.py::validate_token",
    ]


def test_siblings_sharing_a_caller_do_not_conflict(graph):
    # checkout calls both cart_total and charge_card, but neither calls the other.
    Brain(graph, "alice").start_task("tweak totals", ["cart_total"])
    assert Brain(graph, "bob").conflicts(["charge_card"]) == []


def test_own_claims_and_finished_tasks_are_not_conflicts(graph):
    alice, bob = Brain(graph, "alice"), Brain(graph, "bob")
    t = alice.start_task("a", ["validate_token"])
    assert alice.conflicts(["current_user"]) == []
    alice.release(t["intent_id"])
    assert bob.conflicts(["current_user"]) == []


def test_failed_attempt_reaches_next_agent_as_context(graph):
    Brain(graph, "alice").record_attempt(["validate_token"], "PyJWT decode", "failed", "breaks refresh")
    ctx = Brain(graph, "bob").context(["current_user"])  # a direct caller
    [a] = ctx["attempts"]
    assert (a["summary"], a["outcome"], a["relation"]) == ("PyJWT decode", "failed", "neighbour")


def test_decision_on_file_governs_its_functions(graph):
    Brain(graph, "alice").record_decision("keep HMAC", "mobile v2", ["shop/tokens.py"], "PR #42")
    why = Brain(graph, "bob").why("validate_token")
    assert [d["title"] for d in why["decisions"]] == ["keep HMAC"]


def test_handoff_and_resume_transfers_ownership_and_history(graph):
    alice, bob = Brain(graph, "alice", "claude-code"), Brain(graph, "bob", "cursor")
    t = alice.start_task("jwt", ["validate_token"])
    alice.record_attempt(["validate_token"], "PyJWT", "failed", "breaks refresh", t["intent_id"])
    alice.handoff(t["intent_id"], "try dual format", "bob")
    assert [x["status"] for x in bob.open_tasks()] == ["handed_off"]

    h = bob.resume(t["intent_id"])
    assert h["owner"] == "bob" and h["status"] == "active"
    assert h["handoff_note"] == "try dual format"
    assert [s["dev"] for s in h["sessions"]] == ["alice", "bob"]
    assert h["sessions"][0]["attempts"][0]["summary"] == "PyJWT"
    # Alice now sees Bob's claim as someone else's.
    assert alice.conflicts(["validate_token"])[0]["other_dev"] == "bob"


def test_blast_radius_lists_transitive_callers(graph):
    callers = {r["caller"]: r["distance"] for r in Brain(graph, "x").blast_radius("validate_token")}
    assert callers["shop/auth.py::current_user"] == 1
    assert callers["shop/orders.py::checkout"] == 2
    assert callers["shop/api.py::handle"] == 2


def test_reindex_removes_deleted_functions(graph, tmp_path):
    (tmp_path / "m.py").write_text("def a():\n    b()\n\ndef b():\n    pass\n")
    stats = index_repo(graph, tmp_path)
    assert stats["functions"] == 2
    assert Brain(graph, "x").resolve(["validate_token"]) == []


def test_hook_resolves_edit_to_functions_and_warns_once(graph):
    from memofull.capture import Edit, HookEvent
    from memofull.hooks import on_pre_edit

    Brain(graph, "alice").start_task("jwt", ["validate_token"])
    bob = Brain(graph, "bob", "claude")
    bob.attach_session("claude-test")
    ev = HookEvent(agent="claude", kind="pre_tool", session_id="test", cwd=str(SHOPAPP), action="edit",
                   edits=[Edit(str(SHOPAPP / "shop" / "orders.py"), '    total = cart.cart_total(user["id"])')])
    reason = on_pre_edit(bob, ev, SHOPAPP)
    assert "checkout -> current_user -> validate_token" in reason
    assert on_pre_edit(bob, ev, SHOPAPP) is None  # acknowledged, retry goes through
    [task] = [t for t in bob.open_tasks() if t["dev"] == "bob"]
    assert task["claimed"] == ["shop/orders.py::checkout"]  # only the edited function
