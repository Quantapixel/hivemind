"""hivemind command line: index a repo, run the MCP server, open the live view."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import Settings, connect


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings.from_env()
    if args.team:
        s.team = args.team
    if getattr(args, "dev", None):
        s.dev = args.dev
    return s


def cmd_index(args: argparse.Namespace) -> None:
    from .indexer import index_repo

    graph = connect(_settings(args))
    if args.reset:
        graph.query("MATCH (n) DETACH DELETE n")
    print(json.dumps(index_repo(graph, Path(args.path))))


def cmd_serve(args: argparse.Namespace) -> None:
    from .server import run

    run(_settings(args), agent=args.agent, transport=args.transport)


def cmd_ui(args: argparse.Namespace) -> None:
    from .ui import run

    run(_settings(args), host=args.host, port=args.port)


def cmd_demo(args: argparse.Namespace) -> None:
    from .demo import run

    run(_settings(args), pause=args.pause, step=args.step)


def cmd_init(args: argparse.Namespace) -> None:
    from .indexer import index_repo
    from .project import default_dev
    from .setup import init

    s = _settings(args)
    agents = [a.strip() for a in args.agent.split(",") if a.strip()]
    for line in init(Path(args.path), s, agents):
        print(f"  {line}")
    if not args.no_index:
        print(f"  indexed {json.dumps(index_repo(connect(s), Path(args.path)))}")
    print(f"Ready. Team '{s.team}' at {s.where()}. You are '{default_dev(args.path)}' (set HIVEMIND_DEV to change).")
    if "kiro" in agents:
        print("Kiro: start with `kiro-cli chat --agent hivemind`.")
    if "codex" in agents:
        print("Codex: approve the Hivemind hooks when Codex asks on first run.")


def cmd_hook(args: argparse.Namespace) -> None:
    from .hooks import main as hook_main

    if args.team:
        import os

        os.environ["HIVEMIND_TEAM"] = args.team
    hook_main(args.agent)


def cmd_watch(args: argparse.Namespace) -> None:
    from .project import find_root, load_settings
    from .watch import run

    root = find_root(args.path)
    if root is None:
        sys.exit(f"{args.path} is not a Hivemind project; run `hivemind init` there first.")
    s = load_settings(root)
    if getattr(args, "dev", None):
        s.dev = args.dev
    try:
        run(root, s, agent=args.agent, interval=args.interval)
    except KeyboardInterrupt:
        pass


def cmd_connect(args: argparse.Namespace) -> None:
    from .db import Settings, open_db, save_user_config

    s = Settings(url=args.url, team=args.team or "demo")
    try:
        open_db(s).select_graph("hivemind_ping").query("RETURN 1")
    except Exception as exc:
        sys.exit(f"Could not connect to {s.where()}: {exc}")
    path = save_user_config({"url": args.url, "team": s.team})
    print(f"Connected to {s.where()} as team '{s.team}'. Saved privately to {path} (only you can read it).")
    print("Now run `hivemind init <repo> --agent claude|kiro|codex` in your project.")


def cmd_invite(args: argparse.Namespace) -> None:
    from .db import load_user_config

    cfg = load_user_config()
    if not cfg.get("url"):
        sys.exit("Not connected to a hosted database yet. Run `hivemind connect <url> --team <name>` first.")
    team = args.team or cfg.get("team", "demo")
    print("Send this to your teammate privately (it contains the database password):\n")
    print("  git clone https://github.com/Quantapixel/memofull ~/memofull && cd ~/memofull && uv sync")
    print(f"  uv run --project ~/memofull hivemind --team {team} connect '{cfg['url']}'")
    print("  cd <your clone of the project> && uv run --project ~/memofull hivemind init . --agent claude   # or kiro, codex")


def cmd_status(args: argparse.Namespace) -> None:
    from .brain import Brain

    s = _settings(args)
    brain = Brain(connect(s), s.dev)
    print(json.dumps({"team": s.team, "open_tasks": brain.open_tasks()}, indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hivemind", description=__doc__)
    parser.add_argument("--team", help="team graph to use (default $HIVEMIND_TEAM or 'demo')")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="index a Python repo into the team graph")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--reset", action="store_true", help="wipe the team graph first")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("serve", help="run the MCP server for one developer's agent")
    p.add_argument("--dev", help="developer name (default $HIVEMIND_DEV)")
    p.add_argument("--agent", default="agent", help="agent label, e.g. claude-code or cursor")
    p.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ui", help="serve the live graph view")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("demo", help="play a three-developer scenario (wipes the team graph)")
    p.add_argument("--pause", type=float, default=2.5, help="seconds between beats")
    p.add_argument("--step", action="store_true", help="wait for enter between beats")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("init", help="connect a repo's coding agents to the team brain and index it")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--agent", default="claude", help="comma-separated: claude, codex, kiro (default claude)")
    p.add_argument("--no-index", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("hook", help="agent hook entry point (reads the event on stdin)")
    p.add_argument("--agent", default="claude", choices=["claude", "codex", "kiro"])
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("watch", help="record edits made by any tool (for agents without hooks)")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--dev", help="developer name (default $HIVEMIND_DEV or git user.name)")
    p.add_argument("--agent", default="watch", help="label for edits, e.g. cursor")
    p.add_argument("--interval", type=float, default=1.0)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("connect", help="save a hosted FalkorDB URL (redis:// or rediss://) for this user")
    p.add_argument("url")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("invite", help="print the commands a teammate runs to join")
    p.set_defaults(func=cmd_invite)

    p = sub.add_parser("status", help="print open tasks")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ConnectionError as exc:
        sys.exit(f"cannot reach FalkorDB: {exc}. Check `hivemind connect`, or start a local one with `docker compose up -d`.")


if __name__ == "__main__":
    main()
