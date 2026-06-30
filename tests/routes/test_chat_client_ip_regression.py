"""Regression test for the streaming chat 500 (UnboundLocalError: client_ip).

Bug: `client_ip` was assigned only inside the ``if is_anonymous:`` branch of
``chat_completions``. For authenticated requests it was therefore never bound,
and the streaming dispatch (``dispatch_streaming(..., client_ip=client_ip)``)
raised ``UnboundLocalError`` — so *every* streamed chat (i.e. all website chat)
returned HTTP 500 while non-streaming requests worked.

Fix: hoist the client-IP resolution so it runs for all requests, before the
``if is_anonymous`` branch.

This test guards the invariant structurally (no live providers required): inside
``chat_completions`` there must be a ``client_ip`` assignment that is NOT nested
inside an ``if is_anonymous`` block, guaranteeing it is bound on every path that
reaches the streaming dispatch.
"""

import ast
from pathlib import Path

CHAT_PY = Path(__file__).resolve().parents[2] / "src" / "routes" / "chat.py"


def _find_function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in chat.py")


def _is_anonymous_if(node: ast.AST) -> bool:
    """True if `node` is an `if is_anonymous:` statement."""
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "is_anonymous"
    )


def _assigns_client_ip(node: ast.AST) -> bool:
    targets = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return any(isinstance(t, ast.Name) and t.id == "client_ip" for t in targets)


def test_client_ip_is_assigned_unconditionally():
    tree = ast.parse(CHAT_PY.read_text())
    fn = _find_function(tree, "chat_completions")

    # Collect every `if is_anonymous:` node so we can tell which assignments are
    # gated behind it.
    anon_if_nodes = [n for n in ast.walk(fn) if _is_anonymous_if(n)]
    anon_descendants = set()
    for anon_if in anon_if_nodes:
        for child in ast.walk(anon_if):
            anon_descendants.add(id(child))

    client_ip_assignments = [n for n in ast.walk(fn) if _assigns_client_ip(n)]
    assert client_ip_assignments, "expected at least one `client_ip = ...` assignment"

    unconditional = [a for a in client_ip_assignments if id(a) not in anon_descendants]
    assert unconditional, (
        "`client_ip` is only assigned inside an `if is_anonymous:` branch. "
        "Authenticated streaming requests will hit `UnboundLocalError: client_ip` "
        "in dispatch_streaming and every streamed chat will 500. Hoist the "
        "client-IP resolution above the `is_anonymous` branch."
    )
