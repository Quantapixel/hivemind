---
title: "My AI agent didn't know what yours was doing, so I gave them one shared brain"
subtitle: "Building Hivemind: a team memory for Claude Code, Kiro and Codex, stored as a FalkorDB graph"
tags: falkordb, graph-database, ai-agents, hackathon, graphhacks
cover: https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/cover.png
---

![Hivemind: a shared graph brain for every coding agent on a team](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/cover.png)

> **TL;DR:** Coding agents on the same team can't see each other and forget everything between sessions. **Hivemind** gives all of them one shared memory: a FalkorDB graph that holds the code's call graph plus who is changing what, what was tried, what failed and what the team decided. Agents are warned *before* they collide, failures follow the code to the next person, and handoffs carry the full history. Nobody has to remember to use it; hooks capture everything automatically.
>
> ▶ **Try it in your browser:** [hivemind-live-demo.vercel.app](https://hivemind-live-demo.vercel.app) (press **Play**, no sign-up)

---

## A Tuesday afternoon with five agents

Picture a small team working on an online shop. Five developers each have their own AI coding agent open in a terminal:

- **Priya**, the tech lead, uses Claude Code.
- **Marco** uses Kiro.
- **Aisha** uses Codex.
- **Ken** uses Claude Code.
- **Lena** uses Kiro.

At 2 p.m., Marco asks Kiro: *"Add JWT support to auth tokens."* Kiro opens `shop/tokens.py` and starts rewriting `validate_token()`.

At 2:10, Aisha asks Codex: *"Add rate limiting to checkout."* Codex opens `shop/orders.py`, a completely different file.

What neither of them knows: `checkout()` calls `current_user()`, and `current_user()` calls `validate_token()`. **Aisha is building on code that Marco is rewriting at that moment.**

Git won't say anything. They're editing different files, so there's no merge conflict. Git only catches *textual* conflicts, and only after both people have finished. This is a *semantic* conflict, and it turns into a bug a week later.

Then it gets worse:

- **2:30.** Marco's first idea, swapping the HMAC check for a PyJWT decode, breaks `test_refresh_session`: users with old tokens get logged out. Kiro tries a variation, and it fails the same way. That lesson now lives only in Marco's chat window.
- **6:00.** Marco goes home halfway through. The next morning Lena picks up the JWT task. Her agent starts **from zero**, and the first thing it's likely to try is… a PyJWT decode.
- **Somewhere in all this,** nobody's agent knows that Priya decided last month that *"tokens stay HMAC until the mobile v3 app ships"*, because mobile v2 parses the token by hand.

Each of these agents is brilliant on its own. Together they act like five people who've never met, each with amnesia.

![Without shared memory vs with Hivemind](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/problem.png)

That's the problem I picked for **[Graph Hacks: Context for AI Agents](https://www.wemakedevs.org/hackathons/falkordb)** (WeMakeDevs × FalkorDB), in the *Agent Memory and Coordination* track.

## Why the usual fixes don't work

Before building anything, I went through what teams do today:

| What teams try | Why it falls short |
|---|---|
| **Git and PR reviews** | Only catches textual conflicts, and only after the work is done |
| **`CLAUDE.md` / `AGENTS.md` files** | Static. Someone has to write and update them by hand, and they don't know what's happening *right now* |
| **Each agent's own memory** | Stays on one laptop, in one tool. Kiro's memory doesn't help Codex |
| **Slack: "hey, is anyone touching auth?"** | Works until someone forgets to ask, and agents never ask |

What I wanted was memory that is:

1. **Shared** across people, machines *and* different agent tools
2. **Live**, so it knows what's being changed right now, not last week
3. **Attached to the code**, so a failure on `validate_token` reaches whoever touches `validate_token` next
4. **Automatic**, so it doesn't depend on anyone (human or agent) remembering to write it down

## The key idea: memory belongs to the code, not the chat

Here's what made the design click for me: **every memory worth sharing is about a specific piece of code.**

- "Marco is changing `validate_token`."
- "A PyJWT decode on `validate_token` broke the refresh test."
- "`tokens.py` stays HMAC until mobile v3."

If memory is attached to functions, and functions are connected by calls, then "what should my agent know before editing `checkout`?" becomes a question about **what's connected to `checkout`**. That's a graph question.

## Why a graph, and why FalkorDB

When I listed every question an agent needs answered, each one turned out to be about connections:

| The agent wants to know… | On a graph, that is… |
|---|---|
| Is anyone changing code that mine calls, or that calls mine? | A path along `CALLS` edges to a teammate's claim |
| What has been tried on this code or its neighbours? | `Attempt` nodes within one hop of the function |
| Why is this code like this? | `Developer → DECIDED → Decision → GOVERNS → Function` |
| What breaks if I change it? | All callers, up to 3 hops away |
| Who knows this code? | Developers linked through claims, attempts and decisions |
| Where did this task leave off? | `Session → WORKS_ON → Intent ← PART_OF ← Attempt` |

In a relational database, every one of these needs recursive joins, and they would run on *every agent step*. In a graph, each is a single query.

I used **FalkorDB** for a few concrete reasons:

- **Cypher with variable-length paths.** `[:CALLS*1..3]` expresses "up to three calls away" in a few characters. Conflict detection is built on it.
- **It's fast enough to run before every single edit.** That's the bar. If the check is slow, nobody keeps it turned on.
- **Multi-graph.** Each team gets its own graph (`hivemind_shop`, `hivemind_payments`…) in one database.
- **Built-in graph algorithms.** `algo.pageRank`, `algo.betweenness` and `shortestPath` power the dashboard, with nothing extra to install.
- **It speaks the Redis protocol, and there's a free cloud tier.** Every agent on every laptop can connect directly with a URL. No backend of my own needed.

## The data model

The graph has two layers. The **code layer** is built from the repo. The **team memory** layer holds everything people and agents did, attached to the exact functions it's about.

![The Hivemind graph data model](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/graph-model.png)

Reading the picture from top left:

- Marco **RAN** a Kiro session, which **WORKS_ON** the task *"Add JWT support"*. That task **CLAIMS** `validate_token`, so the graph knows who is changing it right now.
- The session **MADE** an attempt, *"PyJWT decode"*, that failed twice. It **TOUCHED** `validate_token`, so the failure is stored on the code, not in a chat log.
- Later, Lena's passing attempt **RESOLVES** that failure.
- Lena **DECIDED** *"Accept HMAC + JWT"*. That decision **GOVERNS** `validate_token` and **SUPERSEDES** Priya's old one.

I found it helpful to think of the memory in four kinds, borrowed loosely from how people describe human memory:

| Kind | In plain words | Nodes | How it changes over time |
|---|---|---|---|
| **Episodic** | What happened | `Session`, `Change`, `Attempt`, `Event` | Repeats merge; failures get `RESOLVES` links when fixed |
| **Semantic** | What is true | `Decision`, the code graph | New decisions supersede old ones; code re-indexed on every edit |
| **Procedural** | How we work here | `Convention` | Duplicates merge |
| **Coordination** | Who's doing what right now | `Intent` + `CLAIMS` | Active → handed off → resumed → done |

## Building the code layer

Everything depends on an accurate call graph, so this is where I started.

**Indexing.** Hivemind parses every Python file with Python's built-in `ast` module. It records each function, the functions it calls, and the imports, then writes `File`, `Function`, `DEFINES`, `CALLS` and `IMPORTS` into the graph in batches with `UNWIND` + `MERGE`.

**Knowing exactly what changed.** Every function gets a *structural hash*: a hash of its syntax tree without line numbers. After an agent edits a file, Hivemind re-indexes just that file and compares hashes:

- different hash → the function **changed**
- new name → **added**
- gone → **removed**

So a comment, blank line or reformat doesn't count as a change. A real logic change always does, whichever agent made it.

**Resolving calls.** When a function calls `validate_token`, which `validate_token` is it? Hivemind prefers a definition in the same file and otherwise links to any function with that name:

```cypher
MATCH (a:Function {file: $file})
UNWIND a.calls AS name
MATCH (b:Function {name: name}) WHERE b <> a
WITH a, collect(b) AS targets, [t IN collect(b) WHERE t.file = a.file] AS local
UNWIND CASE WHEN size(local) > 0 THEN local ELSE targets END AS t
MERGE (a)-[:CALLS]->(t)
```

It's not a full type checker, but it's right for the vast majority of real code, and it's fast enough to run after every edit.

## Feature 1: catching conflicts before anyone writes code

This is the feature I'm proudest of. Before every edit, Hivemind asks: *"Starting from the functions I'm about to change, can I reach a function someone else has claimed within 3 calls?"*

![How Hivemind catches a conflict before anyone writes code](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/conflict.png)

The core query:

```cypher
MATCH (t:Function) WHERE t.qname IN $mine
MATCH p = (t)-[:CALLS*1..3]->(o:Function)<-[:CLAIMS]-(i:Intent {status: 'active'})
WHERE i.dev <> $me
RETURN t.qname, o.qname, i.dev, i.description,
       [n IN nodes(p) | n.qname] AS path
```

Line by line:

- `(t)-[:CALLS*1..3]->(o)` walks **1 to 3** call edges from my function.
- `<-[:CLAIMS]-(i:Intent {status: 'active'})` checks whether someone has an active task on the function it reaches.
- `i.dev <> $me` skips my own tasks.
- `nodes(p)` returns the **actual path**, so the agent can explain the conflict instead of just saying "something's wrong."

Hivemind runs this in **three separate directions**:

1. **Same function:** we're both editing `validate_token`.
2. **My code calls theirs:** `checkout → current_user → validate_token`.
3. **Their code calls mine:** I'm changing something their function depends on.

Each one gives the agent a different message. For each pair, it keeps only the *shortest* path, because that's the clearest explanation.

**Block once, then step aside.** When the hook finds a conflict, it pauses the edit *once* and gives the agent the explanation. The agent tells the developer, who decides what to do. If they retry, the edit goes through, because Hivemind records that the warning was acknowledged (as a small node in the graph, naturally). It's a heads-up, not a lock. Locks would make people turn it off.

Here's the moment in the live demo when Aisha starts her task and the conflict appears on the map:

![Live demo: a conflict appears the moment Aisha starts](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/conflict-demo.gif)

## Feature 2: memory that writes itself

Early on I made a decision that shaped everything else: **an agent should never have to remember to update the memory.** (Why this matters is the first item in *What I learned*.)

Claude Code, Kiro and Codex all support **hooks**: small commands the agent runs at certain moments. Hivemind installs one hook that listens to all of them:

| When the agent… | Hivemind… |
|---|---|
| starts a session | gives it a short team brief: who's working on what, and unresolved failures |
| receives a prompt | creates a task for the session, named after what the user asked |
| is about to edit | claims the functions being edited and runs the conflict check |
| finishes an edit | re-indexes the file and records exactly which functions changed (by AST hash) |
| runs tests | **fail:** records a failed attempt on the changed functions, with the failing test and error line. **pass:** resolves earlier failures on that code |
| stops | saves the agent's own summary as the task's progress note |

The three agents send these events in **different formats**. Claude Code uses `PascalCase` event names, Kiro uses `camelCase` with different tool names (`fs_write`, `execute_bash`), and Codex's tool names vary by version. A small normalization layer turns all of them into one shape, and it was built from **real payloads captured from real sessions**, not from documentation.

Test detection works the same way. Hivemind recognizes `pytest`, `unittest`, `tox`, `npm test` and friends, reads the exit code, and pulls the failing test name out of the output.

Here's how Marco's failure becomes team memory **without anyone typing anything**:

1. Marco's prompt → a task *"Add JWT support to auth tokens"*
2. Kiro edits `tokens.py` → re-index → `validate_token` **changed**
3. Kiro runs `pytest` → exit code 1 → `test_refresh_session` failed
4. Changed functions + failing test → a failed `Attempt` that `TOUCHED` `validate_token`
5. If Kiro then explains the failure in its own words, the explanation is **merged into that same attempt** instead of creating a second one.

Before any agent edits a function, it gets the context around it, one hop in either direction:

```cypher
MATCH (t:Function) WHERE t.qname IN $q
MATCH (t)-[:CALLS*0..1]-(fn:Function)<-[:TOUCHED]-(a:Attempt)
RETURN a.summary, a.outcome, a.reason, a.count, a.devs, a.resolved
```

So when Lena's agent opens `validate_token` the next morning, the first thing it sees is: *"PyJWT decode failed twice: tokens issued before the deploy are rejected, so users get logged out."* It doesn't waste an hour rediscovering that.

## Feature 3: memory that cleans itself up

Memory that only ever grows turns into noise, and noisy context makes agents *worse*. So Hivemind consolidates as it writes:

![Memory that consolidates itself, step by step](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/memory-lifecycle.png)

**Repeats merge.** When a new attempt has the same outcome on the same code, with the same failing tests or very similar wording, Hivemind increments a counter instead of adding a node. You see *"seen 2× by marco"*, not two identical warnings.

**Fixes resolve failures.** When tests pass after a change, every open failure on those functions is marked fixed and linked to the fix:

```cypher
MATCH (f:Attempt {outcome: 'failed'})-[:TOUCHED]->(fn:Function)
WHERE fn.qname IN $q AND coalesce(f.resolved, false) = false
WITH DISTINCT f MATCH (a:Attempt {id: $fix})
CREATE (a)-[:RESOLVES]->(f)
SET f.resolved = true, f.resolved_dev = $dev
```

The failure doesn't disappear; it becomes history: *"PyJWT decode failed ×2. Fixed by Lena: validate both formats."* That's exactly what the next person needs to know.

**Decisions supersede decisions.** When someone records a new decision on code that already has one, the new one `SUPERSEDES` the old. Agents only see what's true *now*, while `why()` keeps the whole story.

**Conventions merge** when someone restates an existing one.

## Feature 4: handoffs that don't lose anything

When Marco stops for the day, his agent hands the task off with a note. When Lena resumes it, one query moves ownership and keeps everything else in place:

```cypher
MATCH (i:Intent {id: $task})
OPTIONAL MATCH (:Developer)-[o:OWNS]->(i)
DELETE o
WITH DISTINCT i
MATCH (d:Developer {name: $me}), (s:Session {id: $session})
CREATE (d)-[:OWNS]->(i)
CREATE (s)-[:WORKS_ON]->(i)
SET i.status = 'active'
```

Only the `OWNS` edge moves. The claims, Marco's sessions, both failed attempts, his note and Priya's decision all stay connected to the task, so Lena's agent gets the full history in one call.

## Feature 5: asking the graph questions

Agents also get an **MCP server** with 14 tools for things hooks can't see:

| Coordinate | Remember | Explain |
|---|---|---|
| `start_task`, `claim_more`, `check_conflicts`, `finish_task`, `handoff`, `open_tasks`, `resume_task` | `record_attempt`, `record_decision`, `add_convention`, `get_context` | `why`, `blast_radius`, `who_knows` |

The explain tools are my favourite, because each one is a single traversal:

- **`blast_radius(fn)`**: "what breaks if I change this?"
  ```cypher
  MATCH p = (caller:Function)-[:CALLS*1..3]->(t:Function {qname: $q})
  RETURN caller.qname, min(length(p)) AS distance
  ```
- **`who_knows(fn)`**: developers ranked by their claims, attempts and decisions on this function and its neighbours.
- **`why(fn)`**: the current decision, the one it replaced, every attempt (failed and fixed) and the people involved. Ask `why("validate_token")` at the end of the demo and you get the whole JWT story in one answer.

## Feature 6: a live map of the team

`hivemind ui` (and the hosted dashboard) draws the codebase as an architecture map. Functions are grouped by file, each developer's work is ringed in their colour, conflict paths are animated in red, and memory appears as badges.

![The Hivemind live view](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/images/live-view.png)

It leans on FalkorDB's built-in algorithms:

```cypher
CALL algo.pageRank('Function', 'CALLS') YIELD node, score        // "core" functions
CALL algo.betweenness({nodeLabels: ['Function'], relationshipTypes: ['CALLS']})  // chokepoints
RETURN [n IN nodes(shortestPath((x)-[:CALLS*]->(y))) | n.qname]  // how is A connected to B?
```

- **PageRank** surfaces the functions everything depends on.
- **Betweenness** finds chokepoints that many call paths run through, the places where conflicts are most likely.
- **shortestPath** powers a "how is A connected to B?" path finder. If there's no direct chain, it finds the closest shared caller.

Humans can add decisions and conventions from the dashboard too, so people teach the agents and not only the other way round.

Here's the end of the demo story: Lena resumes Marco's task, ships a fix that accepts both token formats, and Marco's failure turns into "fixed by Lena":

![Live demo: Lena's fix resolves Marco's failure](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/fix-demo.gif)

## Architecture: deliberately boring

![Hivemind architecture](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/architecture.png)

- Every developer's agent gets **hooks** (automatic capture) and the **MCP server** (explicit tools).
- Both talk **directly to FalkorDB Cloud** over Cypher. **There is no Hivemind server** to deploy, scale or keep alive.
- A dashboard on Vercel reads the same graph and is protected by an access code.
- All timestamps come from the **database server's clock**, because five laptops will never agree on the time.

Joining a team takes three commands:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh        # 1. install uv
uv tool install git+https://github.com/Quantapixel/hivemind   # 2. install Hivemind
hivemind join https://your-team-dashboard.vercel.app   # 3. inside your project
```

`join` asks for the team's access code, connects you to the team graph, uses your git name, **detects which agents you have installed** (Claude Code, Kiro, Codex), wires each one up in its own config format and indexes the code. If anything's off, `hivemind doctor` checks every piece and tells you how to fix it.

**Privacy:** only work inside the project folder is shared: prompts, agents' summaries, which functions changed, and test results. Your source code is never uploaded, only function names and how they call each other.

## Does it work with real agents?

A demo script is easy to fake, so I tested with real agents. Three "machines" (separate clones of a project) shared one FalkorDB Cloud graph:

1. **Kira, using Kiro,** was asked to add token revocation. The prompt never mentioned Hivemind. The hooks recorded the task from her prompt, `revoke_token` as **added**, `validate_token` as **changed**, the passing test run, and Kiro's own summary as the progress note.
2. **Carl, using Claude Code,** was then asked to add token scopes, touching the same area. His agent said, unprompted: *"The revocation check from kira's recent change is untouched."* A different developer on a different agent knew what the first one had done, and was careful not to break it.
3. **Cody, using Codex,** read the shared task board through the MCP tools and saw both teammates' work. `hivemind watch` captured his edits.

That Claude Code moment is when I knew the idea worked. Nobody told the agent to check. The context was just *there*.

## How I built it

I built Hivemind during the hackathon window, with Claude Code as my AI pair programmer. Roughly in order:

1. **The graph first.** A FalkorDB connection with one graph per team, the AST indexer and a small demo shop app (auth, cart, payments, orders) to give the call graph something real to chew on.
2. **The "brain."** Claims, multi-hop conflict detection, memory and handoffs, exposed as 14 MCP tools and a CLI. The first conflict check was undirected; I made it directional almost immediately (see *What I learned*).
3. **The live view.** I needed to *see* the graph to trust it, so the dashboard came early: first a plain graph, then an inspector, path finder and hotspots, and finally the architecture-map layout.
4. **Automatic capture.** Structural hashes, incremental re-indexing and hook handlers for all three agents, followed by real sessions in Kiro and Claude Code that each turned up bugs.
5. **Leaving localhost.** FalkorDB Cloud, a Vercel dashboard, `hivemind join` and `hivemind doctor`, so a teammate can get started in three commands.
6. **The public demo.** A five-developer story that plays one step at a time. Each step writes to a real graph through the same calls the hooks make, so what you see is what a real team would see.

## What I learned

**1. Agents won't remember to call your tools.** My first version relied on agents calling MCP tools like `record_attempt`. But an agent only calls a tool when it decides to, and when it's focused on a task, it usually doesn't. Moving capture into **hooks** (prompt, edit, test run, stop) made the memory fill itself, whatever the agent was thinking about. *If your agent memory depends on the agent's discipline, you don't have memory.*

**2. Direction matters in graph queries.** My first conflict check traversed `CALLS` in both directions at once. That flagged two functions as conflicting just because something *else* called both of them, which was a false positive and annoying. Splitting it into "code I call" and "code that calls me" fixed it, and gave better explanations too. A graph makes it very easy to write a query that *sounds* right but is subtly wrong.

**3. Function-level beats file-level.** "Someone edited `tokens.py`" is too vague to act on. Hashing each function's syntax tree tells me exactly which function changed and ignores formatting noise. That precision is what makes a failure land on the right node, so it reaches the right person later.

**4. Never forget something just because one laptop can't see it.** When a function disappears from *my* working copy, it might still exist on someone else's branch. In early versions, re-indexing could wipe that function and its history. Now a function is only removed if no memory is attached to it. *In a shared memory, absence on one machine isn't evidence of deletion.*

**5. Real sessions find bugs your tests don't.** Claude Code, Kiro and Codex all send hook payloads in different shapes, and the docs don't tell the whole story. The first real Kiro session and the first real Claude Code session each found issues my fixtures had missed. I saved those real payloads as test fixtures, and every test runs against a real FalkorDB, not a mock.

**6. Distributed systems problems show up on day one.** Laptop clocks disagree, so every timestamp comes from the database server. And my dashboard was first deployed far from the database, so every refresh crossed the world. Running the dashboard in the same region as the database fixed it.

**7. Warnings beat locks.** It was tempting to *block* conflicting edits. But a tool that blocks people gets uninstalled. Pausing once with a clear explanation, then letting the human decide, keeps people in control while still preventing the collision.

**8. The graph kept the code small.** Every feature (conflicts, context, consolidation, handoffs, blast radius, who-knows, why) is one or two Cypher queries. When the data model matches the questions, the code stays short. Take FalkorDB away and none of it works.

## What's next

- **More languages:** Tree-sitter indexing for TypeScript, Go and Rust (Python only today)
- **Smarter merging:** using FalkorDB's vector index to merge similar failures by meaning, not just by word overlap
- **Git-aware memory:** re-index on commit and link decisions to PRs automatically
- **Reviewer suggestions:** community detection on the call graph to find who should review a change
- **Forgetting well:** memory decay for old attempts and expiry for stale claims

## Try it yourself

- ▶ **Live demo (no sign-up):** [hivemind-live-demo.vercel.app](https://hivemind-live-demo.vercel.app)
- 🎬 **Demo video:** [3-minute walkthrough](https://github.com/Quantapixel/hivemind/blob/main/docs/videos/hivemind-demo.mp4)
- 💻 **Code:** [github.com/Quantapixel/hivemind](https://github.com/Quantapixel/hivemind)

Want to run it locally with no accounts at all?

```bash
git clone https://github.com/Quantapixel/hivemind && cd hivemind && uv sync
docker compose up -d          # local FalkorDB
uv run hivemind ui &          # http://127.0.0.1:8765
uv run hivemind demo --step   # press Enter to advance the story
```

If your team uses coding agents, I'd love to hear whether this matches your experience. And if you've ever had two agents quietly undo each other's work, you'll know why I built it.

---

*Built for Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB, October 2026), Track 02: Agent Memory and Coordination. I built it with Claude Code as my AI coding assistant: I came up with the idea and set the product direction, and much of the code was written by the assistant under that direction and reviewed by me. All example data is synthetic.*

*#GraphHacks #FalkorDB #WeMakeDevs #AIAgents*
