"""Getting people set up: `memofull join` and `memofull doctor`.

join    one command from a fresh install to a working setup: exchange the
        team's dashboard access code for the database connection, check it,
        make sure the developer has a name, find the coding agents that are
        installed, wire them into this project and index the code.
doctor  check every piece and say exactly what to fix.
"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .db import Settings, connect, load_user_config, open_db, save_user_config
from .project import MARKER, default_dev, find_root, load_settings

AGENT_BINARIES = {"claude": "claude", "kiro": "kiro-cli", "codex": "codex"}
AGENT_NAMES = {"claude": "Claude Code", "kiro": "Kiro", "codex": "Codex"}
START = {
    "claude": "claude",
    "kiro": "kiro-cli chat --agent memofull",
    "codex": "codex   (approve the Memofull hooks when asked; meanwhile run `memofull watch .` in another terminal)",
}

GREEN, RED, YELLOW, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = BOLD = RESET = ""


def detect_agents() -> list[str]:
    return [a for a, binary in AGENT_BINARIES.items() if shutil.which(binary)]


def git_root(path: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True, timeout=5)
        return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_name(path: Path) -> str:
    try:
        return subprocess.run(["git", "config", "user.name"], cwd=path, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def fetch_join(dashboard: str, code: str) -> dict:
    url = dashboard.rstrip("/") + "/api/join"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {code}", "User-Agent": "memofull-cli"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise SystemExit("That access code was not accepted. Check it with whoever invited you.") from None
        if e.code == 404:
            raise SystemExit(f"{dashboard} does not allow joining. Ask for a connect command instead.") from None
        raise SystemExit(f"The dashboard answered {e.code}: {e.read().decode(errors='replace')[:200]}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise SystemExit(f"Could not reach {dashboard}: {getattr(e, 'reason', e)}") from None


def say(ok: bool | None, text: str, fix: str = "") -> bool:
    mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}" if ok is False else f"{YELLOW}!{RESET}"
    print(f"  {mark} {text}")
    if fix and not ok:
        print(f"      {DIM}{fix}{RESET}")
    return bool(ok) or ok is None


def join(dashboard: str, code: str | None, path: Path, agents: list[str] | None, index: bool = True) -> None:
    from .indexer import index_repo
    from .setup import init

    print(f"{BOLD}Joining via {dashboard}{RESET}")
    code = code or os.environ.get("MEMOFULL_ACCESS_CODE") or getpass.getpass("  Team access code: ").strip()
    info = fetch_join(dashboard, code)
    settings = Settings(url=info["url"], team=info["team"])
    try:
        open_db(settings).connection.ping()
    except Exception as exc:
        raise SystemExit(f"Got the team's database from the dashboard but could not reach it: {exc}\n"
                         "If it's a free FalkorDB Cloud instance it may be asleep; ask whoever invited you.") from None
    save_user_config({**load_user_config(), "url": info["url"], "team": info["team"], "dashboard": dashboard.rstrip("/"), "access_code": code})
    say(True, f"Connected to team '{info['team']}' ({settings.where()})")

    repo = git_root(path)
    if repo is None:
        raise SystemExit(f"\n{path} is not inside a git repository. `cd` into your clone of the team's project and run this again.")

    name = git_name(repo)
    if not name:
        name = input("  Your name (as teammates will see it): ").strip()
        if not name:
            raise SystemExit("A name is needed so teammates can tell who is doing what.")
        subprocess.run(["git", "config", "--global", "user.name", name], check=False)
    others = [r[0] for r in connect(settings).query("MATCH (d:Developer) RETURN d.name").result_set]
    say(True, f"You are '{name}'" + (f" {DIM}(someone with this name is already on the team; if that's not you, run `git config --global user.name \"Other Name\"` and join again){RESET}" if name in others else ""))

    agents = agents or detect_agents()
    if not agents:
        raise SystemExit("No coding agent found. Install Claude Code, Kiro CLI or Codex CLI, then run this again.")
    settings.dev = name
    for line in init(repo, settings, agents):
        print(f"  {GREEN}✓{RESET} {line}")
    if index:
        stats = index_repo(connect(settings), repo)
        say(True, f"Indexed {stats['functions']} functions in {stats['files']} files")

    print(f"\n{BOLD}You're in.{RESET} In {repo}:")
    for a in agents:
        print(f"  {AGENT_NAMES[a]:<12} {START[a]}")
    print(f"\nDashboard: {dashboard.rstrip('/')}   (same access code)")
    print(f"{DIM}Problems? Run `memofull doctor`.{RESET}")


def doctor(path: Path) -> int:
    ok = True
    print(f"{BOLD}Memofull doctor{RESET}")
    print(f"  {DIM}python {sys.executable}{RESET}")

    cfg = load_user_config()
    root = find_root(path)
    if root is not None:
        settings = load_settings(root)
    else:
        settings = Settings.from_env()
    where = settings.where()
    ok &= say(bool(cfg.get("url") or os.environ.get("FALKORDB_URL") or os.environ.get("FALKORDB_HOST") or root),
              f"Database configured: {where}", "Run `memofull join <dashboard-url>` (or `memofull connect <url>`).")
    try:
        t = time.time()
        g = connect(settings)
        ms = (time.time() - t) * 1000
        fns = g.query("MATCH (f:Function) RETURN count(f)").result_set[0][0]
        ok &= say(True, f"Database reachable in {ms:.0f} ms; team '{settings.team}' has {fns} functions indexed")
    except Exception as exc:
        ok &= say(False, f"Cannot reach the database: {exc}",
                  "A free FalkorDB Cloud instance sleeps after a day unused: wake it in the FalkorDB console.")
        g = None

    if root is None:
        ok &= say(False, f"{path} is not a Memofull project", "cd into the project and run `memofull join <dashboard-url>` there.")
        return 0 if ok else 1
    ok &= say(True, f"Project {root} (team '{json.loads((root / MARKER).read_text()).get('team')}')")
    name = default_dev(root)
    ok &= say(name not in ("", "anonymous"), f"You are '{name}'", 'Set a name: git config --global user.name "Your Name"')

    for agent, binary in AGENT_BINARIES.items():
        installed = shutil.which(binary) is not None
        configured, detail = _agent_configured(agent, root)
        if configured:
            ok &= say(installed, f"{AGENT_NAMES[agent]}: {detail}", f"{binary} is not installed on this machine.")
        elif installed:
            say(None, f"{AGENT_NAMES[agent]} is installed but not connected", f"Run `memofull init . --agent {agent}`.")
    if not any(_agent_configured(a, root)[0] for a in AGENT_BINARIES):
        ok &= say(False, "No agent is connected in this project", "Run `memofull join <dashboard-url>` or `memofull init . --agent claude`.")

    hook_python = _hook_python(root)
    if hook_python:
        ok &= say(Path(hook_python).exists(), "Hook command points at an existing Python",
                  "Memofull was reinstalled or moved: run `memofull init .` again.")
    if g is not None:
        last = g.query("MATCH (e:Event) WHERE e.dev = $d RETURN max(e.ts)", {"d": name}).result_set[0][0]
        say(None if last is None else True,
            f"Last activity from you: {time.strftime('%Y-%m-%d %H:%M', time.localtime(last))}" if last else "No activity from you yet (start your agent and give it a task)")
    dash = cfg.get("dashboard")
    if dash:
        say(True, f"Dashboard: {dash}")
    print(f"\n{GREEN + 'All good.' + RESET if ok else RED + 'Fix the items marked ✗ above.' + RESET}")
    return 0 if ok else 1


def _agent_configured(agent: str, root: Path) -> tuple[bool, str]:
    if agent == "claude":
        mcp = root / ".mcp.json"
        hooks = root / ".claude" / "settings.local.json"
        has_mcp = mcp.exists() and "memofull" in json.loads(mcp.read_text()).get("mcpServers", {})
        has_hooks = hooks.exists() and "memofull.cli hook" in hooks.read_text()
        return has_mcp or has_hooks, "tools and hooks connected" if has_mcp and has_hooks else "partly connected (re-run init)"
    if agent == "kiro":
        p = root / ".kiro" / "agents" / "memofull.json"
        return p.exists(), "agent 'memofull' connected (kiro-cli chat --agent memofull)"
    if agent == "codex":
        cfg = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
        text = cfg.read_text() if cfg.exists() else ""
        has = "[mcp_servers.memofull]" in text
        return has, "tools connected" + ("; hooks installed (approve them in Codex)" if "MEMOFULL MANAGED HOOKS" in text else "")
    return False, ""


def _hook_python(root: Path) -> str | None:
    p = root / ".claude" / "settings.local.json"
    if p.exists():
        for line in p.read_text().split('"'):
            if "memofull.cli hook" in line:
                return line.split(" -m ")[0].strip().strip("'")
    k = root / ".kiro" / "agents" / "memofull.json"
    if k.exists():
        return json.loads(k.read_text()).get("mcpServers", {}).get("memofull", {}).get("command")
    return None
