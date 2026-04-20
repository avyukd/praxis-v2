from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    TRIAGE_FILING = "triage_filing"
    ANALYZE_FILING = "analyze_filing"
    COMPILE_TO_WIKI = "compile_to_wiki"
    NOTIFY = "notify"
    ORCHESTRATE_DIVE = "orchestrate_dive"
    # Section B specialist taxonomy (D19)
    DIVE_FINANCIAL_RIGOROUS = "dive_financial_rigorous"
    DIVE_BUSINESS_MOAT = "dive_business_moat"
    DIVE_INDUSTRY_STRUCTURE = "dive_industry_structure"
    DIVE_CAPITAL_ALLOCATION = "dive_capital_allocation"
    DIVE_GEOPOLITICAL_RISK = "dive_geopolitical_risk"
    DIVE_MACRO = "dive_macro"
    DIVE_CUSTOM = "dive_custom"
    SYNTHESIZE_MEMO = "synthesize_memo"
    REFRESH_INDEX = "refresh_index"
    LINT_VAULT = "lint_vault"
    GENERATE_DAILY_JOURNAL = "generate_daily_journal"
    RATE_LIMIT_PROBE = "rate_limit_probe"
    CLEANUP_SESSIONS = "cleanup_sessions"
    # Section D idea surfacing
    SURFACE_IDEAS = "surface_ideas"


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
    TaskType.DIVE_FINANCIAL_RIGOROUS: TaskModel.OPUS,
    TaskType.DIVE_BUSINESS_MOAT: TaskModel.OPUS,
    TaskType.DIVE_INDUSTRY_STRUCTURE: TaskModel.OPUS,
    TaskType.DIVE_CAPITAL_ALLOCATION: TaskModel.OPUS,
    TaskType.DIVE_GEOPOLITICAL_RISK: TaskModel.OPUS,
    TaskType.DIVE_MACRO: TaskModel.OPUS,
    TaskType.DIVE_CUSTOM: TaskModel.OPUS,
    TaskType.SYNTHESIZE_MEMO: TaskModel.OPUS,
    TaskType.REFRESH_INDEX: TaskModel.HAIKU,
    TaskType.LINT_VAULT: TaskModel.SONNET,
    TaskType.GENERATE_DAILY_JOURNAL: TaskModel.HAIKU,
    TaskType.RATE_LIMIT_PROBE: TaskModel.HAIKU,
    TaskType.CLEANUP_SESSIONS: TaskModel.NONE,
    TaskType.SURFACE_IDEAS: TaskModel.SONNET,
}


TASK_RESOURCE_KEYS: dict[TaskType, str | None] = {
    TaskType.TRIAGE_FILING: None,
    TaskType.ANALYZE_FILING: None,
    TaskType.COMPILE_TO_WIKI: "company",
    TaskType.NOTIFY: None,
    TaskType.ORCHESTRATE_DIVE: "investigation",
    TaskType.DIVE_FINANCIAL_RIGOROUS: "company",
    TaskType.DIVE_BUSINESS_MOAT: "company",
    TaskType.DIVE_INDUSTRY_STRUCTURE: "company",
    TaskType.DIVE_CAPITAL_ALLOCATION: "company",
    TaskType.DIVE_GEOPOLITICAL_RISK: "company",
    TaskType.DIVE_MACRO: "company",
    TaskType.DIVE_CUSTOM: "company",
    TaskType.SYNTHESIZE_MEMO: "company",
    TaskType.REFRESH_INDEX: "index",
    TaskType.LINT_VAULT: "lint",
    TaskType.GENERATE_DAILY_JOURNAL: "journal",
    TaskType.RATE_LIMIT_PROBE: None,
    TaskType.CLEANUP_SESSIONS: "cleanup",
    TaskType.SURFACE_IDEAS: "surface_ideas",
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
