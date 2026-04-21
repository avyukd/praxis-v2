"""Worker must not hand handlers a long-lived handler_session.

Historically the dispatcher opened a handler_session that stayed open
for the entire handler run. Handlers typically do one read at the top
(e.g. `SELECT investigations.initiated_by`) then run an LLM call for
15+ minutes; asyncpg keeps the transaction open `idle in transaction`
for the full duration, starves the pg pool, and hangs the dispatcher.

Regression guard: ensure the ctx passed to handlers has `session=None`
so they fall back to short-scoped per-query sessions.
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_worker_passes_session_none_to_handler_context() -> None:
    src = Path("services/dispatcher/worker.py").read_text()
    tree = ast.parse(src)

    # Find the HandlerContext(...) construction inside execute_task.
    found_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "HandlerContext":
                found_calls.append(node)

    assert found_calls, (
        "HandlerContext() construction not found in worker.py — "
        "test needs updating if the file structure changed."
    )

    for call in found_calls:
        session_kw = next(
            (kw for kw in call.keywords if kw.arg == "session"), None
        )
        assert session_kw is not None, (
            "HandlerContext() in worker.py must explicitly pass "
            "session=None. If omitted, handlers may inadvertently hold "
            "connection state across LLM calls."
        )
        assert isinstance(session_kw.value, ast.Constant), (
            "session= must be a literal None, not a variable. Passing "
            "an open session again would re-introduce the pool-starve bug."
        )
        assert session_kw.value.value is None, (
            f"HandlerContext session= must be None, got {session_kw.value.value!r}"
        )


def test_worker_does_not_wrap_handler_in_session_scope() -> None:
    """Belt-and-suspenders: the handler execution block must not be
    inside an `async with session_scope() as handler_session:` — that's
    exactly the shape that caused the idle-in-transaction leak."""
    src = Path("services/dispatcher/worker.py").read_text()
    # Simple string check: the specific variable name we used before.
    assert "handler_session" not in src, (
        "services/dispatcher/worker.py still references `handler_session`. "
        "The shared-session pattern was removed because it left asyncpg "
        "transactions open for the full handler duration, starving the "
        "pg connection pool and hanging the dispatcher."
    )
