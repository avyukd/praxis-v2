from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from handlers import HandlerRegistry, HandlerResult
from praxis_core.llm.invoker import LLMResult
from praxis_core.schemas.artifacts import ValidationMalformed, ValidationResult
from services.dispatcher import worker
from services.dispatcher.worker import (
    requeue_canceled_task,
    requeue_interrupted_llm_task,
    retry_payload_patch,
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


def test_retry_payload_patch_targets_dive_validation_failures() -> None:
    task = cast(
        Any,
        SimpleNamespace(
            type="dive_business_moat",
            payload={"ticker": "TRAX", "_retry_count": 1},
        ),
    )

    patch = retry_payload_patch(
        task,
        "artifacts malformed: /tmp/company/dives/business-moat.md: need >=1 web retrieval",
    )

    assert patch == {
        "_retry_reason": "artifacts malformed: /tmp/company/dives/business-moat.md: need >=1 web retrieval",
        "_retry_count": 2,
    }


def test_retry_payload_patch_ignores_non_dive_failures() -> None:
    task = cast(Any, SimpleNamespace(type="surface_ideas", payload={}))
    assert retry_payload_patch(task, "artifacts malformed: x") is None


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


@pytest.mark.asyncio
async def test_rate_limit_probe_still_limited_marks_success_not_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marked_success: dict[str, object] = {}
    marked_failed: dict[str, object] = {}
    emitted: list[tuple[str, str, dict[str, object]]] = []

    @asynccontextmanager
    async def _fake_session_scope():
        yield object()

    async def _fake_handler(_ctx):  # type: ignore[no-untyped-def]
        return HandlerResult(
            ok=False,
            message="rate_limit",
            llm_result=LLMResult(
                text="",
                duration_s=1.0,
                finish_reason="rate_limit",
                model="haiku",
                invoker="cli",
            ),
        )

    registry = HandlerRegistry()
    registry.register("rate_limit_probe", _fake_handler)

    async def _fake_mark_success(session, task_id, validation=None):  # type: ignore[no-untyped-def]
        marked_success["session"] = session
        marked_success["task_id"] = task_id
        marked_success["validation"] = validation

    async def _fake_mark_failed(session, task_id, error, telemetry=None):  # type: ignore[no-untyped-def]
        marked_failed["session"] = session
        marked_failed["task_id"] = task_id
        marked_failed["error"] = error
        marked_failed["telemetry"] = telemetry

    async def _fake_emit_event(component, event_type, payload, session=None):  # type: ignore[no-untyped-def]
        emitted.append((component, event_type, payload))

    async def _noop(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None

    async def _never_requeue(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return False

    class _FakeRateLimiter:
        async def record_hit(self, session, upstream_resets_at=None):  # type: ignore[no-untyped-def]
            return True

        async def reset_consecutive_hits(self, session):  # type: ignore[no-untyped-def]
            return None

        async def probe_succeeded(self, session):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(worker, "session_scope", _fake_session_scope)
    monkeypatch.setattr(worker, "get_handler_registry", lambda: registry)
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(
            worker_heartbeat_interval_s=60,
            worker_cancel_poll_interval_s=60,
            cli_wall_clock_timeout_s=60,
            vault_root=tmp_path,
        ),
    )
    monkeypatch.setattr(worker, "_heartbeat_loop", _noop)
    monkeypatch.setattr(worker, "_cancel_watch_loop", _noop)
    monkeypatch.setattr(worker, "record_task_telemetry", _noop)
    monkeypatch.setattr(worker, "requeue_interrupted_llm_task", _never_requeue)
    monkeypatch.setattr(worker, "RateLimitManager", _FakeRateLimiter)
    monkeypatch.setattr(worker, "mark_success", _fake_mark_success)
    monkeypatch.setattr(worker, "mark_failed", _fake_mark_failed)
    monkeypatch.setattr(worker, "emit_event", _fake_emit_event)

    task_id = uuid4()
    task = cast(
        Any,
        SimpleNamespace(
            id=task_id,
            type="rate_limit_probe",
            payload={"probe_id": str(uuid4())},
            model="haiku",
            rate_limit_bounces=0,
            attempts=1,
            max_attempts=3,
        ),
    )

    await worker.execute_task(task, "worker-0001")

    assert marked_success["task_id"] == task_id
    assert marked_failed == {}
    assert any(
        event_type == "task_rate_limit"
        and payload == {
            "task_id": str(task_id),
            "type": "rate_limit_probe",
            "probe_single_use": True,
            "probe_outcome": "still_limited",
        }
        for _, event_type, payload in emitted
    )
