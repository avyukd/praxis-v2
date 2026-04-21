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
    # Wiki connectivity maintenance (no-LLM graph traversal)
    REFRESH_BACKLINKS = "refresh_backlinks"
    TICKER_INDEX = "ticker_index"
    # Open-ended research engine (broad-topic prompts → cross-cutting memo)
    ORCHESTRATE_RESEARCH = "orchestrate_research"
    GATHER_SOURCES = "gather_sources"
    COMPILE_RESEARCH_NODE = "compile_research_node"
    ANSWER_QUESTION = "answer_question"
    SCREEN_CANDIDATE_COMPANIES = "screen_candidate_companies"
    SYNTHESIZE_CROSSCUT_MEMO = "synthesize_crosscut_memo"


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
    TaskType.REFRESH_BACKLINKS: TaskModel.NONE,
    TaskType.TICKER_INDEX: TaskModel.NONE,
    TaskType.ORCHESTRATE_RESEARCH: TaskModel.SONNET,
    TaskType.GATHER_SOURCES: TaskModel.SONNET,
    TaskType.COMPILE_RESEARCH_NODE: TaskModel.SONNET,
    TaskType.ANSWER_QUESTION: TaskModel.SONNET,
    TaskType.SCREEN_CANDIDATE_COMPANIES: TaskModel.SONNET,
    TaskType.SYNTHESIZE_CROSSCUT_MEMO: TaskModel.OPUS,
}


TASK_RESOURCE_KEYS: dict[TaskType, str | None] = {
    TaskType.TRIAGE_FILING: None,
    TaskType.ANALYZE_FILING: None,
    # compile_to_wiki needs exclusive access to notes.md — serialize per ticker.
    TaskType.COMPILE_TO_WIKI: "company",
    TaskType.NOTIFY: None,
    # One orchestrator per investigation; the dive plan fan-out runs concurrently.
    TaskType.ORCHESTRATE_DIVE: "investigation",
    # Dives write to per-specialty files (companies/<T>/dives/<specialty>.md) —
    # no shared-state conflict. Parallel execution means the full dive chain
    # finishes in ~15min wall instead of ~60min serial, and each specialist
    # can't crib from peer verdicts (breaks echo chamber, forces independent
    # retrieval + analysis per specialty).
    TaskType.DIVE_FINANCIAL_RIGOROUS: None,
    TaskType.DIVE_BUSINESS_MOAT: None,
    TaskType.DIVE_INDUSTRY_STRUCTURE: None,
    TaskType.DIVE_CAPITAL_ALLOCATION: None,
    TaskType.DIVE_GEOPOLITICAL_RISK: None,
    TaskType.DIVE_MACRO: None,
    TaskType.DIVE_CUSTOM: None,
    # synthesize_memo reads all dives + writes a single memo — needs to wait
    # for dives and hold exclusive ticker-state while writing.
    TaskType.SYNTHESIZE_MEMO: "company",
    TaskType.REFRESH_INDEX: "index",
    TaskType.LINT_VAULT: "lint",
    TaskType.GENERATE_DAILY_JOURNAL: "journal",
    TaskType.RATE_LIMIT_PROBE: None,
    TaskType.CLEANUP_SESSIONS: "cleanup",
    TaskType.SURFACE_IDEAS: "surface_ideas",
    # These walk the whole vault; serialize to avoid parallel full-scans.
    TaskType.REFRESH_BACKLINKS: "wiki_mgmt",
    TaskType.TICKER_INDEX: "wiki_mgmt",
    # Open-ended research. Keys are literal sentinels — the real resource
    # families (theme:/question:/concept:/basket:/crosscutting:) are
    # resolved per-task in enqueue._resource_key_for() because the target
    # node slug lives in the payload, not the TaskType.
    TaskType.ORCHESTRATE_RESEARCH: "investigation",
    TaskType.GATHER_SOURCES: None,  # parallel retrieval OK
    # compile/answer/synthesize map to node-specific keys via enqueue helper
    TaskType.COMPILE_RESEARCH_NODE: "research_node",
    TaskType.ANSWER_QUESTION: "research_node",
    TaskType.SCREEN_CANDIDATE_COMPANIES: None,
    TaskType.SYNTHESIZE_CROSSCUT_MEMO: "crosscutting",
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
