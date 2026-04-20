from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[str]: ARRAY(String),
        list[uuid.UUID]: ARRAY(UUID(as_uuid=True)),
    }


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dedup_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    resource_key: Mapped[str | None] = mapped_column(String(128))
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="SET NULL")
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    # depends_on column dropped per D26 (dead code)

    lease_holder: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rate_limit_bounces: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_error: Mapped[str | None] = mapped_column(Text)
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    telemetry: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    investigation: Mapped[Investigation | None] = relationship(
        "Investigation", back_populates="tasks", foreign_keys=[investigation_id]
    )

    __table_args__ = (
        Index(
            "idx_tasks_dispatch",
            "status",
            "priority",
            "created_at",
            postgresql_where=text("status IN ('queued', 'partial')"),
        ),
        Index(
            "idx_tasks_resource",
            "resource_key",
            "status",
            postgresql_where=text("status = 'running'"),
        ),
        Index("idx_tasks_investigation", "investigation_id"),
        Index("idx_tasks_type_status", "type", "status"),
    )


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    handle: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    initiated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    entry_nodes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_progress_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    artifacts: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    vault_path: Mapped[str | None] = mapped_column(String(512))
    # D21 — research priority 0-10 (drives ResearchBudget)
    research_priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )

    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="investigation", foreign_keys="Task.investigation_id"
    )


class RateLimitState(Base):
    __tablename__ = "rate_limit_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, server_default="1")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="clear", server_default="'clear'"
    )
    limited_until_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_hits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_hit_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probe_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (CheckConstraint("id = 1", name="rate_limit_singleton"),)


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    component: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    component: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_events_recent", "ts", postgresql_using="btree"),
        Index("idx_events_component_type", "component", "event_type"),
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    dedup_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    vault_path: Mapped[str] = mapped_column(String(512), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(16))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeadLetterTask(Base):
    __tablename__ = "dead_letter_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    original_task: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    final_error: Mapped[str | None] = mapped_column(Text)
    failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MarketCapCache(Base):
    __tablename__ = "market_cap_cache"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    market_cap_usd: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="yfinance", server_default="'yfinance'"
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)


class SignalFired(Base):
    __tablename__ = "signals_fired"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    ticker: Mapped[str | None] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    urgency: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_signals_fired_at", "fired_at"),)
