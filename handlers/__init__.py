from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.llm.invoker import LLMResult


class HandlerContext(BaseModel):
    task_id: str
    task_type: str
    payload: dict[str, Any]
    vault_root: Path
    model: str
    # Worker's session — handlers should use this for DB writes related to task lifecycle
    # (investigation updates, signal records) so they land in the same transaction
    # as task status transitions. When None, handlers fall back to their own session_scope.
    session: AsyncSession | None = None

    model_config = {"arbitrary_types_allowed": True}


class HandlerResult(BaseModel):
    """Opaque result returned by a handler. Validator runs after to check artifacts."""

    ok: bool
    llm_result: LLMResult | None = None
    message: str | None = None

    model_config = {"arbitrary_types_allowed": True}


HandlerFn = Callable[[HandlerContext], Awaitable[HandlerResult]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFn] = {}

    def register(self, task_type: str, handler: HandlerFn) -> None:
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> HandlerFn | None:
        return self._handlers.get(task_type)

    def registered_types(self) -> list[str]:
        return list(self._handlers.keys())


def _build_registry() -> HandlerRegistry:
    from handlers import (
        analyze_filing,
        cleanup_sessions,
        compile_to_wiki,
        dive_business,
        dive_financials,
        dive_moat,
        generate_daily_journal,
        lint_vault,
        notify,
        orchestrate_dive,
        rate_limit_probe,
        refresh_index,
        synthesize_memo,
        triage_filing,
    )

    r = HandlerRegistry()
    r.register("triage_filing", triage_filing.handle)
    r.register("analyze_filing", analyze_filing.handle)
    r.register("compile_to_wiki", compile_to_wiki.handle)
    r.register("notify", notify.handle)
    r.register("orchestrate_dive", orchestrate_dive.handle)
    r.register("dive_business", dive_business.handle)
    r.register("dive_moat", dive_moat.handle)
    r.register("dive_financials", dive_financials.handle)
    r.register("synthesize_memo", synthesize_memo.handle)
    r.register("refresh_index", refresh_index.handle)
    r.register("lint_vault", lint_vault.handle)
    r.register("generate_daily_journal", generate_daily_journal.handle)
    r.register("rate_limit_probe", rate_limit_probe.handle)
    r.register("cleanup_sessions", cleanup_sessions.handle)
    return r


_registry: HandlerRegistry = _build_registry()


def get_handler_registry() -> HandlerRegistry:
    return _registry
