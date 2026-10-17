"""memofull command line: index a repo, run the MCP server, open the live view."""

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

    from .onboard import detect_agents

    s = _settings(args)
    agents = detect_agents() if args.agent == "auto" else [a.strip() for a in args.agent.split(",") if a.strip()]
    if not agents:
        sys.exit("No coding agent found (claude, kiro-cli or codex). Install one, or pass --agent.")
    for line in init(Path(args.path), s, agents):
        print(f"  {line}")
    if not args.no_index:
        print(f"  indexed {json.dumps(index_repo(connect(s), Path(args.path)))}")
    print(f"Ready. Team '{s.team}' at {s.where()}. You are '{default_dev(args.path)}' (set MEMOFULL_DEV to change).")
    if "kiro" in agents:
        print("Kiro: start with `kiro-cli chat --agent memofull`.")
    if "codex" in agents:
        print("Codex: approve the Memofull hooks when Codex asks on first run.")


def cmd_hook(args: argparse.Namespace) -> None:
    from .hooks import main as hook_main

    if args.team:
        import os

        os.environ["MEMOFULL_TEAM"] = args.team
    hook_main(args.agent)


def cmd_watch(args: argparse.Namespace) -> None:
    from .project import find_root, load_settings
    from .watch import run

    root = find_root(args.path)
    if root is None:
        sys.exit(f"{args.path} is not a Memofull project; run `memofull init` there first.")
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
        open_db(s).connection.ping()
    except Exception as exc:
        sys.exit(f"Could not connect to {s.where()}: {exc}")
    path = save_user_config({"url": args.url, "team": s.team})
    print(f"Connected to {s.where()} as team '{s.team}'. Saved privately to {path} (only you can read it).")
    print("Now run `memofull init <repo> --agent claude|kiro|codex` in your project.")


def cmd_invite(args: argparse.Namespace) -> None:
    from .db import load_user_config, user_config_path

    cfg = load_user_config()
    hosted = user_config_path().with_name("dashboard.json")
    dash = json.loads(hosted.read_text()) if hosted.exists() else {}
    url = dash.get("url") or cfg.get("dashboard")
    code = dash.get("access_code") or cfg.get("access_code")
    if url and code:
        print("Send this to your teammate (privately: the access code opens the team's memory):\n")
        print("  1. Install uv (skip if you have it):")
        print("       curl -LsSf https://astral.sh/uv/install.sh | sh")
        print("  2. Install Memofull:")
        print("       uv tool install git+https://github.com/Quantapixel/memofull")
        print("  3. In your clone of our project:")
        print(f"       memofull join {url}")
        print(f"     Access code: {code}")
        print(f"\n  Dashboard: {url}")
        return
    if not cfg.get("url"):
        sys.exit("Not connected to a hosted database yet. Run `memofull connect <url> --team <name>` first.")
    team = args.team or cfg.get("team", "demo")
    print("No dashboard deployed yet, so share the database directly (privately: it contains the password):\n")
    print("  uv tool install git+https://github.com/Quantapixel/memofull")
    print(f"  memofull --team {team} connect '{cfg['url']}'")
    print("  cd <your clone of the project> && memofull init .")


def cmd_join(args: argparse.Namespace) -> None:
    from .onboard import join

    agents = [a.strip() for a in args.agent.split(",") if a.strip()] if args.agent else None
    join(args.dashboard, args.code, Path(args.path).resolve(), agents, index=not args.no_index)


def cmd_doctor(args: argparse.Namespace) -> None:
    from .onboard import doctor

    sys.exit(doctor(Path(args.path).resolve()))


def cmd_status(args: argparse.Namespace) -> None:
    from .brain import Brain

    s = _settings(args)
    brain = Brain(connect(s), s.dev)
    print(json.dumps({"team": s.team, "open_tasks": brain.open_tasks()}, indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="memofull", description=__doc__)
    parser.add_argument("--team", help="team graph to use (default $MEMOFULL_TEAM or 'demo')")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="index a Python repo into the team graph")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--reset", action="store_true", help="wipe the team graph first")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("serve", help="run the MCP server for one developer's agent")
    p.add_argument("--dev", help="developer name (default $MEMOFULL_DEV)")
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
    p.add_argument("--agent", default="auto", help="comma-separated: claude, codex, kiro (default: every one installed)")
    p.add_argument("--no-index", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("hook", help="agent hook entry point (reads the event on stdin)")
    p.add_argument("--agent", default="claude", choices=["claude", "codex", "kiro"])
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("watch", help="record edits made by any tool (for agents without hooks)")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--dev", help="developer name (default $MEMOFULL_DEV or git user.name)")
    p.add_argument("--agent", default="watch", help="label for edits, e.g. cursor")
    p.add_argument("--interval", type=float, default=1.0)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("join", help="join a team: give the dashboard URL, enter its access code, done")
    p.add_argument("dashboard", help="the team dashboard URL, e.g. https://memofull-dashboard.vercel.app")
    p.add_argument("--code", help="the access code (asked for if not given)")
    p.add_argument("--path", default=".", help="your clone of the team's project (default: current folder)")
    p.add_argument("--agent", help="comma-separated: claude, kiro, codex (default: every one installed)")
    p.add_argument("--no-index", action="store_true")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("doctor", help="check the setup and say what to fix")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_doctor)

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
        sys.exit(f"cannot reach FalkorDB: {exc}. Check `memofull connect`, or start a local one with `docker compose up -d`.")


if __name__ == "__main__":
    main()
