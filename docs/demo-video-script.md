# Demo video script (about 3 minutes)

The rules ask for "a demo video showing the working project". Judges will want to see real agents, not just the dashboard, so the video uses two terminals plus the browser. Record at 1080p with a screen recorder (on GNOME, press Ctrl+Alt+Shift+R or use OBS) and talk over it.

## Before recording (5 minutes)

1. Wake the database: open your instance in the FalkorDB Cloud console and check it says Running. Then run `hivemind doctor` in `~/shopapp` and make sure everything is ✓.
2. Open two terminals side by side, both in `~/shopapp`:
   - Terminal 1: you (Claude Code).
   - Terminal 2: a second developer. Use a second clone with a different name, e.g.
     `git clone https://github.com/Quantapixel/shopapp ~/shopapp-maya && cd ~/shopapp-maya && git config user.name "Maya" && hivemind join https://hivemind-dashboard-five.vercel.app`
     Use `kiro-cli chat --agent hivemind` here to show a second agent.
3. Browser: the private dashboard (https://hivemind-dashboard-five.vercel.app) on team `duo`, and a second tab with the public demo.
4. Make sure the shop app's tests pass at the start: `python3 -m unittest discover -s tests -t .`

## Shots

**0:00 – 0:20 · The problem** (talk over the dashboard)
"Every developer now has their own coding agent. But my agent doesn't know what yours is doing, and it forgets everything between sessions. Hivemind gives all of them one shared memory, a graph in FalkorDB."

**0:20 – 0:50 · Collision caught before it happens**
- Terminal 2 (Maya, Kiro): *"Make validate_token reject tokens older than 30 minutes."* Let it start editing.
- Terminal 1 (you, Claude Code): *"Add rate limiting to checkout in shop/orders.py."*
- Show the edit being blocked with the message: `checkout -> current_user -> validate_token`.
- Switch to the dashboard: the red conflict path and both tasks.
- Say: "Git would only catch this after we both finished. The graph caught it before I wrote a line, because checkout calls current_user, which calls validate_token."

**0:50 – 1:30 · Memory that writes itself**
- Let one agent's test run fail (if nothing fails naturally, ask Maya's agent to *"change validate_token to require a 'v2:' prefix and run the tests"*).
- Dashboard: click `validate_token`. History shows the failed attempt with the failing test, and the activity feed shows edits and test runs.
- Say: "Nobody called a tool. The hooks saw the edit, re-indexed the file to find exactly which function changed, and attached the failing test to it."

**1:30 – 2:00 · Shared memory across agents**
- Terminal 1: *"What has been tried on validate_token?"* Claude Code answers with Maya's failure.
- Say: "Different machine, different person, different agent. Same memory."

**2:00 – 2:30 · Handoff and fix**
- Terminal 2: *"I'm stopping, hand this task off to Jaskaran Singh with a note."*
- Terminal 1: *"Pick up what was handed to me."* Fix it, run the tests, and they pass.
- Dashboard: the failure is struck through, "Fixed by …".

**2:30 – 2:50 · Why a graph**
- README → *Cypher queries*, or the dashboard's Path finder and Hotspots (PageRank, betweenness).
- Say: "Every feature is a traversal: conflicts are a variable-length CALLS path to someone's claim, blast radius is transitive callers, and core functions come from PageRank. Remove FalkorDB and nothing works."

**2:50 – 3:00 · Close**
- Public demo tab: "Try it yourself at hivemind-live-demo.vercel.app. Five developers, three agents, one memory."

## If something goes wrong while recording

- An agent doesn't get blocked: the conflict check only fires when the other person's task is still active. Make sure terminal 2's task hasn't finished.
- "Could not reach the database": the free instance went to sleep; wake it in the console.
- Fallback: record the public demo playing (press Play) while you narrate. It runs the same story with five developers.
