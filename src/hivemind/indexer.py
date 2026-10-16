"""Turn a Python repo into File / Function nodes with DEFINES, CALLS and IMPORTS edges.

Uses the stdlib `ast` module so indexing has no native dependencies. Call
resolution is name based: a call to `foo()` or `x.foo()` links to every
function named `foo` in the repo, preferring one in the same file.
"""

from __future__ import annotations

import ast
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


def parse_repo(root: Path) -> list[FileInfo]:
    root = root.resolve()
    files: list[FileInfo] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        except (SyntaxError, UnicodeDecodeError):
            continue
        info = FileInfo(path=rel.as_posix(), module=_module_name(rel))
        _Visitor(info).visit(tree)
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
    fn_rows = [
        {
            "qname": fn.qname,
            "name": fn.name,
            "file": fn.file,
            "lineno": fn.lineno,
            "end_lineno": fn.end_lineno,
        }
        for f in files
        for fn in f.functions
    ]

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
            SET fn.name = r.name, fn.file = r.file,
                fn.lineno = r.lineno, fn.end_lineno = r.end_lineno
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
