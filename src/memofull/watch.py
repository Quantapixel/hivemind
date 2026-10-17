"""`memofull watch`: capture edits from any tool, hooks or not.

Polls the project's Python files; when one changes it is re-indexed, the
changed functions are recorded and claimed for this developer, and any
conflict with a teammate's work is printed (and sent as a desktop
notification when notify-send is available). Use it alongside an agent
that has no hooks, or an editor.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .brain import Brain
from .db import Settings, connect
from .indexer import SKIP_DIRS, index_file

YELLOW, RED, DIM, RESET = "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def _snapshot(root: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.endswith(".py"):
                p = Path(dirpath) / f
                try:
                    out[p.relative_to(root).as_posix()] = p.stat().st_mtime
                except OSError:
                    pass
    return out


def _notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Memofull", title, body], capture_output=True)


def run(root: Path, settings: Settings, agent: str = "watch", interval: float = 1.0) -> None:
    brain = Brain(connect(settings), settings.dev, agent)
    brain.attach_session(f"{agent}-{socket.gethostname()}-{os.getpid()}")
    print(f"Watching {root} as {settings.dev} (team '{settings.team}'). Ctrl+C to stop.", flush=True)
    seen = _snapshot(root)
    while True:
        time.sleep(interval)
        now = _snapshot(root)
        touched = [p for p, m in now.items() if seen.get(p) != m] + [p for p in seen if p not in now]
        seen = now
        for rel in touched:
            diff = index_file(brain.g, root, rel)
            if not any(diff.values()):
                continue
            out = brain.record_change(rel, diff, "watch")
            names = ", ".join(q.split("::")[-1] for q in out["changed"]) or "(removed code)"
            print(f"{DIM}{time.strftime('%H:%M:%S')}{RESET} {rel}: {names}", flush=True)
            for c in out["conflicts"]:
                path = " -> ".join(p.split("::")[-1] for p in c["path"])
                msg = f"{c['other_dev']} is on \"{c['other_intent']}\": {path}"
                print(f"  {RED}conflict{RESET} {msg}", flush=True)
                _notify("Memofull: connected work", msg)
