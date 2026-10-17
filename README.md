# ⬡ Memofull

**A shared graph brain for every coding agent on a team.** Built on [FalkorDB](https://falkordb.com) for [Graph Hacks: Context for AI Agents](https://www.wemakedevs.org/hackathons/falkordb) (WeMakeDevs × FalkorDB, Oct 15–18 2026).

| | |
|---|---|
| **▶ Live demo** | **[memofull-live-demo.vercel.app](https://memofull-live-demo.vercel.app)**: press Play, no sign-up |
| **Demo videos** | [Memofull demo (3:14)](docs/videos/memofull-demo.mp4) · [Walkthrough of the live demo (2:49)](docs/videos/memofull-demo-walkthrough.mp4) |
| **Track** | Agent Memory and Coordination (Track 02) |
| **Built with** | FalkorDB Cloud · Python · MCP · Claude Code, Kiro and Codex hooks · Vercel |

Five developers each run their own Claude Code, Codex or Kiro agent on the same repo. None of those agents knows what the others are doing. Agent A rewrites `validate_token()` while Agent B builds on its old behaviour. Agent C retries a fix that Agent D found breaks production yesterday. Every session starts from zero.

Memofull puts every agent on one graph. It holds the code's call graph, who is changing what, what has been tried, and what the team decided. **Agents don't have to remember to use it:** hooks in Claude Code, Kiro and Codex record every edit (down to the function) and every test run as they happen, and the memory consolidates itself as it grows. Agents get three things from it:

- **Warnings before conflicts happen.** Before an edit, the agent learns that a teammate is changing code it depends on, and it gets the exact call path: `checkout → current_user → validate_token`. Git only catches the textual conflict after both people are done. Memofull catches the *semantic* one before anyone writes code.
- **Memory of what failed, captured automatically.** When an agent changes code and the tests fail, that failure is attached to the exact functions it changed. It reaches the next agent that touches that function *or anything that calls it*, on any machine, in any later session, whichever agent that developer uses. When a later change makes the tests pass, the failure is marked fixed, along with the fix.
- **Clean handoffs.** Alice's agent stops mid-task, and Bob's agent resumes it the next morning with every session, attempt and note.

**▶ [Live demo](https://memofull-live-demo.vercel.app)**: press **Play** to watch five developers using Claude Code, Kiro and Codex work on one project through the shared memory: conflicts caught before anyone writes code, a failure that two attempts hit and that gets merged, a handoff, a fix that resolves the failure, and a decision that supersedes an older one. No sign-up needed.

![live view](docs/live-view.png)

## Get started

### Join a team (3 steps)

Your teammate sends you a **dashboard link** and an **access code** (they run `memofull invite` to get both). You'll also need the team's project on your machine (`git clone` it) and a coding agent: **Claude Code**, **Kiro CLI** or **Codex CLI**, logged in to your own account. Works on macOS and Linux; on Windows, use WSL.

```bash
# 1. Install uv (skip if you have it), then open a new terminal
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install Memofull
uv tool install git+https://github.com/Quantapixel/memofull

# 3. Inside your clone of the team's project
memofull join https://your-team-dashboard.vercel.app
#    Team access code: ********
```

`join` connects you to the team database, uses your git name (or asks for one), finds every coding agent you have installed, connects them to this project and indexes the code. Then start your agent in that folder and work as usual:

| Agent | Start it with | Notes |
|---|---|---|
| Claude Code | `claude` | Approve the **memofull** MCP server when asked |
| Kiro | `kiro-cli chat --agent memofull` | |
| Codex | `codex` | Approve the Memofull hooks when asked; until you do, keep `memofull watch .` running in a second terminal |

Open the dashboard link and enter the same access code to see the team live.

**Something not working?** Run `memofull doctor` in the project folder. It checks every piece and prints the fix for anything that's wrong.

| Problem | Fix |
|---|---|
| `That access code was not accepted` | Ask your teammate for the current code |
| `Could not reach` the database | A free FalkorDB Cloud instance sleeps after a day without use; whoever runs the team wakes it in the FalkorDB console |
| You show up under the wrong name | `git config --global user.name "Your Name"`, then `memofull join` again |
| `not inside a git repository` | `cd` into your clone of the project first |
| `memofull: command not found` | Open a new terminal after installing, or run `uv tool update-shell` |

**What gets shared:** only work inside that project folder: your prompts, your agent's summaries, which functions changed and test results. Your code itself is never uploaded, only function names and how they call each other.

### Start a team (about 15 minutes, once)

1. **A database everyone can reach.** Create a free instance on [FalkorDB Cloud](https://app.falkordb.cloud/signup): pick a region near your team and set a password made of letters and numbers. Free instances sleep after a day unused, are deleted after 7 days, and don't use TLS: fine for trying it out; use a paid tier or your own server for real work (Memofull supports `rediss://` URLs).
2. **Install and connect:**
   ```bash
   uv tool install git+https://github.com/Quantapixel/memofull
   memofull --team myteam connect 'redis://falkordb:PASSWORD@HOST:PORT'
   ```
   The URL is saved privately to `~/.config/memofull/config.json`, never to the repo.
3. **Connect your project:** `cd your-project && memofull init .`
4. **Host the dashboard on Vercel** (needs the [Vercel CLI](https://vercel.com/docs/cli), logged in). From a clone of this repo:
   ```bash
   FALKORDB_URL='redis://falkordb:PASSWORD@HOST:PORT' \
   MEMOFULL_DASHBOARD_KEY="$(openssl rand -base64 12)" MEMOFULL_TEAMS=myteam \
   scripts/deploy-dashboard.sh
   ```
   Set `"regions"` in `deploy/vercel/vercel.json` next to your database (`bom1` for Mumbai, `iad1` for US East...), or every refresh crosses the world. If Vercel's own login wall blocks visitors, turn it off with `vercel project protection disable memofull-dashboard --sso` (the dashboard has its own access code). The script saves the dashboard link and code privately so `invite` can print them.
5. **Invite people:** `memofull invite` prints the three steps above with your dashboard link and code. Send it privately, and give them access to the project repo.

## Why a graph

Every question Memofull answers is a question about connections:

| Question | Graph work |
|---|---|
| Is anyone working on code that mine calls, or that calls mine? | Variable-length `CALLS` traversal from my functions to other developers' `CLAIMS`, returning the shortest path |
| What has been tried on this code or its neighbours? | `(:Attempt)-[:TOUCHED]->(:Function)` within one hop of the call graph |
| Why is this code like this? | `(:Developer)-[:DECIDED]->(:Decision)-[:GOVERNS]->(:Function\|:File)` |
| What breaks if I change it? | Transitive callers, `(caller)-[:CALLS*1..3]->(target)` |
| Who knows this code? | Developers linked to it through claims, attempts and decisions |
| Where did this task leave off? | `(:Session)-[:WORKS_ON]->(:Intent)<-[:PART_OF]-(:Attempt)` across developers |

In a relational store, each of these would mean stitching rows together with recursive joins on every agent step. In FalkorDB each one is a single Cypher query, fast enough to run before every edit.

## Graph data model

One FalkorDB graph per team (`memofull_<team>`, using FalkorDB's multi-graph support). The **code layer** is built from the repo; everything else is **team memory** attached to it.

```
CODE LAYER (indexer.py, re-indexed per file as agents edit)
  (File)-[:DEFINES]->(Function)-[:CALLS]->(Function)        Function {qname, name, file, lineno, hash, calls}
  (File)-[:IMPORTS]->(File)

WHO IS DOING WHAT (coordination)
  (Developer)-[:RAN]->(Session {agent})-[:WORKS_ON]->(Intent {description, status, progress})
  (Developer)-[:OWNS]->(Intent)                               moves to the new owner on handoff
  (Intent)-[:CLAIMS]->(Function)                              what a task is changing right now

WHAT HAPPENED (episodic)
  (Session)-[:MADE]->(Change {added, changed, removed})-[:CHANGED]->(Function)
  (Session)-[:MADE]->(Attempt {outcome, reason, signature, count, devs})-[:TOUCHED]->(Function)
  (Attempt)-[:RESOLVES]->(Attempt)                            a later success fixed this failure
  (Attempt)-[:PART_OF]->(Intent)
  (Session)-[:LOGGED]->(Event)                                activity feed

WHAT IS TRUE / HOW WE WORK (semantic, procedural)
  (Developer)-[:DECIDED]->(Decision)-[:GOVERNS]->(Function|File)
  (Decision)-[:SUPERSEDES]->(Decision)                        only the newest is shown to agents
  (Developer)-[:SET]->(Convention)-[:APPLIES_TO]->(File)
```

| Memory type | Nodes | How it changes over time |
|---|---|---|
| **Episodic**: what happened | `Session`, `Change`, `Attempt`, `Event` | Repeats merge (`count`, `devs`); failures get `RESOLVES` links when fixed |
| **Semantic**: what is true | `Decision`, the code graph | Restated decisions `SUPERSEDE` older ones; code re-indexed per edit by AST hash |
| **Procedural**: how we work | `Convention` | Duplicates merge |
| **Coordination**: shared state | `Intent` + `CLAIMS` | Active → handed off → resumed → done |

## Architecture

```
 Developer A (anywhere)                Developer B (anywhere)
 Claude Code / Kiro / Codex            Claude Code / Kiro / Codex
   ├─ hooks: memofull hook               ├─ hooks
   └─ MCP:   memofull serve              └─ MCP
          │   Cypher over redis(s)://          │
          ▼                                    ▼
   ┌──────────────── FalkorDB Cloud ────────────────┐
   │  memofull_<team>   memofull_<other team>  ...  │
   └───────────────────────▲────────────────────────┘
                           │
        Vercel: dashboard (access code) · public demo (simulation)
```

Every agent action goes straight to the graph. No server of ours sits in between, so there's nothing else to run. Timestamps come from the database server's clock, because laptops disagree.

## Cypher queries and graph algorithms it depends on

Every feature is one or two queries. These are the ones the product can't work without (simplified only by removing parameters):

**1. Conflict detection: is anyone changing code mine calls, or code that calls mine?** (`brain.py: conflicts`). This runs before every edit, in both directions separately so functions that merely share a caller don't count:
```cypher
MATCH (t:Function) WHERE t.qname IN $mine
MATCH p = (t)-[:CALLS*1..3]->(o:Function)<-[:CLAIMS]-(i:Intent {status: 'active'})   // or (t)<-[:CALLS*1..3]-(o)
WHERE i.dev <> $me
RETURN t.qname, o.qname, i.dev, i.description, [n IN nodes(p) | n.qname] AS path
```

**2. Context before editing: what has been tried on this code or its neighbours?** (`brain.py: context`)
```cypher
MATCH (t:Function) WHERE t.qname IN $q
MATCH (t)-[:CALLS*0..1]-(fn:Function)<-[:TOUCHED]-(a:Attempt)
WITH a, collect(DISTINCT fn.qname) AS fns
RETURN a.summary, a.outcome, a.reason, a.count, a.devs, a.resolved, a.resolved_summary
```

**3. A success resolves earlier failures** (`brain.py: record_attempt`)
```cypher
MATCH (f:Attempt {outcome: 'failed'})-[:TOUCHED]->(fn:Function)
WHERE fn.qname IN $q AND coalesce(f.resolved, false) = false
WITH DISTINCT f MATCH (a:Attempt {id: $fix})
CREATE (a)-[:RESOLVES]->(f) SET f.resolved = true, f.resolved_dev = $dev
```
Duplicates are merged first: a new attempt with the same outcome on the same functions and the same failing tests (or similar wording) increments `count` instead of creating a node.

**4. Decisions supersede decisions** (`brain.py: record_decision`)
```cypher
MATCH (x:Decision)-[:GOVERNS]->(n)
WHERE x.superseded = false AND ((n:Function AND n.qname IN $q) OR (n:File AND n.path IN $files))
...
CREATE (new)-[:SUPERSEDES]->(old) SET old.superseded = true
```

**5. Blast radius: what breaks if this changes?** (`brain.py: blast_radius`)
```cypher
MATCH (t:Function {qname: $q})
MATCH p = (caller:Function)-[:CALLS*1..3]->(t)
RETURN caller.qname, min(length(p)) AS distance
```

**6. Incremental re-index after an agent edits a file** (`indexer.py: index_file`). Each function's AST hash says what really changed, and calls are re-resolved by name:
```cypher
MATCH (a:Function {file: $file}) UNWIND a.calls AS name
MATCH (b:Function {name: name}) WHERE b <> a
WITH a, collect(b) AS targets, [t IN collect(b) WHERE t.file = a.file] AS local
UNWIND CASE WHEN size(local) > 0 THEN local ELSE targets END AS t
MERGE (a)-[:CALLS]->(t)
```

**7. Graph algorithms in the dashboard** (`ui.py`)
```cypher
CALL algo.pageRank('Function', 'CALLS') YIELD node, score                       // "core" functions
CALL algo.betweenness({nodeLabels: ['Function'], relationshipTypes: ['CALLS']})  // chokepoints
RETURN [n IN nodes(shortestPath((x)-[:CALLS*]->(y))) | n.qname]                 // path finder
```

**Remove FalkorDB and nothing works:** conflicts, context, consolidation, handoffs and the dashboard are all traversals over this graph.

## Memory that writes itself

Every agent action that matters becomes team memory, without the agent calling a tool:

| The agent… | Memofull records |
|---|---|
| receives a prompt | a task for the session, named after what the user asked |
| is about to edit | which functions are changing; if a teammate is on connected code, the edit is **blocked once** with the call path and any failures on the other side |
| finishes an edit | the file is re-indexed and each function's AST hash compared, so it knows **exactly** which functions changed or were added (formatting doesn't count) |
| runs tests after changing code | **fail:** a failed attempt on the changed functions, with the failing tests and error line. **pass:** resolves earlier failures on that code, recording who fixed them and how |
| stops | its own summary becomes the task's progress note, so handoffs are never empty |

### It consolidates instead of piling up

| When… | Memofull… |
|---|---|
| the same failure happens again (same failing tests, or similar wording on the same code) | merges it: *"seen 3× by alice, bob"* |
| an agent explains a failure its hooks just captured | puts the explanation on the captured attempt instead of creating a second one |
| a success lands on code with open failures | marks them **resolved** and links the fix |
| a decision restates a current one on the same code | **supersedes** it; agents only see what's true now, and `why` keeps the history |
| a convention repeats an existing one | merges it |
| a developer's copy lacks a function another copy has | keeps it (with its memory), since it may only exist on someone else's branch |

## How agents connect

`memofull init <repo> --agent claude,codex,kiro` wires up each agent in its own format:

| | Claude Code | Kiro CLI | Codex CLI |
|---|---|---|---|
| Installs | `.mcp.json`, hooks in `.claude/settings.local.json`, `CLAUDE.md` | `.kiro/agents/memofull.json` (MCP + hooks), `AGENTS.md` | `codex mcp add` (tools pre-approved), hooks block in `~/.codex/config.toml`, `AGENTS.md` |
| MCP tools | ✅ verified in real sessions | ✅ verified in real sessions | ✅ verified in real sessions |
| Automatic capture | ✅ verified in real sessions | ✅ verified in real sessions | ⚠️ installed, but Codex only runs hooks after you trust them; not yet verified. Use `memofull watch` (verified with a real Codex session) |
| Block a conflicting edit | ✅ verified in a real session | ✅ via exit code 2 (tested with real payloads) | after the hooks are trusted |

`memofull watch` works with **any** tool, hooks or not. It notices file changes, records exactly which functions changed, claims them for you, and warns about conflicts (including a desktop notification).

Hooks find the project through `.memofull/config.json`, so the Codex hooks (which are global) do nothing in other repos. Your name comes from `MEMOFULL_DEV`, or else your git `user.name`.

**MCP tools.** `memofull serve` exposes 14 tools for what hooks can't see:

| Coordinate | Remember | Explain |
|---|---|---|
| `start_task` `claim_more` `check_conflicts` `finish_task` `handoff` `open_tasks` `resume_task` | `record_attempt` `record_decision` `add_convention` `get_context` | `why` `blast_radius` `who_knows` |

A conflicting edit is blocked like this:

```
Memofull: a teammate is working on code connected to this edit.
- alice is on "Add JWT support to auth tokens" (shop/tokens.py::validate_token).
  Path: checkout -> current_user -> validate_token. Your code calls code they are changing.
- Past failed attempt by alice on shop/tokens.py::validate_token:
  Replaced HMAC check with PyJWT decode (test_refresh_session fails: old tokens rejected).
```

The agent tells the user, and a retry goes through. It's a warning, not a lock.

### Tested with real agents

Three "machines" (separate clones) shared one FalkorDB:

1. **Kira (Kiro)** was asked to add token revocation; the prompt didn't mention Memofull. Hooks recorded the task from her prompt, `revoke_token` **added** and `validate_token` **changed** (from AST diffs), the passing test run, and Kiro's summary as the progress note.
2. **Carl (Claude Code)** was asked to add token scopes. His agent said, unprompted: *"The revocation check from kira's recent change is untouched."* It knew about work done by a different developer using a different agent.
3. **Cody (Codex)** read the shared task board through the MCP tools and saw Kira's and his own work. `memofull watch` captured his edits.

## Live view

`memofull ui` draws the codebase as an architecture map: functions are labelled pills grouped into per-file regions, tasks are pinned to the code they claim, and memory shows as badges. People can add to the memory from here too.

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

## Commands

| Command | What it does |
|---|---|
| `memofull join <dashboard-url>` | Join a team: asks for the access code, connects, wires up every agent you have installed, indexes the project |
| `memofull doctor` | Checks the database, the project, your name, each agent and the hooks; says what to fix |
| `memofull invite` | Prints the three steps to send a teammate |
| `memofull connect <url>` | Save a database URL privately (`~/.config/memofull`), for whoever runs the team |
| `memofull init [path] [--agent claude,kiro,codex]` | Wire a repo's agents (default: all installed) and index it |
| `memofull watch [path]` | Record edits made by any tool, warn about conflicts |
| `memofull index [path] [--reset]` | Index or re-index a Python repo |
| `memofull ui` | Local live view (the hosted one is on Vercel) |
| `memofull demo [--step]` | Scripted three-developer scenario (wipes that team's graph) |
| `memofull serve`, `memofull hook` | MCP server and hook entry point; `init` installs both, you never run them by hand |
| `memofull status` | Open tasks as JSON |

## Host a public demo

The live demo is the same dashboard in public mode: no access code, limited to the teams you name, and it never hands out the database connection. It plays the scripted story from `src/memofull/simulation.py` one step per click, with each step writing to its own `simulation` team graph through the same calls that agents' hooks make.

```bash
FALKORDB_URL='redis://...' MEMOFULL_PUBLIC=1 MEMOFULL_TEAMS=simulation MEMOFULL_SIM_TEAM=simulation \
VERCEL_PROJECT=memofull-live-demo scripts/deploy-dashboard.sh
vercel project protection disable memofull-live-demo --sso   # if Vercel asks visitors to log in
```

## Try the demo locally

No accounts needed: a local FalkorDB, the live view, and a scripted story with three developers.

```bash
git clone https://github.com/Quantapixel/memofull && cd memofull && uv sync
docker compose up -d                # or: podman run -d -p 6379:6379 docker.io/falkordb/falkordb
uv run memofull ui &                # http://127.0.0.1:8765 (⌘K to search)
uv run memofull demo --step         # press Enter to advance
```

## The local demo scenario

`memofull demo` plays this out on [`examples/shopapp`](examples/shopapp):

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

## How this was built (AI disclosure)

As the hackathon rules require: Memofull was built during the hackathon window (first commit on Oct 16, 2026) with **Claude Code** (Anthropic) as an AI coding assistant. Jaskaran Singh came up with the idea (agents on different machines sharing one memory of a codebase) and set the product direction: automatic capture across Claude Code, Kiro and Codex, a cloud-hosted setup with a Vercel dashboard, one-command onboarding and the public simulation. He also reviewed the results at each step. Much of the code was written by the assistant under that direction. It was verified with automated tests against a real FalkorDB (63 tests) and with real Claude Code, Kiro and Codex sessions (see *Tested with real agents*). All example data is synthetic: [`examples/shopapp`](examples/shopapp) is a made-up shop backend.

## Roadmap

- Tree-sitter indexing for TypeScript, Go and Rust
- Git-aware re-indexing on commit, and decisions linked to PRs automatically
- Verify Codex hooks once trusted interactively; stale-claim expiry and memory decay for old attempts
- Semantic (embedding) matching for consolidation via FalkorDB's vector index; word overlap today
- Community detection over the call graph to suggest who should review a change
