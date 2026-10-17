"""MCP server: exposes the team brain as tools to any coding agent
(Claude Code, Codex, Kiro, ...). Each developer's agent launches it over
stdio; it finds the project's .memofull/config.json and the team's shared
FalkorDB from there."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .brain import Brain
from .db import Settings, connect
from .project import default_dev, find_root, load_settings, read_session

INSTRUCTIONS = """\
Memofull is the shared memory of every coding agent on this team. Other
developers' agents are editing the same codebase right now, and they read
what you write here. Edits and test runs are captured automatically by
hooks; the tools below are for what hooks cannot see. Follow this protocol:

1. Before editing code, call `start_task` with what you intend to do and the
   functions or files you will touch. Read the conflicts and context it
   returns. If another developer is working nearby, tell the user and
   coordinate before you change shared code. Never repeat an approach that a
   past attempt records as failed unless you know why it will work now.
2. When an approach fails or succeeds in a way worth remembering, call
   `record_attempt`. When the user makes a design choice, call
   `record_decision`. When you learn a rule about how this repo works, call
   `add_convention`.
3. If you must stop before finishing, call `handoff` with a note for whoever
   picks it up. When done, call `finish_task`.
4. To pick up someone else's work, call `open_tasks` then `resume_task`.

Targets can be function names ("login"), dotted names ("Session.refresh"),
file paths ("shop/auth.py") or full ids ("shop/auth.py::login").
"""


def create_server(brain: Brain) -> MCPServer:
    mcp = MCPServer("memofull", instructions=INSTRUCTIONS)

    @mcp.tool()
    def start_task(intent: str, targets: list[str]) -> dict:
        """Declare what you are about to do and claim the code you will touch.
        Returns your intent_id, any other developer working within 3 call-graph
        hops (with the path that links you), and everything the team already
        knows about this code: past attempts, decisions and conventions."""
        return brain.start_task(intent, targets)

    @mcp.tool()
    def claim_more(intent_id: str, targets: list[str]) -> dict:
        """Add more functions or files to a task you already started.
        Returns any new conflicts."""
        return brain.claim(intent_id, targets)

    @mcp.tool()
    def check_conflicts(targets: list[str], hops: int = 3) -> list[dict]:
        """Check whether anyone else is working on code within `hops` call-graph
        steps of these targets, without claiming anything."""
        return brain.conflicts(targets, hops=hops)

    @mcp.tool()
    def get_context(targets: list[str]) -> dict:
        """Everything the team knows about this code: attempts on it and its
        direct neighbours, decisions that govern it, conventions, and who is
        working nearby."""
        return brain.context(targets)

    @mcp.tool()
    def record_attempt(
        targets: list[str], summary: str, outcome: str, reason: str = "", intent_id: str = ""
    ) -> dict:
        """Remember an approach you tried. outcome: 'failed', 'succeeded' or
        'abandoned'. Put the cause in `reason` (e.g. the failing test or error)
        so the next agent does not repeat it."""
        return brain.record_attempt(targets, summary, outcome, reason, intent_id or None)

    @mcp.tool()
    def record_decision(title: str, rationale: str, targets: list[str], source: str = "", supersedes: str = "") -> dict:
        """Remember a design decision and the code it governs. `source` can be
        a PR, issue or chat link. A decision that restates a current one on the
        same code replaces it automatically; pass `supersedes` (a decision id)
        to replace one explicitly. Returns other current decisions on that
        code as `related`: check they still agree with this one."""
        return brain.record_decision(title, rationale, targets, source, supersedes)

    @mcp.tool()
    def add_convention(rule: str, files: list[str] | None = None) -> dict:
        """Remember a rule for how code is written here. Leave `files` empty
        for a repo-wide rule."""
        return brain.add_convention(rule, files)

    @mcp.tool()
    def finish_task(intent_id: str, summary: str = "", outcome: str = "done") -> dict:
        """Release your claims. outcome: 'done' or 'abandoned'."""
        return brain.release(intent_id, outcome, summary)

    @mcp.tool()
    def handoff(intent_id: str, note: str, to_dev: str = "") -> dict:
        """Pause your task so a teammate's agent can resume it. The note should
        say what is done, what failed and what to try next."""
        return brain.handoff(intent_id, note, to_dev or None)

    @mcp.tool()
    def open_tasks() -> list[dict]:
        """List active and handed-off tasks across the team."""
        return brain.open_tasks()

    @mcp.tool()
    def resume_task(intent_id: str) -> dict:
        """Take over a handed-off task. Returns its full history: every session,
        every attempt, the handoff note and current context."""
        return brain.resume(intent_id)

    @mcp.tool()
    def why(target: str) -> dict:
        """Explain why code is the way it is: the decisions that govern it, the
        attempts made on it, and the people involved."""
        return brain.why(target)

    @mcp.tool()
    def blast_radius(target: str, hops: int = 3) -> list[dict]:
        """What depends on this code: every transitive caller up to `hops` away."""
        return brain.blast_radius(target, hops)

    @mcp.tool()
    def who_knows(target: str) -> list[dict]:
        """Which teammates have worked on this code or the code around it."""
        return brain.experts(target)

    return mcp


def run(settings: Settings | None = None, agent: str = "agent", transport: str = "stdio") -> None:
    root = find_root()
    if root is not None:
        settings = load_settings(root)
    settings = settings or Settings.from_env()
    if root is None and settings.dev in ("", "anonymous"):
        settings.dev = default_dev()
    brain = Brain(connect(settings), settings.dev, agent)
    if root is not None:
        # Share the session the agent's SessionStart hook recorded.
        brain.session_hint = lambda: read_session(root, agent)
    create_server(brain).run(transport=transport)
