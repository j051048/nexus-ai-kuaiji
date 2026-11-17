"""OPT02-D: prevent new unscoped write transactions.

The baseline records existing ``transaction(write=True)`` calls that default to
the global write scope. The set may shrink only; adding a new entry requires an
explicit owner decision, so this test fails for any new file::function pair.
"""

from __future__ import annotations

import ast
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
BASELINE = Path(__file__).resolve().with_name("write_scope_baseline.txt")


def _enclosing_function(tree: ast.AST, lineno: int) -> str:
    owners = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= getattr(node, "end_lineno", node.lineno)
    ]
    if not owners:
        return "<module>"
    return max(owners, key=lambda node: node.lineno).name


def unscoped_write_transactions(root: Path = APP_ROOT) -> set[str]:
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "transaction"
            ):
                continue
            keywords = {item.arg: item.value for item in node.keywords if item.arg}
            write = keywords.get("write")
            if not (isinstance(write, ast.Constant) and write.value is True):
                continue
            if "write_scope" in keywords:
                continue
            relative = path.relative_to(root.parent).as_posix()
            found.add(f"{relative}::{_enclosing_function(tree, node.lineno)}")
    return found


def baseline_entries() -> set[str]:
    entries: set[str] = set()
    for raw in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert "::" in line, f"baseline entry must be path::function: {line!r}"
        entries.add(line)
    return entries


def test_scanner_marks_a_new_unscoped_write_call(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "sample.py").write_text(
        "def write_things(db):\n    db.transaction(write=True)\n",
        encoding="utf-8",
    )
    assert unscoped_write_transactions(app) == {"app/sample.py::write_things"}


def test_new_unscoped_write_transaction_is_outside_baseline() -> None:
    allowed = baseline_entries()
    actual = unscoped_write_transactions()
    added = sorted(actual - allowed)
    assert not added, "New write transaction must declare write_scope. New entries: " + ", ".join(
        added
    )
