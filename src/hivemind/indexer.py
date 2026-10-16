"""Turn a Python repo into File / Function nodes with DEFINES, CALLS and IMPORTS edges.

Uses the stdlib `ast` module so indexing has no native dependencies. Call
resolution is name based: a call to `foo()` or `x.foo()` links to every
function named `foo` in the repo, preferring one in the same file.

Each function carries a structural hash (its AST without positions), so
re-indexing a file after an agent edits it tells us exactly which functions
changed, independent of which agent made the edit.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from falkordb import Graph

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".tox"}


@dataclass
class FunctionInfo:
    qname: str  # "pkg/auth.py::Session.refresh"
    name: str  # "refresh"
    file: str
    lineno: int
    end_lineno: int
    hash: str = ""
    calls: set[str] = field(default_factory=set)


@dataclass
class FileInfo:
    path: str
    module: str
    functions: list[FunctionInfo] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)


def _module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _Visitor(ast.NodeVisitor):
    def __init__(self, info: FileInfo):
        self.info = info
        self.scope: list[str] = []
        self.current: list[FunctionInfo] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        dotted = ".".join([*self.scope, node.name])
        fn = FunctionInfo(
            qname=f"{self.info.path}::{dotted}",
            name=node.name,
            file=self.info.path,
            lineno=node.lineno,
            end_lineno=node.end_lineno or node.lineno,
            hash=hashlib.sha1(ast.dump(node).encode()).hexdigest()[:16],
        )
        self.info.functions.append(fn)
        self.scope.append(node.name)
        self.current.append(fn)
        self.generic_visit(node)
        self.current.pop()
        self.scope.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Call(self, node: ast.Call) -> None:
        if self.current:
            target = node.func
            if isinstance(target, ast.Name):
                self.current[-1].calls.add(target.id)
            elif isinstance(target, ast.Attribute):
                self.current[-1].calls.add(target.attr)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.info.imports.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base = node.module
            if node.level:
                pkg = self.info.module.split(".")
                pkg = pkg[: len(pkg) - node.level] if len(pkg) >= node.level else []
                base = ".".join([*pkg, node.module])
            self.info.imports.add(base)
            for alias in node.names:
                self.info.imports.add(f"{base}.{alias.name}")


def parse_file(root: Path, rel: str) -> FileInfo | None:
    path = root / rel
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None
    info = FileInfo(path=Path(rel).as_posix(), module=_module_name(Path(rel)))
    _Visitor(info).visit(tree)
    return info


def parse_repo(root: Path) -> list[FileInfo]:
    root = root.resolve()
    files: list[FileInfo] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        info = parse_file(root, rel.as_posix())
        if info:
            files.append(info)
    return files


def resolve(files: list[FileInfo]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (call edges, import edges) as (from, to) pairs of qnames / paths."""
    by_name: dict[str, list[FunctionInfo]] = {}
    for f in files:
        for fn in f.functions:
            by_name.setdefault(fn.name, []).append(fn)

    calls: set[tuple[str, str]] = set()
    for f in files:
        for fn in f.functions:
            for callee in fn.calls:
                targets = by_name.get(callee, [])
                local = [t for t in targets if t.file == fn.file]
                for t in local or targets:
                    if t.qname != fn.qname:
                        calls.add((fn.qname, t.qname))

    by_module = {f.module: f.path for f in files}
    imports: set[tuple[str, str]] = set()
    for f in files:
        for mod in f.imports:
            target = by_module.get(mod)
            if target and target != f.path:
                imports.add((f.path, target))
    return sorted(calls), sorted(imports)


def index_repo(graph: Graph, root: Path, batch: int = 500) -> dict[str, int]:
    """Upsert the code layer. Functions that no longer exist are removed, along
    with their CALLS edges, but memory attached to them (attempts, decisions)
    keeps its own nodes."""
    files = parse_repo(root)
    calls, imports = resolve(files)

    file_rows = [{"path": f.path, "module": f.module} for f in files]
    fn_rows = [_fn_row(fn) for f in files for fn in f.functions]

    def chunks(items: list) -> list[list]:
        return [items[i : i + batch] for i in range(0, len(items), batch)] or [[]]

    for chunk in chunks(file_rows):
        graph.query(
            "UNWIND $rows AS r MERGE (f:File {path: r.path}) SET f.module = r.module",
            {"rows": chunk},
        )
    for chunk in chunks(fn_rows):
        graph.query(
            """
            UNWIND $rows AS r
            MERGE (fn:Function {qname: r.qname})
            SET fn.name = r.name, fn.file = r.file, fn.lineno = r.lineno,
                fn.end_lineno = r.end_lineno, fn.hash = r.hash, fn.calls = r.calls
            WITH fn, r
            MATCH (f:File {path: r.file})
            MERGE (f)-[:DEFINES]->(fn)
            """,
            {"rows": chunk},
        )

    # Drop stale code nodes and rebuild structural edges from scratch.
    live_fns = [r["qname"] for r in fn_rows]
    live_files = [r["path"] for r in file_rows]
    graph.query("MATCH (fn:Function) WHERE NOT fn.qname IN $live DETACH DELETE fn", {"live": live_fns})
    graph.query("MATCH (f:File) WHERE NOT f.path IN $live DETACH DELETE f", {"live": live_files})
    graph.query("MATCH (:Function)-[c:CALLS]->(:Function) DELETE c")
    graph.query("MATCH (:File)-[i:IMPORTS]->(:File) DELETE i")

    for chunk in chunks([{"a": a, "b": b} for a, b in calls]):
        graph.query(
            """
            UNWIND $rows AS r
            MATCH (a:Function {qname: r.a}), (b:Function {qname: r.b})
            MERGE (a)-[:CALLS]->(b)
            """,
            {"rows": chunk},
        )
    for chunk in chunks([{"a": a, "b": b} for a, b in imports]):
        graph.query(
            """
            UNWIND $rows AS r
            MATCH (a:File {path: r.a}), (b:File {path: r.b})
            MERGE (a)-[:IMPORTS]->(b)
            """,
            {"rows": chunk},
        )

    return {"files": len(files), "functions": len(fn_rows), "calls": len(calls), "imports": len(imports)}


def _fn_row(fn: FunctionInfo) -> dict:
    return {
        "qname": fn.qname,
        "name": fn.name,
        "file": fn.file,
        "lineno": fn.lineno,
        "end_lineno": fn.end_lineno,
        "hash": fn.hash,
        "calls": sorted(fn.calls),
    }


def index_file(graph: Graph, root: Path, rel: str) -> dict[str, list[str]]:
    """Re-index one file after it changed and report which functions were
    added, changed (different structure) or removed."""
    rel = Path(rel).as_posix()
    if not rel.endswith(".py"):
        return {"added": [], "changed": [], "removed": []}
    before = {r[0]: r[1] for r in graph.query("MATCH (fn:Function {file: $f}) RETURN fn.qname, fn.hash", {"f": rel}).result_set}
    info = parse_file(root.resolve(), rel) if (root / rel).exists() else None
    if info is None and (root / rel).exists():
        return {"added": [], "changed": [], "removed": []}  # mid-edit syntax error: keep the last good index
    fns = info.functions if info else []
    after = {fn.qname: fn.hash for fn in fns}

    if info:
        graph.query("MERGE (f:File {path: $p}) SET f.module = $m", {"p": rel, "m": info.module})
        graph.query(
            """
            UNWIND $rows AS r
            MERGE (fn:Function {qname: r.qname})
            SET fn.name = r.name, fn.file = r.file, fn.lineno = r.lineno,
                fn.end_lineno = r.end_lineno, fn.hash = r.hash, fn.calls = r.calls
            WITH fn
            MATCH (f:File {path: $p})
            MERGE (f)-[:DEFINES]->(fn)
            """,
            {"rows": [_fn_row(fn) for fn in fns], "p": rel},
        )
    removed = [q for q in before if q not in after]
    if removed:
        graph.query("MATCH (fn:Function) WHERE fn.qname IN $q DETACH DELETE fn", {"q": removed})
    if info is None:
        graph.query("MATCH (f:File {path: $p}) DETACH DELETE f", {"p": rel})

    # Outgoing calls from this file: same-file definitions win, else any match.
    graph.query("MATCH (a:Function {file: $p})-[c:CALLS]->() DELETE c", {"p": rel})
    graph.query(
        """
        MATCH (a:Function {file: $p})
        UNWIND a.calls AS name
        MATCH (b:Function {name: name}) WHERE b <> a
        WITH a, name, collect(b) AS targets
        WITH a, [t IN targets WHERE t.file = a.file] AS local, targets
        UNWIND CASE WHEN size(local) > 0 THEN local ELSE targets END AS t
        MERGE (a)-[:CALLS]->(t)
        """,
        {"p": rel},
    )
    # Incoming calls to functions that are new to the graph.
    added = [q for q in after if q not in before]
    if added:
        graph.query(
            """
            MATCH (t:Function) WHERE t.qname IN $q
            MATCH (c:Function) WHERE t.name IN c.calls AND c <> t AND c.file <> t.file
            MERGE (c)-[:CALLS]->(t)
            """,
            {"q": added},
        )
    changed = [q for q in after if q in before and before[q] != after[q]]
    return {"added": added, "changed": changed, "removed": removed}
