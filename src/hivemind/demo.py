"""Scripted three-developer scenario on the demo shop app.

Run `hivemind ui` in one terminal and `hivemind demo` in another, then
watch the graph while three agents on three machines coordinate through it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .brain import Brain
from .db import Settings, connect
from .indexer import index_repo

SHOPAPP = Path(__file__).resolve().parents[2] / "examples" / "shopapp"

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
COLORS = {"alice": "\033[38;5;75m", "bob": "\033[38;5;209m", "carol": "\033[38;5;177m"}
RED, GREEN = "\033[38;5;203m", "\033[38;5;78m"


class Demo:
    def __init__(self, settings: Settings, pause: float, step: bool):
        self.graph = connect(settings)
        self.pause = pause
        self.step = step
        self.alice = Brain(self.graph, "alice", "claude-code")
        self.bob = Brain(self.graph, "bob", "cursor")
        self.carol = Brain(self.graph, "carol", "codex")

    def say(self, who: str, text: str) -> None:
        print(f"{COLORS.get(who, '')}{BOLD}{who:>6}{RESET} {text}")

    def note(self, text: str) -> None:
        print(f"{DIM}       {text}{RESET}")

    def beat(self, title: str) -> None:
        if self.step:
            input(f"\n{DIM}[enter] {title}{RESET}")
        else:
            time.sleep(self.pause)
            print(f"\n{BOLD}── {title}{RESET}")

    def show_conflicts(self, conflicts: list[dict]) -> None:
        if not conflicts:
            self.note(f"{GREEN}no conflicts{RESET}")
        for c in conflicts:
            path = " → ".join(p.split("::")[-1] for p in c["path"])
            self.note(f"{RED}⚠ {c['other_dev']} is on “{c['other_intent']}”{RESET}")
            self.note(f"  {path}   ({c['meaning']})")

    def run(self) -> None:
        self.graph.query("MATCH (n) DETACH DELETE n")
        stats = index_repo(self.graph, SHOPAPP)
        print(f"{BOLD}Indexed shop app:{RESET} {json.dumps(stats)}")

        self.beat("Team memory from last week")
        self.alice.add_convention("All DB access goes through shop/db.py helpers")
        self.alice.record_decision(
            "Tokens stay HMAC-signed until mobile v3 ships",
            "Mobile v2 parses the token string by hand; any format change logs every phone user out.",
            ["shop/tokens.py"],
            source="PR #42",
        )
        self.say("alice", "recorded a convention and a decision on shop/tokens.py")

        self.beat("Alice's agent starts a task")
        a = self.alice.start_task("Add JWT support to auth tokens", ["validate_token", "issue_token"])
        self.say("alice", "start_task: Add JWT support to auth tokens")
        for d in a["context"]["decisions"]:
            self.note(f"◆ decision in force: {d['title']} ({d['source']})")

        self.beat("Alice's agent edits code and runs the tests (captured by hooks)")
        self.alice.record_change("shop/tokens.py", {"changed": ["shop/tokens.py::validate_token"]}, "Edit")
        self.say("alice", "hook: edit captured -> validate_token changed")
        self.alice.record_test_run(
            "pytest tests/test_auth.py",
            False,
            "test_refresh_session failed: AssertionError: token issued before deploy was rejected",
            "tests/test_auth.py::test_refresh_session",
        )
        self.say("alice", f"hook: {RED}tests failed{RESET} -> failed attempt recorded on validate_token")
        self.alice.record_attempt(
            ["validate_token"],
            "Replaced HMAC check with PyJWT decode",
            "failed",
            "tokens issued before deploy are rejected, users get logged out",
            a["intent_id"],
        )
        self.note("Her agent explains why; the explanation merges into the captured failure.")

        self.beat("Bob's agent, on another machine, starts work on checkout")
        b = self.bob.start_task("Add rate limiting to checkout", ["checkout"])
        self.say("bob", "start_task: Add rate limiting to checkout")
        self.show_conflicts(b["conflicts"])
        self.note("Bob's agent warns him before writing a line of code.")

        self.beat("Carol's agent starts on refunds")
        c = self.carol.start_task("Add reason codes to refunds", ["refund"])
        self.say("carol", "start_task: Add reason codes to refunds")
        self.show_conflicts(c["conflicts"])
        for conv in c["context"]["conventions"]:
            self.note(f"✓ convention: {conv['rule']}")

        self.beat("End of Alice's day: she hands off")
        self.alice.handoff(
            a["intent_id"],
            "PyJWT-only validation breaks live sessions. Next: accept both formats in validate_token, issue JWT only behind a flag.",
            to_dev="bob",
        )
        self.say("alice", "handoff → bob")

        self.beat("Bob's agent picks it up the next morning")
        self.bob.release(b["intent_id"], "done", "rate limit added, no auth changes")
        self.say("bob", "finish_task: rate limiting (done)")
        h = self.bob.resume(a["intent_id"])
        self.say("bob", f"resume_task: {h['description']}")
        self.note(f"handoff note from {h['handoff_from']}: {h['handoff_note']}")
        for s in h["sessions"]:
            for att in s["attempts"]:
                self.note(f"{RED}✗ {s['dev']} already tried: {att['summary']} → {att['reason']}{RESET}")
        self.note("Bob's agent skips the dead end it would otherwise have repeated.")

        self.beat("Bob's agent ships the fix")
        self.bob.record_change("shop/tokens.py", {"changed": ["shop/tokens.py::validate_token"]}, "Edit")
        out = self.bob.record_test_run("pytest tests/test_auth.py", True)
        self.say("bob", f"hook: {GREEN}tests pass{RESET} -> resolves {len(out.get('resolves', []))} earlier failure")
        self.bob.record_attempt(
            ["validate_token"],
            "Dual-format validation: accept HMAC and JWT, issue JWT behind FEATURE_JWT",
            "succeeded",
            "old sessions survive deploy",
            a["intent_id"],
        )
        self.bob.record_decision(
            "Validate both token formats until mobile v3 is at 95% adoption",
            "Lets us issue JWTs without logging out mobile v2 users.",
            ["validate_token"],
            source="handoff from alice",
        )
        self.bob.release(a["intent_id"], "done", "dual-format tokens")
        self.say("bob", "explained the fix and recorded a decision")

        self.beat("Anyone can now ask why validate_token looks the way it does")
        why = self.carol.why("validate_token")
        for d in why["decisions"]:
            self.note(f"◆ {d['title']} — {d['dev']}, {d['source']}")
        for att in why["history"]:
            mark = f"{RED}✗" if att["outcome"] == "failed" else f"{GREEN}✓"
            fixed = f" (fixed by {att['resolved_by']})" if att.get("resolved") else ""
            self.note(f"{mark} {att['summary']} — {att['dev']}{fixed}{RESET}")
        print(f"\n{BOLD}Done.{RESET} Carol's refund task is still open; click around the live view.")


def run(settings: Settings, pause: float = 2.5, step: bool = False) -> None:
    Demo(settings, pause, step).run()
