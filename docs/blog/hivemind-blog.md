---
title: "Hivemind: shared memory for coding agents, built on FalkorDB"
subtitle: "How I used a FalkorDB graph to let Claude Code, Kiro and Codex share context across a team"
tags: falkordb, ai, graphdatabase, hackathon
cover: https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/cover.png
---

On many teams, each developer now works with their own coding agent: Claude Code, Kiro, Codex or something else. These agents don't share anything with each other. One agent doesn't know what another is changing, and each session starts without any memory of earlier ones. If one agent found yesterday that an approach breaks the tests, another agent may try the same approach today.

For [Graph Hacks](https://www.wemakedevs.org/hackathons/falkordb) (WeMakeDevs × FalkorDB) I built **Hivemind**, a shared memory that all of these agents read from and write to. It is stored as a graph in FalkorDB. This post explains the problem, how the system works, the main Cypher queries, and what I learned while building it.

There is a live demo that runs in the browser without sign-up. I refer to specific steps of it below:

**[hivemind-live-demo.vercel.app](https://hivemind-live-demo.vercel.app)**

{% embed https://github.com/Quantapixel/hivemind %}

## The problem

The demo uses a fictional team of five working on a small shop backend: Priya (tech lead, Claude Code), Marco (Kiro), Aisha (Codex), Ken (Claude Code) and Lena (Kiro).

Marco asks Kiro to add JWT support, and Kiro starts changing `validate_token()` in `shop/tokens.py`. Shortly after, Aisha asks Codex to add rate limiting to `checkout()` in `shop/orders.py`.

These are different files, but `checkout()` calls `current_user()`, which calls `validate_token()`. Aisha's change depends on code that Marco is changing at the same time.

{% details Which of these would Git catch? %}
None of them. Marco and Aisha edit different files, so there is no merge conflict. Marco's failed attempt is never committed. Priya's earlier decision that tokens must stay HMAC-signed until the mobile v3 app ships isn't in Git either. Git tracks changes to text, and these problems are about how the code is connected and what the team already knows.
{% enddetails %}

Later, Marco's first approach (replacing the HMAC check with a PyJWT decode) fails `test_refresh_session`, because users with old tokens get logged out. A second try fails the same way. He stops for the day, and the next morning Lena continues the task with an agent that has none of this information.

![Without shared memory vs with Hivemind](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/problem.png)

Existing approaches cover parts of this. Git and code review find problems after the work is done. `CLAUDE.md` and `AGENTS.md` files are written by hand and don't reflect work in progress. Each agent's own memory stays on one machine and inside one tool. Asking teammates in chat depends on people remembering to ask.

## Approach: attach memory to the code

Most of the information worth sharing is about a specific function: who is changing `validate_token`, what was tried on it, and which decisions apply to it. So Hivemind attaches each piece of memory to the functions it is about.

Functions are connected by calls. That means the question "what should my agent know before editing `checkout`?" becomes "what is connected to `checkout`?", which is a graph traversal.

The other questions an agent needs answered have the same form:

- Is anyone changing code that mine calls? Follow `CALLS` edges until reaching a teammate's claim.
- What has been tried near this function? Look one hop out for attempts.
- What could break if I change it? Follow callers backwards.

In SQL these would be recursive joins on every agent step. In Cypher each one is a short query, and FalkorDB returns them quickly enough to run before every edit.

Some other FalkorDB features were useful:

- **Multi-graph support.** Each team gets its own graph (`hivemind_shop`, `hivemind_payments`) in one database.
- **Built-in algorithms.** `algo.pageRank`, `algo.betweenness` and `shortestPath` power the dashboard.
- **Redis protocol and a free cloud tier.** Every agent connects to the database directly, so there is no separate Hivemind server.

## The data model

The graph has two layers. The code layer is parsed from the repository. The team memory layer records what people and agents did, linked to the relevant functions.

![The Hivemind graph data model](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/graph-model.png)

In the example:

- Marco ran a Kiro session that works on the task "Add JWT support".
- The task claims `validate_token`, which records who is currently changing it.
- The session made an attempt that failed twice and touched `validate_token`.
- Later, Lena's passing attempt resolves that failure, and her decision supersedes Priya's earlier one.

The code layer is built with Python's `ast` module. It records every function, the functions it calls, and each file's imports, and writes them in batches using `UNWIND` and `MERGE`.

Each function also stores a hash of its syntax tree, without line numbers. When an agent edits a file, Hivemind re-indexes only that file and compares the hashes, which shows exactly which functions changed. Formatting changes and comment edits don't change the hash. Logic changes do.

{% details How function calls are resolved %}
When a function calls `validate_token`, the indexer needs to decide which `validate_token` is meant. It prefers one defined in the same file, and otherwise uses any function with that name:

```cypher
MATCH (a:Function {file: $file})
UNWIND a.calls AS name
MATCH (b:Function {name: name}) WHERE b <> a
WITH a, collect(b) AS targets, [t IN collect(b) WHERE t.file = a.file] AS local
UNWIND CASE WHEN size(local) > 0 THEN local ELSE targets END AS t
MERGE (a)-[:CALLS]->(t)
```

This is not a type checker, and dynamic dispatch can mislead it. For typical application code it is accurate enough, and it is cheap to rerun after each edit.
{% enddetails %}

## Detecting conflicts before an edit

Before an agent edits a file, a hook checks whether any of the functions being changed are within three calls of a function that a teammate has claimed:

```cypher
MATCH (t:Function) WHERE t.qname IN $mine
MATCH p = (t)-[:CALLS*1..3]->(o:Function)<-[:CLAIMS]-(i:Intent {status: 'active'})
WHERE i.dev <> $me
RETURN t.qname, o.qname, i.dev, i.description,
       [n IN nodes(p) | n.qname] AS path
```

`[:CALLS*1..3]` matches paths of one to three call edges. `nodes(p)` returns the path itself, so the agent can show the developer how the two pieces of code are connected.

![How Hivemind catches a conflict before anyone writes code](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/conflict.png)

The full check runs three variants:

1. The same function
2. Code that my code calls
3. Code that calls my code

For each pair it keeps the shortest path, which is the easiest to read.

If a conflict is found, the hook pauses the edit once and returns the explanation to the agent, which passes it on to the developer. If the developer continues, the next attempt goes through. The acknowledgement is stored as a node so the same warning isn't shown again. I chose a one-time warning over a hard block, because a hard block would get in the way of normal work.

> **In the demo:** press **Next step** until step 4 ("Aisha starts rate limiting in Codex"). A red path appears between `checkout` and `validate_token`.

![Live demo: a conflict appears when Aisha starts](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/conflict-demo.gif)

## Recording memory automatically

The first version gave agents MCP tools such as `record_attempt` and relied on them to call these tools. In practice they rarely did, because they focus on the task they were given. The memory stayed mostly empty.

I moved recording into hooks, which Claude Code, Kiro and Codex all support. The hooks run at fixed points:

- When the agent receives a prompt, it becomes the session's task.
- Before an edit, the functions being edited are claimed and checked for conflicts.
- After an edit, the file is re-indexed and the changed functions are recorded.
- When tests run after code changes, the result is recorded as an attempt on those functions.
- When the agent stops, its summary is saved as the task's progress note.

For example, Marco's failure is recorded like this:

1. The prompt "Add JWT support to auth tokens" becomes a task.
2. Kiro edits `tokens.py`. Re-indexing shows that the hash of `validate_token` changed.
3. Kiro runs `pytest`. The exit code is 1, and `test_refresh_session` is read from the output.
4. The changed function and the failing test become an `Attempt` linked to `validate_token`.
5. If Kiro then describes the failure in its own words, that description is added to the same attempt instead of creating a second one.

{% details Handling three different hook formats %}
Claude Code sends `PascalCase` events (`PreToolUse`, `PostToolUseFailure`), and its tools are called `Edit`, `Write` and `Bash`.

Kiro sends `camelCase` events (`preToolUse`, `agentSpawn`). Its tools are called `fs_write` and `execute_bash`, and it puts the exit status in `tool_response.result[0]`.

Codex uses Claude-style event names, but its tool names differ between versions (`shell`, `exec_command`, `apply_patch`). So edits and commands are identified by their inputs instead of by tool name.

A small normalisation layer converts all of these into one event format. I based it on payloads captured from real sessions, which are now used as test fixtures, because the documentation didn't cover every case.

Test runs are detected by matching the command (`pytest`, `unittest`, `jest`, `go test`, `cargo test` and others) and checking the exit code. The name of the failing test is then parsed from the output.
{% enddetails %}

Before an agent edits a function, it receives everything that was tried on that function or its direct neighbours:

```cypher
MATCH (t:Function) WHERE t.qname IN $q
MATCH (t)-[:CALLS*0..1]-(fn:Function)<-[:TOUCHED]-(a:Attempt)
RETURN a.summary, a.outcome, a.reason, a.count, a.devs, a.resolved
```

When Lena's agent opens `validate_token`, its context includes the fact that the PyJWT decode failed twice, and why.

## Consolidating memory

If every event became a new node, the memory would fill up with near-duplicates. Hivemind merges related entries when it writes them.

![How memory is consolidated, step by step](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/memory-lifecycle.png)

- **Repeated failures merge.** If a new attempt has the same outcome on the same code, with the same failing tests or very similar wording, the existing attempt's count is increased instead of adding a node.
- **Fixes resolve failures.** When tests pass on code with open failures, those failures are marked as fixed and linked to the fix with a `RESOLVES` edge. They stay in the graph as history.
- **Decisions supersede decisions.** A new decision on the same code `SUPERSEDES` the old one. Agents see only the current decision, while `why()` can still show the earlier ones.

{% details Query: a passing attempt resolves earlier failures %}
```cypher
MATCH (f:Attempt {outcome: 'failed'})-[:TOUCHED]->(fn:Function)
WHERE fn.qname IN $q AND coalesce(f.resolved, false) = false
WITH DISTINCT f MATCH (a:Attempt {id: $fix})
CREATE (a)-[:RESOLVES]->(f)
SET f.resolved = true, f.resolved_dev = $dev
```

`DISTINCT` is needed because one failure can touch several of the functions in `$q`. Without it, duplicate `RESOLVES` edges would be created.
{% enddetails %}

> **In the demo:** step 7 ("Marco tries again, same failure") shows the merged failure with a ×2 badge. Step 10 ("Lena ships the fix") shows it marked as fixed by Lena.

## Handoffs

When Marco stops, his agent hands the task off with a note. When Lena resumes it, one query moves the `OWNS` edge to her and links her session to the task.

Everything else stays connected to the task: the claims, Marco's sessions, both failed attempts, his note and Priya's decision. Lena's agent receives the full history in one call.

{% details The resume query %}
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
{% enddetails %}

![Live demo: Lena's fix resolves Marco's failure](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/fix-demo.gif)

## Querying the memory

Agents also get an MCP server with 14 tools, for things the hooks can't infer: starting and handing off tasks, recording decisions and conventions, and answering questions about the code. Three of these tools explain the code, and each is a single traversal:

- **`blast_radius`** follows callers backwards:
  ```cypher
  MATCH p = (caller:Function)-[:CALLS*1..3]->(t:Function {qname: $q})
  RETURN caller.qname, min(length(p)) AS distance
  ```
- **`who_knows`** ranks developers by their claims, attempts and decisions on a function and its neighbours.
- **`why`** returns the current decision, the decision it replaced, all attempts, and the people involved. At the end of the demo, `why("validate_token")` returns the full history of the JWT change.

> **In the demo:** at step 12, click `validate_token` on the map. The panel on the right shows the same information as `why`.

## The dashboard

The dashboard shows the codebase as a map:

- Functions are grouped by file.
- Each developer's work is outlined in their colour.
- Conflict paths are drawn in red.
- Memory is shown as badges on functions.

It uses FalkorDB's algorithms in a few places:

- `algo.pageRank` ranks functions that much of the code depends on.
- `algo.betweenness` finds functions that many call paths pass through, which is where conflicts are more likely.
- The path finder uses `shortestPath`. When two functions aren't directly connected, it shows their closest shared caller instead.

People can also add decisions and conventions from the dashboard.

![The Hivemind live view](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/live-view.png)

## Architecture and setup

![Hivemind architecture](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/architecture.png)

Each agent connects to FalkorDB Cloud directly over Cypher, through both the hooks and the MCP server. The dashboard is hosted on Vercel, reads the same graph, and is protected by an access code. Timestamps come from the database server's clock, since laptop clocks can differ.

To join a team, you install Hivemind with `uv tool install git+https://github.com/Quantapixel/hivemind` and run `hivemind join <dashboard-url>` inside the project. The command:

1. Asks for the team's access code
2. Detects which agents are installed
3. Configures each agent in its own format
4. Indexes the code

`hivemind doctor` checks the setup and reports what to fix.

Source code is not uploaded. The graph stores function names, how functions call each other, and what people did to them.

## Testing with real agents

I tested Hivemind with three separate clones of a project, acting as three machines that shared one cloud graph.

First, Kira (using Kiro) was asked to add token revocation. The prompt didn't mention Hivemind. The hooks recorded the task, `revoke_token` as added, `validate_token` as changed, the passing test run, and Kiro's summary.

Next, Carl (using Claude Code) was asked to add token scopes in the same area. During the task, his agent noted: *"The revocation check from kira's recent change is untouched."* It had picked up Kira's change from the shared memory without being asked to check.

Finally, Cody (using Codex) read the shared task board through MCP and saw both of their tasks.

## What I learned

**Agents don't reliably record things themselves.** Relying on agents to call a recording tool left the memory mostly empty. Recording through hooks, outside the agent's decisions, worked much better.

**The first conflict query was too broad.** It followed `CALLS` edges in both directions in one pattern, so two functions were flagged as conflicting just because a third function called both. Splitting it into "code I call" and "code that calls me" removed these false positives and made the explanations clearer.

**Re-indexing could delete memory.** Developers' working copies differ, so a function missing from one copy may still exist on another branch. Early on, re-indexing one copy could delete that function and the memory attached to it. Now a function is only removed if no memory is attached to it.

**Documentation didn't cover all hook payloads.** The first real Kiro and Claude Code sessions each found cases my hand-written test fixtures had missed. The hook tests now use payloads captured from real sessions, and all tests run against a real FalkorDB instance.

**Region placement matters.** The dashboard was first deployed in a region far from the database, which made every request slow. Deploying it in the same region as the database fixed this.

Overall, the graph model kept the code small. Conflict detection, context, consolidation, handoffs, blast radius, `who_knows` and `why` are each one or two Cypher queries.

## Next steps

- Support for TypeScript, Go and Rust using Tree-sitter (only Python is indexed now)
- Merging similar failures by meaning using FalkorDB's vector index (currently it uses word overlap)
- Linking decisions to pull requests automatically
- Suggesting reviewers using community detection on the call graph
- Expiring old attempts and stale claims

## Try it

The [live demo](https://hivemind-live-demo.vercel.app) plays through the full scenario step by step. There is also a [3-minute video](https://github.com/Quantapixel/hivemind/blob/main/docs/videos/hivemind-demo.mp4) using real agents.

To run it locally without any accounts:

```bash
git clone https://github.com/Quantapixel/hivemind && cd hivemind && uv sync
docker compose up -d          # local FalkorDB
uv run hivemind ui &          # http://127.0.0.1:8765
uv run hivemind demo --step   # press Enter to advance
```

If your team uses several agents on one codebase, I'd be interested to hear how you coordinate them.

---

*Built for Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB, October 2026), Track 02: Agent Memory and Coordination. I built it with Claude Code as my coding assistant: the idea and product direction are mine, and much of the code was written by the assistant under that direction and reviewed by me. All example data is synthetic.*
