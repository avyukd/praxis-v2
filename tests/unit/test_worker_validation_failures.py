from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from praxis_core.llm.invoker import LLMResult
from praxis_core.schemas.artifacts import ValidationMalformed, ValidationResult
from services.dispatcher import worker
from services.dispatcher.worker import (
    requeue_canceled_task,
    requeue_interrupted_llm_task,
    validation_failure_reason,
)


def test_validation_failure_reason_prefers_malformed_details() -> None:
    validation = ValidationResult(
        malformed=[
            ValidationMalformed(
                path="/tmp/company/dives/capital-allocation.md",
                reason="frontmatter missing ticker",
            )
        ]
    )

    reason = validation_failure_reason(validation)

    assert reason.startswith("artifacts malformed:")
    assert "capital-allocation.md" in reason
    assert "frontmatter missing ticker" in reason


def test_validation_failure_reason_uses_missing_paths_when_present() -> None:
    validation = ValidationResult(missing=["/tmp/company/dives/capital-allocation.md"])

    assert (
        validation_failure_reason(validation)
        == "artifacts missing: ['/tmp/company/dives/capital-allocation.md']"
    )


@pytest.mark.asyncio
async def test_requeue_canceled_task_releases_running_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: dict[str, object] = {}
    emitted: dict[str, object] = {}

    @asynccontextmanager
    async def _fake_session_scope():
        yield object()

    async def _fake_release_task(session, task_id, backoff_s=30):  # type: ignore[no-untyped-def]
        released["session"] = session
        released["task_id"] = task_id
        released["backoff_s"] = backoff_s

    async def _fake_emit_event(component, event_type, payload, session=None):  # type: ignore[no-untyped-def]
        emitted["component"] = component
        emitted["event_type"] = event_type
        emitted["payload"] = payload
        emitted["session"] = session

    monkeypatch.setattr(worker, "session_scope", _fake_session_scope)
    monkeypatch.setattr(worker, "release_task", _fake_release_task)
    monkeypatch.setattr(worker, "emit_event", _fake_emit_event)

    task_id = uuid4()
    task = cast(Any, SimpleNamespace(id=task_id, type="surface_ideas"))

    await requeue_canceled_task(task, backoff_s=7)

    assert released["task_id"] == task_id
    assert released["backoff_s"] == 7
    assert emitted["component"] == "dispatcher.worker"
    assert emitted["event_type"] == "task_shutdown_requeued"
    assert emitted["payload"] == {
        "task_id": str(task_id),
        "type": "surface_ideas",
        "backoff_s": 7,
    }


@pytest.mark.asyncio
async def test_requeue_interrupted_llm_task_releases_killed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: dict[str, object] = {}
    emitted: dict[str, object] = {}

    async def _fake_release_task(session, task_id, backoff_s=30):  # type: ignore[no-untyped-def]
        released["session"] = session
        released["task_id"] = task_id
        released["backoff_s"] = backoff_s

    async def _fake_emit_event(component, event_type, payload, session=None):  # type: ignore[no-untyped-def]
        emitted["component"] = component
        emitted["event_type"] = event_type
        emitted["payload"] = payload
        emitted["session"] = session

    monkeypatch.setattr(worker, "release_task", _fake_release_task)
    monkeypatch.setattr(worker, "emit_event", _fake_emit_event)

    task_id = uuid4()
    task = cast(Any, SimpleNamespace(id=task_id, type="synthesize_memo"))
    llm = LLMResult(
        text="",
        duration_s=1.0,
        finish_reason="killed",
        model="opus",
        invoker="cli",
    )
    session = object()

    requeued = await requeue_interrupted_llm_task(session, task, llm, backoff_s=9)

    assert requeued is True
    assert released["task_id"] == task_id
    assert released["backoff_s"] == 9
    assert emitted["event_type"] == "task_interrupted_requeued"
    assert emitted["payload"] == {
        "task_id": str(task_id),
        "type": "synthesize_memo",
        "backoff_s": 9,
    }
