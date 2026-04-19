from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from praxis_core.llm.invoker import LLMResult


class HandlerContext(BaseModel):
    task_id: str
    task_type: str
    payload: dict[str, Any]
    vault_root: Path
    model: str

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


_registry = HandlerRegistry()


def get_handler_registry() -> HandlerRegistry:
    """Register all handlers on first access."""
    if not _registry._handlers:
        _register_all()
    return _registry


def _register_all() -> None:
    from handlers import (
        analyze_filing,
        compile_to_wiki,
        dive_business,
        dive_financials,
        dive_moat,
        generate_daily_journal,
        lint_vault,
        notify,
        orchestrate_dive,
        refresh_index,
        synthesize_memo,
        triage_filing,
    )

    _registry.register("triage_filing", triage_filing.handle)
    _registry.register("analyze_filing", analyze_filing.handle)
    _registry.register("compile_to_wiki", compile_to_wiki.handle)
    _registry.register("notify", notify.handle)
    _registry.register("orchestrate_dive", orchestrate_dive.handle)
    _registry.register("dive_business", dive_business.handle)
    _registry.register("dive_moat", dive_moat.handle)
    _registry.register("dive_financials", dive_financials.handle)
    _registry.register("synthesize_memo", synthesize_memo.handle)
    _registry.register("refresh_index", refresh_index.handle)
    _registry.register("lint_vault", lint_vault.handle)
    _registry.register("generate_daily_journal", generate_daily_journal.handle)
