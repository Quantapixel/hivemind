# ⬡ Hivemind

**A shared graph brain for every coding agent on a team.** Built on [FalkorDB](https://falkordb.com) for Graph Hacks, Track 02: Agent Memory and Coordination.

Five developers each run their own Claude Code, Codex or Kiro agent on the same repo. None of those agents knows what the others are doing. Agent A rewrites `validate_token()` while Agent B builds on its old behaviour. Agent C retries a fix that Agent D found breaks production yesterday. Every session starts from zero.

Hivemind puts every agent on one graph. It holds the code's call graph, who is changing what, what has been tried, and what the team decided. **Agents don't have to remember to use it:** hooks in Claude Code, Kiro and Codex record every edit (down to the function) and every test run as they happen, and the memory consolidates itself as it grows. Agents get three things from it:

- **Warnings before conflicts happen.** Before an edit, the agent learns that a teammate is changing code it depends on, and it gets the exact call path: `checkout → current_user → validate_token`. Git only catches the textual conflict after both people are done. Hivemind catches the *semantic* one before anyone writes code.
- **Memory of what failed, captured automatically.** When an agent changes code and the tests fail, that failure is attached to the exact functions it changed. It reaches the next agent that touches that function *or anything that calls it*, on any machine, in any later session, whichever agent that developer uses. When a later change makes the tests pass, the failure is marked fixed, along with the fix.
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

## Memory that writes itself

Every agent action that matters becomes team memory, without the agent calling a tool:

| The agent… | Hivemind records |
|---|---|
| receives a prompt | a task for the session, named after what the user asked |
| is about to edit | which functions are changing; if a teammate is on connected code, the edit is **blocked once** with the call path and any failures on the other side |
| finishes an edit | the file is re-indexed and each function's AST hash compared, so it knows **exactly** which functions changed or were added (formatting doesn't count) |
| runs tests after changing code | **fail:** a failed attempt on the changed functions, with the failing tests and error line. **pass:** resolves earlier failures on that code, recording who fixed them and how |
| stops | its own summary becomes the task's progress note, so handoffs are never empty |

### It consolidates instead of piling up

| When… | Hivemind… |
|---|---|
| the same failure happens again (same failing tests, or similar wording on the same code) | merges it: *"seen 3× by alice, bob"* |
| an agent explains a failure its hooks just captured | puts the explanation on the captured attempt instead of creating a second one |
| a success lands on code with open failures | marks them **resolved** and links the fix |
| a decision restates a current one on the same code | **supersedes** it; agents only see what's true now, and `why` keeps the history |
| a convention repeats an existing one | merges it |
| a developer's copy lacks a function another copy has | keeps it (with its memory), since it may only exist on someone else's branch |

## How agents connect

`hivemind init <repo> --agent claude,codex,kiro` wires up each agent in its own format:

| | Claude Code | Kiro CLI | Codex CLI |
|---|---|---|---|
| Installs | `.mcp.json`, hooks in `.claude/settings.local.json`, `CLAUDE.md` | `.kiro/agents/hivemind.json` (MCP + hooks), `AGENTS.md` | `codex mcp add` (tools pre-approved), hooks block in `~/.codex/config.toml`, `AGENTS.md` |
| MCP tools | ✅ verified in real sessions | ✅ verified in real sessions | ✅ verified in real sessions |
| Automatic capture | ✅ verified in real sessions | ✅ verified in real sessions | ⚠️ installed, but Codex only runs hooks after you trust them; not yet verified. Use `hivemind watch` (verified with a real Codex session) |
| Block a conflicting edit | ✅ verified in a real session | ✅ via exit code 2 (tested with real payloads) | after the hooks are trusted |

`hivemind watch` works with **any** tool, hooks or not. It notices file changes, records exactly which functions changed, claims them for you, and warns about conflicts (including a desktop notification).

Hooks find the project through `.hivemind/config.json`, so the Codex hooks (which are global) do nothing in other repos. Your name comes from `HIVEMIND_DEV`, or else your git `user.name`.

**MCP tools.** `hivemind serve` exposes 14 tools for what hooks can't see:

| Coordinate | Remember | Explain |
|---|---|---|
| `start_task` `claim_more` `check_conflicts` `finish_task` `handoff` `open_tasks` `resume_task` | `record_attempt` `record_decision` `add_convention` `get_context` | `why` `blast_radius` `who_knows` |

A conflicting edit is blocked like this:

```
Hivemind: a teammate is working on code connected to this edit.
- alice is on "Add JWT support to auth tokens" (shop/tokens.py::validate_token).
  Path: checkout -> current_user -> validate_token. Your code calls code they are changing.
- Past failed attempt by alice on shop/tokens.py::validate_token:
  Replaced HMAC check with PyJWT decode (test_refresh_session fails: old tokens rejected).
```

The agent tells the user, and a retry goes through. It's a warning, not a lock.

### Tested with real agents

Three "machines" (separate clones) shared one FalkorDB:

1. **Kira (Kiro)** was asked to add token revocation; the prompt didn't mention Hivemind. Hooks recorded the task from her prompt, `revoke_token` **added** and `validate_token` **changed** (from AST diffs), the passing test run, and Kiro's summary as the progress note.
2. **Carl (Claude Code)** was asked to add token scopes. His agent said, unprompted: *"The revocation check from kira's recent change is untouched."* It knew about work done by a different developer using a different agent.
3. **Cody (Codex)** read the shared task board through the MCP tools and saw Kira's and his own work. `hivemind watch` captured his edits.

## Live view

`hivemind ui` draws the codebase as an architecture map: functions are labelled pills grouped into per-file regions, tasks are pinned to the code they claim, and memory shows as badges. People can add to the memory from here too.

| | |
|---|---|
| **Who's doing what** | Every person with the agents they use (Claude Code, Kiro, Codex), tasks with the agent's latest progress note, and each function's edit history. |
| **Conflicts and work** | Each developer's claims ringed in their colour, and conflict paths animated in red with the exact call direction (`validate_token ← current_user ← checkout`). Focus on one developer to see only their work. |
| **Inspector** | Click a function to see its decisions, an attempt timeline, its impact grouped by hop, callers and callees, and who knows it. Its blast radius lights up on the graph. |
| **Path finder** | "How is A connected to B?" Uses FalkorDB `shortestPath`; if there's no direct chain, it finds the closest shared caller. |
| **Hotspots** | Functions ranked by FalkorDB's `algo.pageRank` (core code) or `algo.betweenness` (chokepoints that many call paths run through). |
| **Memory browser** | Search every decision, attempt and convention: merged repeats (×N), captured vs explained attempts, fixed failures struck through with the fix, superseded decisions dimmed. Add decisions and conventions from the UI, so humans teach the agents too. |
| **Housekeeping** | Release stale tasks, switch between team graphs, filter the activity feed by person, and share links with `?team=&fn=`. |

Toggle layers (calls, work, memory, tests) and colour by file or by risk. Keyboard: <kbd>⌘K</kbd> search · <kbd>P</kbd> path · <kbd>F</kbd> fit · <kbd>D</kbd> decision · <kbd>Esc</kbd> clear. Dark and light themes, and it works on mobile.

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
uv run hivemind init /path/to/repo --agent claude,kiro,codex   # whichever you use
cd /path/to/repo && claude                     # or: kiro-cli chat --agent hivemind / codex
uv run hivemind watch /path/to/repo            # optional: capture edits from any other tool
```

Edits are indexed as they happen. Run `hivemind index` after pulling big changes.

| Command | What it does |
|---|---|
| `hivemind init [path] --agent claude,kiro,codex` | Wire a repo's agents to the team brain and index it |
| `hivemind watch [path]` | Record edits made by any tool, warn about conflicts |
| `hivemind hook --agent X` | The hook entry point the agents call (installed by `init`) |
| `hivemind index [path] [--reset]` | Index or re-index a Python repo |
| `hivemind serve [--dev] [--agent]` | MCP server (stdio, SSE or streamable HTTP) |
| `hivemind ui` | Live graph view |
| `hivemind demo [--step]` | Scripted three-developer scenario (wipes the team graph) |
| `hivemind status` | Open tasks as JSON |

## The demo scenario

`hivemind demo` plays this out on [`examples/shopapp`](examples/shopapp):

1. Team memory from last week: a convention, plus a decision on `tokens.py` ("stay HMAC until mobile v3", PR #42).
2. **Alice's** agent starts *Add JWT support*. It is warned of the decision before it touches anything.
3. Alice's agent edits `validate_token` and runs the tests; they fail. Hooks record the failure on `validate_token`, and her agent's explanation ("replaced HMAC with PyJWT") is merged into it.
4. **Bob's** agent, on another machine, starts *rate limiting on checkout*. It is warned: `checkout → current_user → validate_token`.
5. **Carol's** agent starts on refunds. It is warned that Bob's `checkout` calls her `refund`, and it picks up the team's DB convention.
6. Alice hands off to Bob. Bob's agent resumes the task and sees the PyJWT dead end. It ships a dual-format fix; the passing tests **resolve** Alice's failure, linked to the fix.
7. Anyone can ask `why("validate_token")` and get both decisions and the full attempt history, with names and sources.

## Development

```bash
uv run pytest      # integration tests against the local FalkorDB; each test gets its own graph
```

Layout: `indexer.py` (code → graph, AST hashes, incremental) · `brain.py` (memory, consolidation, coordination) · `capture.py` (normalizes Claude/Kiro/Codex hook payloads, detects test runs) · `hooks.py` · `server.py` (MCP) · `setup.py` (`init`) · `watch.py` · `ui.py` + `static/` · `demo.py`. `tests/fixtures/hooks` holds payloads captured from real agent sessions.

## Roadmap

- Tree-sitter indexing for TypeScript, Go and Rust
- Git-aware re-indexing on commit, and decisions linked to PRs automatically
- Verify Codex hooks once trusted interactively; stale-claim expiry and memory decay for old attempts
- Semantic (embedding) matching for consolidation via FalkorDB's vector index; word overlap today
- Community detection over the call graph to suggest who should review a change
