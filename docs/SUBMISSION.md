# Graph Hacks submission: Hivemind

Copy-paste answers for the submission form. The video link points at a silent walkthrough; swap in the narrated recording if you make one.

**Project name:** Hivemind

**Tagline:** A shared graph brain for every coding agent on a team.

**Track:** Agent Memory and Coordination (Track 02).
*Optional second track:* Agents That Act on Connected Data. Agents run multi-hop call-graph queries through MCP before acting, and every warning shows the path that caused it.

**Links**
- Repository: https://github.com/Quantapixel/memofull
- Live demo (public, press Play): https://hivemind-live-demo.vercel.app
- Demo video: https://github.com/Quantapixel/memofull/releases/download/v1.0/hivemind-demo-walkthrough.mp4 (silent walkthrough; replace with the narrated video when it's recorded)
- Setup instructions: README → *Get started*
- Graph data model: README → *Graph data model*
- Cypher queries and algorithms: README → *Cypher queries and graph algorithms it depends on*

## Short description (~100 words)

Developers now run their own coding agents (Claude Code, Kiro, Codex) on the same codebase, but the agents can't see each other and forget everything between sessions. Hivemind puts every agent on one FalkorDB graph: the code's call graph plus who is changing what, what was tried, what failed and what the team decided. Hooks capture each edit down to the function and every test run automatically. Before an edit, agents are warned when a teammate is changing connected code, with the exact call path. Failures, fixes, decisions and handoffs follow the code across machines and sessions, and the memory consolidates itself as it grows.

## The problem

When a team works with AI coding agents, every agent starts from zero and works alone:
- Agent A changes `validate_token` while agent B, on another laptop, builds on its old behaviour. Git only notices the textual conflict after both are done; the *semantic* conflict goes unnoticed.
- Agent C retries an approach that broke the tests for agent D yesterday.
- When someone stops mid-task, the next person (and their agent) starts over.

## What Hivemind does

- **Captures memory automatically.** Hooks in Claude Code, Kiro and Codex turn prompts into tasks and record edits at function level (by AST hash). Test runs after a change become attempts, and the agent's own summary becomes the task's progress note.
- **Prevents collisions.** Before an edit, a variable-length traversal finds teammates' active claims up to 3 calls away, in either direction. The edit is blocked once, with the path (`checkout → current_user → validate_token`).
- **Consolidates.** Repeated failures merge ("seen 2× by Marco"), a passing change `RESOLVES` earlier failures, restated decisions `SUPERSEDE` older ones, and duplicate rules merge.
- **Hands off cleanly.** Ownership moves while the full history stays attached: sessions, attempts, notes.
- **Explains.** `why(fn)` returns the current decision, the one it replaced, the failures and who fixed them. `blast_radius` and `who_knows` are one query each.
- **Works across machines.** FalkorDB Cloud holds one graph per team. Teammates join with `hivemind join <dashboard>` plus an access code, and a Vercel dashboard shows everything live.

## How FalkorDB is used (central, not decorative)

- One graph per team (multi-graph) holds a code layer (`File`, `Function`, `CALLS`) and team memory (`Intent`, `CLAIMS`, `Change`, `Attempt`, `RESOLVES`, `Decision`, `SUPERSEDES`, `Convention`, `Session`, `Developer`).
- **Variable-length traversals** (`[:CALLS*1..3]`) for conflict detection and blast radius, run before every edit.
- **Graph algorithms:** `algo.pageRank` (core functions), `algo.betweenness` (chokepoints) and `shortestPath` (path finder, including the closest shared caller).
- Incremental re-indexing with Cypher `UNWIND`/`MERGE` as agents edit files.
- Remove FalkorDB and nothing works: conflicts, context, consolidation, handoffs and the dashboard are all graph queries.

## Tested with real agents

Three "machines" (separate clones) shared one FalkorDB Cloud graph:
- **Kiro:** given a task that never mentioned Hivemind. Its prompt, exact function changes and test results were recorded automatically.
- **Claude Code, as a different developer:** said unprompted, "the revocation check from kira's recent change is untouched".
- **Codex:** used the shared task board through MCP; its edits were captured by `hivemind watch`.

Separately, a fresh install from GitHub followed by `hivemind join` was run against the live dashboard. There are 63 automated tests, all running against a real FalkorDB.

## Data

All data is synthetic: `examples/shopapp` is a made-up shop backend, and the simulation's developers are fictional.

## AI assistance disclosure

Built during the hackathon window (first commit Oct 16, 2026) with Claude Code (Anthropic) as an AI coding assistant. Jaskaran Singh came up with the idea and set the product direction: cross-machine shared memory, automatic capture for Claude Code, Kiro and Codex, the hosted dashboard, one-command onboarding and the public simulation. He reviewed each stage; much of the code was written by the assistant under that direction. Full statement in the README.

## Team

Jaskaran Singh (@Quantapixel)
