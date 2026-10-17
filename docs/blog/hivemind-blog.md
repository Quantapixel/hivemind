---
title: "My AI agent didn't know what yours was doing, so I gave them one shared brain"
subtitle: "Building Hivemind: a team memory for Claude Code, Kiro and Codex, stored as a FalkorDB graph"
tags: falkordb, ai, graphdatabase, hackathon
cover: https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/cover.png
---

Picture two coding agents on the same team. One is rewriting a token validator. The other, on a teammate's laptop, is adding a feature that depends on how that validator used to behave. Neither agent knows the other exists, and Git stays quiet because they're in different files. Someone finds out from a failing test a few days later.

Nothing about that is unusual anymore. Everyone on a team has their own agent (Claude Code, Kiro, Codex), each agent is good at its job, and none of them share anything. Every session starts from zero. If my agent learned yesterday that an approach breaks production, yours will try it again today.

For [Graph Hacks](https://www.wemakedevs.org/hackathons/falkordb) (WeMakeDevs × FalkorDB) I built **Hivemind**, a shared memory that all of those agents read and write. It lives in a FalkorDB graph, and this post covers how it works, what the graph queries look like, and what I got wrong along the way.

The demo runs in your browser with no sign-up, and I'll point to specific steps of it as we go:

**▶ [hivemind-live-demo.vercel.app](https://hivemind-live-demo.vercel.app)**

{% embed https://github.com/Quantapixel/hivemind %}

## The scenario

The demo follows a fictional team of five working on a small shop backend: Priya (tech lead, Claude Code), Marco (Kiro), Aisha (Codex), Ken (Claude Code) and Lena (Kiro).

Marco asks Kiro to add JWT support, and Kiro starts changing `validate_token()` in `shop/tokens.py`. A few minutes later Aisha asks Codex to rate-limit `checkout()` in `shop/orders.py`. Different file, different person, different agent.

But `checkout()` calls `current_user()`, which calls `validate_token()`. Aisha is building on top of code that is being rewritten while she works.

{% details Quick check: which of these would Git catch? %}
None of them. Marco and Aisha edit different files, so there's no merge conflict. Marco's failed attempt never touches Git at all. Neither does Priya's decision from last month that tokens must stay HMAC-signed until the mobile v3 app ships. Git tracks text, not intent, and all three problems here are about intent.
{% enddetails %}

It gets worse over the afternoon. Marco's first idea, swapping the HMAC check for a PyJWT decode, fails `test_refresh_session` because users with old tokens get logged out. He tries a variation and hits the same wall. Then he goes home, and the next morning Lena picks up the task with an agent that knows none of this.

![Without shared memory vs with Hivemind](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/problem.png)

I looked at what teams currently do about this. Git and code review catch problems after the work is finished. `CLAUDE.md` and `AGENTS.md` files are static and hand-maintained, so they never know what's happening right now. Each agent's built-in memory stays on one laptop inside one tool. Asking in Slack works until someone forgets to ask, and agents never ask.

## Memory should be attached to code, not chats

The design came together once I noticed that every memory worth sharing is about a specific function. "Marco is changing `validate_token`." "PyJWT decode on `validate_token` broke the refresh test." "`tokens.py` stays HMAC until v3."

If memories hang off functions, and functions are connected by calls, then "what should my agent know before it edits `checkout`?" turns into "what is connected to `checkout`?" That's a graph traversal, and it's why FalkorDB fit.

The questions an agent needs answered all share that shape. Is anyone changing code that mine calls? Follow `CALLS` edges until you reach someone's claim. What has been tried near this function? Look one hop out for attempts. What breaks if I change it? Walk callers backwards. In SQL each of those is a recursive join, run on every agent step. In Cypher each is one short query, and FalkorDB answers them fast enough to sit in front of every single edit, which was the bar. If the check is slow, people turn it off.

A few other FalkorDB features ended up mattering more than I expected. Multi-graph support gives each team its own graph (`hivemind_shop`, `hivemind_payments`) inside one database. The built-in algorithms (`algo.pageRank`, `algo.betweenness`, `shortestPath`) power the dashboard without extra infrastructure. And because it speaks the Redis protocol and has a free cloud tier, every agent on every laptop connects straight to it. There's no Hivemind server anywhere.

## The graph

There are two layers. The bottom one is the code itself, parsed from the repo. The top one is what the team did, attached to the functions it concerns.

![The Hivemind graph data model](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/graph-model.png)

Marco ran a Kiro session that works on the task "Add JWT support". The task claims `validate_token`, which is how the graph knows who is changing it right now. The session made an attempt that failed twice and touched `validate_token`, so the failure sits on the code instead of in a chat log. Later, Lena's passing attempt resolves it, and her decision supersedes Priya's.

The code layer comes from Python's `ast` module: every function, what it calls and what each file imports, written in batches with `UNWIND` and `MERGE`. Each function also stores a hash of its syntax tree with positions stripped out. When an agent edits a file, Hivemind re-indexes only that file and compares hashes, so it knows exactly which functions changed. Reformatting or editing a comment doesn't count. Changing a condition does.

{% details How calls get resolved (the Cypher) %}
When a function calls `validate_token`, the indexer has to pick which `validate_token` that means. It prefers one defined in the same file and falls back to any function with that name:

```cypher
MATCH (a:Function {file: $file})
UNWIND a.calls AS name
MATCH (b:Function {name: name}) WHERE b <> a
WITH a, collect(b) AS targets, [t IN collect(b) WHERE t.file = a.file] AS local
UNWIND CASE WHEN size(local) > 0 THEN local ELSE targets END AS t
MERGE (a)-[:CALLS]->(t)
```

It isn't a type checker, and dynamic dispatch can fool it. For ordinary application code it's right nearly all the time, and it's cheap enough to rerun after every edit.
{% enddetails %}

## Catching the conflict before the first edit

Before an agent edits anything, a hook asks the graph one question: starting from the functions I'm about to change, can I reach something a teammate has claimed within three calls?

```cypher
MATCH (t:Function) WHERE t.qname IN $mine
MATCH p = (t)-[:CALLS*1..3]->(o:Function)<-[:CLAIMS]-(i:Intent {status: 'active'})
WHERE i.dev <> $me
RETURN t.qname, o.qname, i.dev, i.description,
       [n IN nodes(p) | n.qname] AS path
```

`[:CALLS*1..3]` is a variable-length path: one to three call edges in a single pattern. The useful part is `nodes(p)`, which returns the path itself, so the agent can explain *why* there's a conflict instead of just raising an alarm.

![How Hivemind catches a conflict before anyone writes code](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/conflict.png)

The real check runs three variants: the same function, code I call, and code that calls mine. It keeps the shortest path for each pair, because the shortest chain is the easiest explanation for a human to follow.

When it finds something, the hook pauses the edit once and hands the agent that explanation. The agent tells the developer, and if they go ahead, the retry goes through. The acknowledgement is stored as a small node in the graph so the same warning doesn't repeat. I went back and forth on blocking harder, but a tool that stops people from working gets uninstalled by Friday.

> **See it live:** open the [demo](https://hivemind-live-demo.vercel.app), press **Next step** until step 4 ("Aisha starts rate limiting in Codex"), and watch the red path appear between `checkout` and `validate_token`.

![Live demo: a conflict appears the moment Aisha starts](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/conflict-demo.gif)

## Recording memory without anyone asking

My first version gave agents MCP tools like `record_attempt` and trusted them to call them. They mostly didn't. An agent in the middle of a task calls the tools it needs for the task, and bookkeeping isn't one of them. I had a well-designed memory that stayed nearly empty.

So capture moved into hooks, which Claude Code, Kiro and Codex all support. The agent doesn't decide anything. The prompt becomes the session's task. Before an edit, the functions being edited get claimed and checked for conflicts. After an edit, the file is re-indexed and the changed functions are recorded. When a test command runs after code has changed, the result becomes an attempt on exactly those functions. When the agent stops, its own summary becomes the task's progress note.

Here's Marco's failure, recorded without anyone typing anything into Hivemind:

1. Prompt: "Add JWT support to auth tokens" becomes a task.
2. Kiro edits `tokens.py`. Re-indexing shows `validate_token`'s hash changed.
3. Kiro runs `pytest`. Exit code 1, and `test_refresh_session` is pulled from the output.
4. Changed function plus failing test becomes an `Attempt` that `TOUCHED` `validate_token`.
5. When Kiro then explains the failure in its own words, the explanation is merged into that attempt rather than stored as a second memory.

{% details The annoying part: three agents, three payload formats %}
Claude Code sends `PascalCase` events (`PreToolUse`, `PostToolUseFailure`) and tools called `Edit`, `Write` and `Bash`. Kiro sends `camelCase` events (`preToolUse`, `agentSpawn`) with tools called `fs_write` and `execute_bash`, and puts the exit status inside `tool_response.result[0]`. Codex uses Claude-style event names, but its tool names change between versions (`shell`, `exec_command`, `apply_patch`), so edits and commands are recognised by their inputs rather than by name.

A small normalisation layer turns all of them into one event shape. I wrote it from real payloads captured in real sessions, which are now test fixtures, because the documentation didn't cover every case.

Test detection is a regex over the command (`pytest`, `unittest`, `jest`, `go test`, `cargo test` and roughly twenty more) plus the exit code, with a pass over the output to pull out the name of the failing test.
{% enddetails %}

Before any agent edits a function, it gets everything tried on that function or its direct neighbours:

```cypher
MATCH (t:Function) WHERE t.qname IN $q
MATCH (t)-[:CALLS*0..1]-(fn:Function)<-[:TOUCHED]-(a:Attempt)
RETURN a.summary, a.outcome, a.reason, a.count, a.devs, a.resolved
```

So when Lena's agent opens `validate_token` the next morning, the first thing in its context is that PyJWT decode failed twice because tokens issued before the deploy get rejected. It skips that approach without being told.

## Keeping the memory from turning into noise

A memory that only grows becomes a pile of near-duplicate warnings, and noisy context makes agents worse. Hivemind consolidates when it writes.

![Memory that consolidates itself, step by step](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/memory-lifecycle.png)

If a new attempt has the same outcome on the same code with the same failing tests (or very similar wording), the existing attempt's counter goes up instead of a new node appearing. When tests pass on code that has open failures, those failures are marked fixed and linked to the fix with a `RESOLVES` edge. They stay in the graph as history: "failed twice, fixed by Lena by validating both formats" is exactly what the next person needs. A new decision on the same code `SUPERSEDES` the old one, so agents only see the current rule while `why()` can still show how it got there.

{% details The Cypher behind "a fix resolves earlier failures" %}
```cypher
MATCH (f:Attempt {outcome: 'failed'})-[:TOUCHED]->(fn:Function)
WHERE fn.qname IN $q AND coalesce(f.resolved, false) = false
WITH DISTINCT f MATCH (a:Attempt {id: $fix})
CREATE (a)-[:RESOLVES]->(f)
SET f.resolved = true, f.resolved_dev = $dev
```

The `DISTINCT` matters: one failure can touch several of the functions in `$q`, and without it you'd create duplicate `RESOLVES` edges.
{% enddetails %}

> **See it live:** step 7 ("Marco tries again, same failure") shows the merge as a ×2 badge instead of a second entry. Step 10 ("Lena ships the fix") shows the failure turning into "fixed by Lena".

## Handoffs

When Marco stops for the day, his agent hands the task off with a note. When Lena resumes it, one query moves the `OWNS` edge to her and attaches her session. Nothing else moves. The claims, Marco's sessions, both failed attempts, his note and Priya's decision are all still connected to the task, so Lena's agent gets the whole history in one call.

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

## Asking questions

Agents also get an MCP server with 14 tools for things hooks can't infer: starting and handing off tasks, recording decisions and conventions, and a few explain tools. Those last ones are each a single traversal. `blast_radius` walks callers backwards:

```cypher
MATCH p = (caller:Function)-[:CALLS*1..3]->(t:Function {qname: $q})
RETURN caller.qname, min(length(p)) AS distance
```

`who_knows` ranks people by their claims, attempts and decisions on a function and its neighbours. `why` collects the current decision, the one it replaced, every attempt and who was involved. At the end of the demo, asking `why("validate_token")` returns the whole JWT story in one answer.

> **See it live:** at step 12, click `validate_token` on the map. The inspector on the right is that same `why` view.

The dashboard draws the codebase as a map: functions grouped by file, each developer's work outlined in their colour, conflict paths in red, memory as badges. Hotspots come from `algo.pageRank` (functions everything depends on) and `algo.betweenness` (chokepoints that many call paths pass through, which is where conflicts tend to happen). The path finder uses `shortestPath` and falls back to the closest shared caller when two functions aren't directly connected. People can add decisions and conventions there too, so it isn't only agents teaching agents.

![The Hivemind live view](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/images/live-view.png)

## How it runs

![Hivemind architecture](https://raw.githubusercontent.com/Quantapixel/hivemind/main/docs/blog/images/architecture.png)

Each agent talks to FalkorDB Cloud directly over Cypher, through both the hooks and the MCP server. The dashboard on Vercel reads the same graph behind an access code. Timestamps come from the database server's clock because five laptops will never agree on the time.

Joining a team is `uv tool install git+https://github.com/Quantapixel/hivemind` followed by `hivemind join <dashboard-url>` inside the project. That asks for the team's access code, finds which agents you have installed, wires each one up in its own config format and indexes the code. `hivemind doctor` checks every piece when something's off. Source code never leaves your machine; the graph only holds function names, how they call each other, and what people did to them.

## Testing it with real agents

Scripted demos are easy to fake, so I ran three separate clones as three "machines" sharing one cloud graph. Kira, on Kiro, was asked to add token revocation, with no mention of Hivemind in the prompt. The hooks still recorded the task, `revoke_token` as added, `validate_token` as changed, the passing test run and Kiro's summary.

Then Carl, on Claude Code, was asked to add token scopes in the same area. Partway through, his agent said: *"The revocation check from kira's recent change is untouched."* Nobody had told it to check. A different person's work, done in a different agent, was simply part of its context. Cody, on Codex, then read the shared task board through MCP and saw both of them.

## What I got wrong, and what I learned

**Trusting agents to keep records.** Covered above, but it's the lesson I'd pass on to anyone building agent memory: if the memory depends on the agent remembering to write to it, it will be empty. Capture has to happen around the agent, not through it.

**An undirected conflict query.** My first conflict check followed `CALLS` edges in both directions in one pattern. It flagged two functions as conflicting because some third function called both of them. Splitting it into "code I call" and "code that calls me" removed the false positives and made the explanations clearer. A graph query can read perfectly sensibly and still answer a slightly different question than the one you meant.

**Deleting what one laptop couldn't see.** Developers' working copies differ. A function missing from my checkout may exist on your branch. Early on, re-indexing my copy could delete that function along with every memory attached to it. Now a function is only removed when no memory hangs off it.

**Relying on documentation for payload formats.** The first real Kiro session and the first real Claude Code session each broke something my hand-written fixtures hadn't covered. Every hook test now runs on payloads captured from real sessions, against a real FalkorDB rather than a mock.

**Deploying the dashboard far from the database.** Every refresh made a round trip across the world. Pinning the Vercel region next to the database fixed it, and it's now a line in the setup docs.

On the positive side, the data model kept the codebase small. Conflicts, context, consolidation, handoffs, blast radius, who-knows and why are each one or two Cypher queries. When the shape of the data matches the shape of the questions, there's not much code left to write.

## What's next

Only Python is indexed today, so Tree-sitter support for TypeScript, Go and Rust is first. After that: merging similar failures by meaning using FalkorDB's vector index (it uses word overlap now), linking decisions to PRs automatically, suggesting reviewers with community detection on the call graph, and letting old attempts and stale claims fade out.

## Try it

The [live demo](https://hivemind-live-demo.vercel.app) plays the whole story in a few minutes. There's also a [3-minute video](https://github.com/Quantapixel/hivemind/blob/main/docs/videos/hivemind-demo.mp4) with real agents in real terminals. To run everything locally with no accounts:

```bash
git clone https://github.com/Quantapixel/hivemind && cd hivemind && uv sync
docker compose up -d          # local FalkorDB
uv run hivemind ui &          # http://127.0.0.1:8765
uv run hivemind demo --step   # press Enter to advance
```

If your team runs several agents on one codebase, I'd like to hear how you handle this today, especially if you've found something simpler.

---

*Built for Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB, October 2026), Track 02: Agent Memory and Coordination. I built it with Claude Code as my coding assistant: the idea and product direction are mine, and much of the code was written by the assistant under that direction and reviewed by me. All example data is synthetic.*
