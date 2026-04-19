from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    TRIAGE_FILING = "triage_filing"
    ANALYZE_FILING = "analyze_filing"
    COMPILE_TO_WIKI = "compile_to_wiki"
    NOTIFY = "notify"
    ORCHESTRATE_DIVE = "orchestrate_dive"
    DIVE_BUSINESS = "dive_business"
    DIVE_MOAT = "dive_moat"
    DIVE_FINANCIALS = "dive_financials"
    SYNTHESIZE_MEMO = "synthesize_memo"
    REFRESH_INDEX = "refresh_index"
    LINT_VAULT = "lint_vault"
    GENERATE_DAILY_JOURNAL = "generate_daily_journal"
    RATE_LIMIT_PROBE = "rate_limit_probe"
    CLEANUP_SESSIONS = "cleanup_sessions"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PARTIAL = "partial"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELED = "canceled"


class TaskModel(StrEnum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"
    NONE = "none"


MODEL_TIERS: dict[TaskType, TaskModel] = {
    TaskType.TRIAGE_FILING: TaskModel.HAIKU,
    TaskType.ANALYZE_FILING: TaskModel.SONNET,
    TaskType.COMPILE_TO_WIKI: TaskModel.SONNET,
    TaskType.NOTIFY: TaskModel.NONE,
    TaskType.ORCHESTRATE_DIVE: TaskModel.SONNET,
    TaskType.DIVE_BUSINESS: TaskModel.OPUS,
    TaskType.DIVE_MOAT: TaskModel.OPUS,
    TaskType.DIVE_FINANCIALS: TaskModel.OPUS,
    TaskType.SYNTHESIZE_MEMO: TaskModel.OPUS,
    TaskType.REFRESH_INDEX: TaskModel.HAIKU,
    TaskType.LINT_VAULT: TaskModel.SONNET,
    TaskType.GENERATE_DAILY_JOURNAL: TaskModel.HAIKU,
    TaskType.RATE_LIMIT_PROBE: TaskModel.HAIKU,
    TaskType.CLEANUP_SESSIONS: TaskModel.NONE,
}


TASK_RESOURCE_KEYS: dict[TaskType, str | None] = {
    TaskType.TRIAGE_FILING: None,
    TaskType.ANALYZE_FILING: None,
    TaskType.COMPILE_TO_WIKI: "company",
    TaskType.NOTIFY: None,
    TaskType.ORCHESTRATE_DIVE: "investigation",
    TaskType.DIVE_BUSINESS: "company",
    TaskType.DIVE_MOAT: "company",
    TaskType.DIVE_FINANCIALS: "company",
    TaskType.SYNTHESIZE_MEMO: "company",
    TaskType.REFRESH_INDEX: "index",
    TaskType.LINT_VAULT: "lint",
    TaskType.GENERATE_DAILY_JOURNAL: "journal",
    TaskType.RATE_LIMIT_PROBE: None,
    TaskType.CLEANUP_SESSIONS: "cleanup",
}


MODEL_TO_CLI_FLAG: dict[TaskModel, str] = {
    TaskModel.HAIKU: "claude-haiku-4-5-20251001",
    TaskModel.SONNET: "claude-sonnet-4-6",
    TaskModel.OPUS: "claude-opus-4-7",
    TaskModel.NONE: "",
}


MODEL_TO_API_NAME: dict[TaskModel, str] = {
    TaskModel.HAIKU: "claude-haiku-4-5",
    TaskModel.SONNET: "claude-sonnet-4-6",
    TaskModel.OPUS: "claude-opus-4-7",
    TaskModel.NONE: "",
}
