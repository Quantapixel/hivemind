# ⬡ Hivemind

**A shared graph brain for every coding agent on a team.** Built on [FalkorDB](https://falkordb.com) for Graph Hacks, Track 02: Agent Memory and Coordination.

Five developers each run their own Claude Code, Cursor or Codex agent on the same repo. None of those agents knows what the others are doing. Agent A rewrites `validate_token()` while Agent B builds on its old behaviour. Agent C retries a fix that Agent D found breaks production yesterday. Every session starts from zero.

Hivemind puts every agent on one graph. It holds the code's call graph, who is changing what, what has been tried, and what the team decided. Agents get three things from it:

- **Warnings before conflicts happen.** Before an edit, the agent learns that a teammate is changing code it depends on, and it gets the exact call path: `checkout → current_user → validate_token`. Git only catches the textual conflict after both people are done. Hivemind catches the *semantic* one before anyone writes code.
- **Memory of what failed.** "Alice tried PyJWT here; it logged every user out" stays attached to the function. It reaches the next agent that touches that function *or anything that calls it*, on any machine, in any later session.
- **Clean handoffs.** Alice's agent stops mid-task, and Bob's agent resumes it the next morning with every session, attempt and note.

![live view](docs/live-view.png)

## Why a graph

Every question Hivemind answers is a question about connections:

| Question | Graph work |
|---|---|
| Is anyone working on code that mine calls, or that calls mine? | Variable-length `CALLS` traversal from my functions to other developers' `CLAIMS`, returning the shortest path |
| What has been tried on this code or its neighbours? | `(:Attempt)-[:TOUCHED]->(:Function)` within one hop of the call graph |
| Why is this code like this? | `(:Developer)-[:DECIDED]->(:Decision)-[:GOVERNS]->(:Function\|:File)` |
| What breaks if I change it? | Transitive callers, `(caller)-[:CALLS*1..3]->(target)` |
| Who knows this code? | Developers linked to it through claims, attempts and decisions |
| Where did this task leave off? | `(:Session)-[:WORKS_ON]->(:Intent)<-[:PART_OF]-(:Attempt)` across developers |

In a relational store, each of these would mean stitching rows together with recursive joins on every agent step. In FalkorDB each one is a single Cypher query, fast enough to run before every edit.

## Graph model

```
                       ┌──────────── code layer (indexer) ────────────┐
                       (File)-[:DEFINES]->(Function)-[:CALLS]->(Function)
                       (File)-[:IMPORTS]->(File)
                                  ▲            ▲            ▲
                          [:CLAIMS]│   [:TOUCHED]│  [:GOVERNS]│
(Developer)-[:OWNS]->(Intent)──────┘  (Attempt)──┘ (Decision)──┘   (Convention)-[:APPLIES_TO]->(File)
     │                  ▲                 ▲              ▲                 ▲
     └─[:RAN]->(Session)┴─[:WORKS_ON]     └─[:MADE]      └─[:DECIDED]      └─[:SET]  (Developer)
```

| Memory type | Nodes | Lifetime |
|---|---|---|
| **Episodic**: what happened | `Session`, `Attempt`, `Event` | Permanent history |
| **Semantic**: what is true | `Decision`, the code graph | Until superseded or re-indexed |
| **Procedural**: how we work | `Convention` | Repo-wide or scoped to files |
| **Coordination**: shared state | `Intent` + `CLAIMS` | Active → handed off → done |

Each team gets its own isolated graph (`hivemind_<team>`) in a single FalkorDB instance, using FalkorDB's multi-graph support.

## How agents use it

**1. MCP tools.** `hivemind serve` exposes 14 tools to any MCP client:

| Coordinate | Remember | Explain |
|---|---|---|
| `start_task` `claim_more` `check_conflicts` `finish_task` `handoff` `open_tasks` `resume_task` | `record_attempt` `record_decision` `add_convention` `get_context` | `why` `blast_radius` `who_knows` |

The server's instructions teach the agent the protocol: declare the task, read the context, record what happened, and hand off cleanly.

**2. A Claude Code hook that enforces the protocol.** Agents forget instructions, so `hivemind init` also installs a `PreToolUse` hook on `Edit`/`Write`/`MultiEdit`. The hook:

1. maps the edit's line range to the exact functions it touches,
2. auto-claims them, so teammates see the work even if the agent never called `start_task`,
3. denies the edit **once** if it conflicts with a teammate's work. The agent receives the call path and any failed attempts on the other side, and it tells the user. A retry goes through, so this is a warning, not a lock.

```
Hivemind: a teammate is working on code connected to this edit.
- alice is on "Add JWT support to auth tokens" (shop/tokens.py::validate_token).
  Path: checkout -> current_user -> validate_token. Your code calls code they are changing.
- Past failed attempt by alice on shop/tokens.py::validate_token:
  Replaced HMAC check with PyJWT decode (test_refresh_session fails: old tokens rejected).
```

**3. A live view for the humans.** `hivemind ui` shows the team graph as it changes, and lets people add to it:

| | |
|---|---|
| **Conflicts and work** | Each developer's claims ringed in their colour, and conflict paths animated in red with the exact call direction (`validate_token ← current_user ← checkout`). Focus on one developer to see only their work. |
| **Inspector** | Click a function to see its decisions, an attempt timeline, its impact grouped by hop, callers and callees, and who knows it. Its blast radius lights up on the graph. |
| **Path finder** | "How is A connected to B?" Uses FalkorDB `shortestPath`; if there's no direct chain, it finds the closest shared caller. |
| **Hotspots** | Functions ranked by FalkorDB's `algo.pageRank` (core code) or `algo.betweenness` (chokepoints that many call paths run through). |
| **Memory browser** | Search every decision, attempt and convention. Add decisions and conventions from the UI, so humans teach the agents too. |
| **Housekeeping** | Release stale tasks, switch between team graphs, filter the activity feed by person, and share links with `?team=&fn=`. |

Keyboard: <kbd>⌘K</kbd> search · <kbd>P</kbd> path · <kbd>F</kbd> fit · <kbd>D</kbd> decision · <kbd>Esc</kbd> clear. Dark and light themes, and it works on mobile.

## Quickstart

```bash
# 1. FalkorDB (one per team; everyone points at it)
docker compose up -d        # or: podman run -d -p 6379:6379 -p 3000:3000 docker.io/falkordb/falkordb

# 2. Install
uv sync

# 3. Watch the demo: three developers, three agents, one brain
uv run hivemind ui &        # http://127.0.0.1:8765 (⌘K to search)
uv run hivemind demo        # --step to advance with enter
```

### Use it on your repo

```bash
export FALKORDB_HOST=<shared host>  HIVEMIND_TEAM=myteam
uv run hivemind init /path/to/repo     # MCP server + hooks + CLAUDE.md + index
export HIVEMIND_DEV=alice              # on each developer's machine
cd /path/to/repo && claude
```

Re-run `hivemind index /path/to/repo` after big changes. Re-indexing keeps all memory attached to functions that still exist.

| Command | What it does |
|---|---|
| `hivemind init [path]` | Wire a repo's Claude Code to the team brain and index it |
| `hivemind index [path] [--reset]` | Index or re-index a Python repo |
| `hivemind serve [--dev] [--agent]` | MCP server (stdio, SSE or streamable HTTP) |
| `hivemind ui` | Live graph view |
| `hivemind demo [--step]` | Scripted three-developer scenario (wipes the team graph) |
| `hivemind status` | Open tasks as JSON |

## The demo scenario

`hivemind demo` plays this out on [`examples/shopapp`](examples/shopapp):

1. Team memory from last week: a convention, plus a decision on `tokens.py` ("stay HMAC until mobile v3", PR #42).
2. **Alice's** agent starts *Add JWT support*. It is warned of the decision before it touches anything.
3. Alice's agent tries PyJWT. The attempt fails, and the failure is recorded on `validate_token`.
4. **Bob's** agent, on another machine, starts *rate limiting on checkout*. It is warned: `checkout → current_user → validate_token`.
5. **Carol's** agent starts on refunds. It is warned that Bob's `checkout` calls her `refund`, and it picks up the team's DB convention.
6. Alice hands off to Bob. Bob's agent resumes the task, sees the PyJWT dead end, skips it, ships a dual-format fix and records why.
7. Anyone can ask `why("validate_token")` and get both decisions and the full attempt history, with names and sources.

## Development

```bash
uv run pytest      # integration tests against the local FalkorDB; each test gets its own graph
```

Layout: `indexer.py` (code → graph) · `brain.py` (all memory and coordination queries) · `server.py` (MCP) · `hooks.py` (Claude Code enforcement) · `ui.py` + `static/` (live view) · `demo.py`.

## Roadmap

- Tree-sitter indexing for TypeScript, Go and Rust
- Git-aware re-indexing on commit, and decisions linked to PRs automatically
- Stale-claim expiry and memory decay for old attempts
- Community detection over the call graph to suggest who should review a change
