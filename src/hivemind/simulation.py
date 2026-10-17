"""A five-developer story on the shop app, played one step at a time.

Each step writes to a team graph through the same Brain calls that the
agents' hooks and MCP tools make, so the dashboard shows exactly what a
real team would see. The current step lives in the graph itself, so the
story can be advanced from stateless serverless calls (the hosted demo)
as well as from `hivemind simulate`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from falkordb import Graph

from .brain import Brain
from .indexer import index_repo

DEVS = {
    "Priya": "claude",   # tech lead
    "Marco": "kiro",
    "Aisha": "codex",
    "Ken": "claude",
    "Lena": "kiro",
}
VT = "shop/tokens.py::validate_token"
IT = "shop/tokens.py::issue_token"
CO = "shop/orders.py::checkout"
RF = "shop/payments.py::refund"
CU = "shop/auth.py::current_user"
REFRESH_TEST = "tests/test_auth.py::test_refresh_session"


def _brain(g: Graph, dev: str) -> Brain:
    b = Brain(g, dev, DEVS[dev])
    b.attach_session(f"sim-{dev.lower()}")
    return b


def _intent(b: Brain) -> str:
    r = b.q(
        "MATCH (:Session {id: $sid})-[:WORKS_ON]->(i:Intent) RETURN i.id AS id ORDER BY i.created DESC LIMIT 1",
        sid=b.session_id,
    )
    return r[0]["id"]


def _blocked(b: Brain, conflicts: list[dict]) -> str:
    """What the edit hook does when a teammate is on connected code."""
    c = conflicts[0]
    path = " → ".join(p.split("::")[-1] for p in c["path"])
    b._event("blocked", f"edit paused once: {c['other_dev']} is changing {c['other_function'].split('::')[-1]} ({path})", [c["target"]])
    return path


# Each step: (title, what to look at, action)
def _s1(g):
    p = _brain(g, "Priya")
    p.add_convention("All database access goes through the shop/db.py helpers")
    p.record_decision(
        "Tokens stay HMAC-signed until the mobile v3 app ships",
        "Mobile v2 parses the token string by hand; changing the format logs every phone user out.",
        ["shop/tokens.py"],
        source="PR #42",
    )


def _s2(g):
    m = _brain(g, "Marco")
    iid = m.note_prompt("Add JWT support to auth tokens")
    m.claim(iid, [VT, IT], quiet=True)


def _s3(g):
    m = _brain(g, "Marco")
    m.record_change("shop/tokens.py", {"changed": [VT]}, "fs_write")
    m.record_test_run("pytest tests/test_auth.py", False, "test_refresh_session failed: AssertionError: token issued before deploy was rejected", REFRESH_TEST)
    m.record_attempt([VT], "Replaced the HMAC check with a PyJWT decode", "failed", "tokens issued before the deploy are rejected, so users get logged out", _intent(m))


def _s4(g):
    a = _brain(g, "Aisha")
    iid = a.note_prompt("Add rate limiting to checkout")
    conflicts = a.conflicts([CO])
    a.claim(iid, [CO], quiet=True)
    if conflicts:
        _blocked(a, conflicts)


def _s5(g):
    a = _brain(g, "Aisha")
    a.set_progress("Checked with Marco: the rate limit runs before current_user, so his token change won't affect it.")
    a.record_change("shop/orders.py", {"changed": [CO]}, "apply_patch")
    a.record_test_run("pytest tests/", True)


def _s6(g):
    k = _brain(g, "Ken")
    iid = k.note_prompt("Add reason codes to refunds")
    conflicts = k.conflicts([RF])
    k.claim(iid, [RF], quiet=True)
    if conflicts:
        _blocked(k, conflicts)


def _s7(g):
    m = _brain(g, "Marco")
    m.record_change("shop/tokens.py", {"changed": [VT]}, "fs_write")
    m.record_test_run("pytest tests/test_auth.py", False, "test_refresh_session failed: AssertionError: token issued before deploy was rejected", REFRESH_TEST)


def _s8(g):
    m = _brain(g, "Marco")
    m.handoff(_intent(m), "PyJWT-only validation rejects existing sessions (tried twice). Next: accept both formats in validate_token and issue JWT behind a flag.", "Lena")
    m.set_progress("Out for the day. JWT work handed to Lena with notes.")


def _s9(g):
    m = _brain(g, "Marco")
    _brain(g, "Lena").resume(_intent(m))


def _s10(g):
    lena = _brain(g, "Lena")
    iid = _intent(lena)
    lena.record_change("shop/tokens.py", {"changed": [VT]}, "fs_write")
    lena.record_test_run("pytest tests/", True)
    lena.record_attempt([VT], "Validate both formats: HMAC and JWT; issue JWT behind FEATURE_JWT", "succeeded", "old sessions survive the deploy", iid)
    old = lena.q("MATCH (x:Decision) WHERE x.title STARTS WITH 'Tokens stay HMAC' RETURN x.id AS id")
    lena.record_decision(
        "Accept HMAC and JWT tokens until mobile v3 reaches 95% of users",
        "Lets us issue JWTs now without logging out mobile v2 users.",
        [VT],
        source="handoff from Marco",
        supersedes=old[0]["id"] if old else "",
    )


def _s11(g):
    k = _brain(g, "Ken")
    k.record_change("shop/payments.py", {"changed": [RF]}, "Edit")
    k.record_test_run("pytest tests/", True)
    k.release(_intent(k), "done", "Refunds carry a reason code")
    a = _brain(g, "Aisha")
    a.release(_intent(a), "done", "Checkout is rate limited per user")


def _s12(g):
    lena = _brain(g, "Lena")
    lena.release(_intent(lena), "done", "Dual-format tokens shipped")
    _brain(g, "Priya").why(VT)


# (title, narration, action, function to show in the inspector)
STEPS: list[tuple[str, str, Callable[[Graph], None], str | None]] = [
    ("Last month's team memory",
     "Priya, the tech lead, recorded a rule and a decision: tokens stay HMAC-signed until mobile v3 ships. Both are attached to the code they govern. Click validate_token to see them.",
     _s1, VT),
    ("Marco asks Kiro for JWT support",
     "Hooks turn his prompt into a task and claim validate_token and issue_token. Before writing code, his agent is shown Priya's HMAC decision.",
     _s2, VT),
    ("Marco's tests fail",
     "Kiro swaps in a PyJWT decode, runs the tests and test_refresh_session fails. Hooks record it on validate_token automatically, and Kiro's explanation is merged into that memory (red badge).",
     _s3, VT),
    ("Aisha starts rate limiting in Codex",
     "checkout calls current_user, which calls validate_token, the code Marco is changing. Her first edit is paused once with that exact path. See the red path on the graph and the conflict on the left.",
     _s4, None),
    ("Aisha coordinates, then ships",
     "She checks with Marco, makes the change, and her tests pass. Her agent's summary becomes the task's progress note.",
     _s5, CO),
    ("Ken starts refunds in Claude Code",
     "Aisha's checkout calls refund, so Ken is warned too. Two conflicts are now live on the dashboard.",
     _s6, None),
    ("Marco tries again, same failure",
     "The same test fails the same way. Hivemind merges it into the existing memory (seen 2×) instead of adding a duplicate.",
     _s7, VT),
    ("Marco hands off to Lena",
     "End of his day. The task, his two failures and a note on what to try next stay on the graph for whoever picks it up.",
     _s8, VT),
    ("Lena resumes in Kiro",
     "Ownership moves to Lena with the full history: Marco's sessions, the PyJWT dead end and Priya's decision. Her agent skips what already failed.",
     _s9, VT),
    ("Lena ships the fix",
     "Validating both token formats makes the tests pass, which resolves Marco's failure and links the fix. Her new decision supersedes Priya's old one.",
     _s10, VT),
    ("Ken and Aisha finish",
     "Their tasks close and the conflicts clear. Everything they did stays attached to checkout and refund.",
     _s11, RF),
    ("Why does validate_token look like this?",
     "Lena closes the JWT task. Anyone can now ask why: the current decision, the one it replaced, the failure seen twice and who fixed it. Click validate_token.",
     _s12, VT),
]


def state(g: Graph) -> int:
    r = g.query("MATCH (s:Sim {id: 'state'}) RETURN s.step").result_set
    return int(r[0][0]) if r else -1


def describe(g: Graph) -> dict:
    step = state(g)
    cur = STEPS[step - 1] if 1 <= step <= len(STEPS) else None
    return {
        "step": step,
        "total": len(STEPS),
        "title": cur[0] if cur else ("Ready to play" if step == 0 else "Not started"),
        "narration": cur[1] if cur else "Five developers, three different coding agents, one shared memory. Press play.",
        "focus": cur[3] if cur else None,
        "devs": [{"name": d, "agent": a} for d, a in DEVS.items()],
        "steps": [s[0] for s in STEPS],
    }


def reset(g: Graph, project: Path) -> dict:
    g.query("MATCH (n) DETACH DELETE n")
    index_repo(g, project)
    g.query("MERGE (s:Sim {id: 'state'}) SET s.step = 0")
    return describe(g)


def advance(g: Graph, expect: int | None = None) -> dict:
    """Run the next step. `expect` makes it idempotent when several viewers
    press play at once: only the call that sees the expected step runs it."""
    step = state(g)
    if step < 0:
        raise ValueError("simulation not started; reset it first")
    if expect is not None and step != expect:
        return describe(g)
    if step >= len(STEPS):
        return describe(g)
    g.query("MATCH (s:Sim {id: 'state'}) SET s.step = $n", {"n": step + 1})
    STEPS[step][2](g)
    return describe(g)
