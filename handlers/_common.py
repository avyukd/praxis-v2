from __future__ import annotations

from pathlib import Path

from praxis_core.llm.invoker import LLMResult, get_invoker
from praxis_core.schemas.task_types import TaskModel
from praxis_core.vault import conventions as vc


async def run_llm(
    *,
    system_prompt: str,
    user_prompt: str,
    model: TaskModel,
    max_budget_usd: float | None = None,
    vault_root: Path,
    allowed_tools: list[str] | None = None,
) -> LLMResult:
    invoker = get_invoker()
    mcp_cfg = _mcp_config_for_vault(vault_root)
    return await invoker.run(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_budget_usd=max_budget_usd,
        mcp_config_path=str(mcp_cfg) if mcp_cfg else None,
        allowed_tools=allowed_tools
        or [
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "Bash(mkdir:*)",
        ],
    )


def _mcp_config_for_vault(vault_root: Path) -> Path | None:
    """Look for an MCP config file the worker can use.

    We write a generic one at <vault>/.mcp-config.json that points at our MCP server.
    If not present, invoker runs without MCP.
    """
    cfg = vault_root / ".mcp-config.json"
    return cfg if cfg.exists() else None


SYSTEM_PROMPT_PREFIX = """You are a worker in the praxis-v2 research system. You will be given a single task.
Your output MUST be validated artifacts in the vault (at the paths specified in your instructions).
Follow these rules strictly:
- Write files atomically; the task will be validated by checking artifacts exist and parse.
- Cite every quantitative claim with a wikilink to a source under _raw/ or _analyzed/.
- Respect YAML frontmatter conventions; refer to vault/CLAUDE.md if unsure.
- If you cannot produce a valid artifact, write what you can and exit. A partial result triggers a remediation task.
- Do not open browser tools, only use tools explicitly allowed.
"""


def read_vault_schema(vault_root: Path) -> str:
    p = vc.schema_path(vault_root)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""
