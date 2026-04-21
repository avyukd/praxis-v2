from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from handlers import HandlerContext, analyze_filing, compile_to_wiki, notify
from handlers import _common as common
from praxis_core.llm.invoker import LLMResult
from praxis_core.schemas.artifacts import AnalysisResult
from praxis_core.schemas.payloads import AnalyzeFilingPayload
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.enqueue import _resource_key_for
from praxis_core.tasks.validators import validate_compile_to_wiki


@pytest.mark.asyncio
async def test_run_llm_honors_empty_allowed_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class _StubInvoker:
        async def run(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return LLMResult(
                text="ok",
                duration_s=0.01,
                finish_reason="stop",
                model="haiku",
                invoker="cli",
            )

    monkeypatch.setattr(common, "get_invoker", lambda: _StubInvoker())
    await common.run_llm(
        system_prompt="sys",
        user_prompt="usr",
        model=TaskModel.HAIKU,
        vault_root=tmp_path,
        allowed_tools=[],
    )
    assert captured["allowed_tools"] == []


def test_resource_key_singletons_round2():
    assert _resource_key_for(TaskType.CLEANUP_SESSIONS, {}) == "cleanup:singleton"
    assert _resource_key_for(TaskType.SURFACE_IDEAS, {}) == "surface_ideas:singleton"
    assert _resource_key_for(TaskType.REFRESH_BACKLINKS, {}) == "wiki_mgmt:singleton"
    assert _resource_key_for(TaskType.TICKER_INDEX, {}) == "wiki_mgmt:singleton"


@pytest.mark.asyncio
async def test_compile_manual_source_prompt_avoids_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    captured: dict[str, str] = {}

    async def _fake_run_llm(**kwargs):  # type: ignore[no-untyped-def]
        captured["user_prompt"] = kwargs["user_prompt"]
        return LLMResult(
            text="ok",
            duration_s=0.01,
            finish_reason="stop",
            model="sonnet",
            invoker="cli",
        )

    monkeypatch.setattr(compile_to_wiki, "run_llm", _fake_run_llm)
    monkeypatch.setattr(compile_to_wiki, "read_vault_schema", lambda _v: "")

    ctx = HandlerContext(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.COMPILE_TO_WIKI.value,
        payload={"source_kind": "manual_source", "analysis_path": "_raw/manual/a.md"},
        vault_root=tmp_path,
        model=TaskModel.SONNET.value,
        session=None,
    )
    result = await compile_to_wiki.handle(ctx)
    assert result.ok
    assert "manual/sources.md" in captured["user_prompt"]
    assert "Ticker:" not in captured["user_prompt"]


def test_validate_compile_to_wiki_manual_source_without_ticker(tmp_path: Path):
    (tmp_path / "LOG.md").write_text("# log\n", encoding="utf-8")
    (tmp_path / "manual").mkdir(parents=True, exist_ok=True)
    (tmp_path / "manual" / "sources.md").write_text(
        "Manual source note with [[_raw/manual/2026-04-20/manual-abc.md]]\n",
        encoding="utf-8",
    )
    payload = {
        "source_kind": "manual_source",
        "analysis_path": "_raw/manual/2026-04-20/manual-abc.md",
    }
    r = validate_compile_to_wiki(payload, tmp_path)
    assert r.is_success


@pytest.mark.asyncio
async def test_analyze_downstream_dedup_source_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dedups: list[str] = []

    async def _fake_enqueue_task(session, **kwargs):  # type: ignore[no-untyped-def]
        _ = session
        dedups.append(kwargs["dedup_key"])
        return uuid.uuid4()

    monkeypatch.setattr(analyze_filing, "enqueue_task", _fake_enqueue_task)
    @asynccontextmanager
    async def _fake_session_scope():
        yield object()
    monkeypatch.setattr(analyze_filing, "session_scope", _fake_session_scope)

    ctx = HandlerContext(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.ANALYZE_FILING.value,
        payload={},
        vault_root=tmp_path,
        model=TaskModel.SONNET.value,
        session=None,
    )
    payload = AnalyzeFilingPayload(
        accession="gnw-123",
        form_type="press_release",
        ticker="ABC",
        raw_path="_raw/press_releases/gnw/ABC/gnw-123/release.txt",
        source="gnw",
        release_id="gnw-123",
    )
    out_dir = tmp_path / "_analyzed" / "press_releases" / "gnw" / "ABC" / "gnw-123"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = AnalysisResult(
        accession="gnw-123",
        ticker="ABC",
        form_type="press_release",
        source="gnw",
        classification="positive",
        magnitude=0.7,
        new_information="x",
        materiality="x",
        explanation="x",
        analyzed_at="2026-04-20T10:00:00-04:00",
        model="sonnet",
    )
    await analyze_filing._enqueue_downstream(ctx, payload, result, out_dir)
    assert any(d == "notify:press_release:gnw:gnw-123" for d in dedups)
    assert any(d == "compile:press_release:gnw:gnw-123" for d in dedups)


@pytest.mark.asyncio
async def test_analyze_press_release_mcap_fallback_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    raw = tmp_path / "_raw" / "press_releases" / "gnw" / "ABC" / "gnw-1" / "release.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("sample release", encoding="utf-8")

    calls: list[str] = []

    async def _fake_get_cached_mcap(_session, ticker: str):  # type: ignore[no-untyped-def]
        calls.append(ticker)
        if ticker == "ABC.V":
            return 123_000_000
        return None

    class _DummySession:
        pass

    @asynccontextmanager
    async def _fake_session_scope():
        yield _DummySession()

    run_calls = {"n": 0}

    async def _fake_run_llm(**kwargs):  # type: ignore[no-untyped-def]
        run_calls["n"] += 1
        if run_calls["n"] == 1:
            return LLMResult(
                text="positive",
                duration_s=0.01,
                finish_reason="stop",
                model="haiku",
                invoker="cli",
            )
        return LLMResult(
            text=json.dumps(
                {
                    "classification": "neutral",
                    "magnitude": 0.1,
                    "new_information": "x",
                    "materiality": "x",
                    "explanation": "x",
                }
            ),
            duration_s=0.01,
            finish_reason="stop",
            model="sonnet",
            invoker="cli",
        )

    monkeypatch.setattr(analyze_filing, "get_cached_mcap", _fake_get_cached_mcap)
    monkeypatch.setattr(analyze_filing, "session_scope", _fake_session_scope)
    monkeypatch.setattr(analyze_filing, "run_llm", _fake_run_llm)
    monkeypatch.setattr(analyze_filing, "_enqueue_downstream", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(analyze_filing, "constitution_prompt_block", lambda _v: "")

    ctx = HandlerContext(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.ANALYZE_FILING.value,
        payload={
            "accession": "gnw-1",
            "form_type": "press_release",
            "ticker": "ABC",
            "raw_path": str(raw.relative_to(tmp_path)),
            "source": "gnw",
            "release_id": "gnw-1",
        },
        vault_root=tmp_path,
        model=TaskModel.SONNET.value,
        session=None,
    )

    result = await analyze_filing.handle(ctx)
    assert result.ok
    assert calls[:3] == ["ABC", "ABC.TO", "ABC.V"]


@pytest.mark.asyncio
async def test_notify_uses_async_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    called = {"ok": False}

    async def _fake_push(topic_url: str, title: str, body: str, priority: str = "default") -> None:
        _ = (topic_url, title, body, priority)
        called["ok"] = True

    class _DummySession:
        def add(self, _obj):
            return None

    monkeypatch.setattr(notify, "_push_ntfy", _fake_push)
    @asynccontextmanager
    async def _fake_session_scope():
        yield _DummySession()
    monkeypatch.setattr(notify, "session_scope", _fake_session_scope)

    ctx = HandlerContext(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.NOTIFY.value,
        payload={
            "ticker": "ABC",
            "signal_type": "test_signal",
            "urgency": "high",
            "title": "Title",
            "body": "Body",
        },
        vault_root=tmp_path,
        model=TaskModel.NONE.value,
        session=None,
    )
    result = await notify.handle(ctx)
    assert result.ok
    assert called["ok"] is True


def test_orchestrate_no_substring_initiated_by_heuristic():
    src = Path("handlers/orchestrate_dive.py").read_text(encoding="utf-8")
    assert '"observer" in (payload.thesis_handle or "")' not in src
