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

    p = sub.add_parser("status", help="print open tasks")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ConnectionError as exc:
        sys.exit(f"cannot reach FalkorDB: {exc}. Start it with `podman compose up -d`.")


if __name__ == "__main__":
    main()
