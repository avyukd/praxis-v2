from __future__ import annotations

from pathlib import Path

from handlers import HandlerContext, HandlerResult
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import RefreshIndexPayload
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("handlers.refresh_index")


def _collect_nodes(vault_root: Path) -> dict[str, list[str]]:
    def _list(dir_path: Path) -> list[str]:
        """Top-level *.md plus one level of <dir>/index.md stubs.

        Some nodes (themes, concepts) are single files; others (companies,
        sometimes investigations) are directories with a stub file inside.
        We want both in the index.
        """
        if not dir_path.exists():
            return []
        top = list(dir_path.glob("*.md"))
        stubs = list(dir_path.glob("*/index.md")) + list(dir_path.glob("*/notes.md"))
        return sorted(p.relative_to(vault_root).as_posix() for p in top + stubs)

    return {
        "companies": _list(vault_root / "companies"),
        "themes": _list(vault_root / "themes"),
        "concepts": _list(vault_root / "concepts"),
        "people": _list(vault_root / "people"),
        "questions": _list(vault_root / "questions"),
        "investigations": _list(vault_root / "investigations"),
        "memos": _list(vault_root / "memos"),
    }


def _render_index(nodes: dict[str, list[str]], ran_at: str) -> str:
    parts = [
        "# INDEX",
        "",
        f"Auto-maintained map of content. Last refresh: {ran_at}",
        "",
    ]
    for section_name, paths in nodes.items():
        parts.append(f"## {section_name.title()} ({len(paths)})")
        if not paths:
            parts.append("_empty_")
        else:
            for p in paths:
                if p.endswith("/notes.md") or p.endswith("/index.md"):
                    handle = p.split("/")[-2]
                else:
                    handle = Path(p).stem
                parts.append(f"- [[{p}|{handle}]]")
        parts.append("")
    return "\n".join(parts) + "\n"


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = RefreshIndexPayload.model_validate(ctx.payload)
    nodes = _collect_nodes(ctx.vault_root)
    ran_at = et_iso()
    content = _render_index(nodes, ran_at)
    atomic_write(vc.index_path(ctx.vault_root), content)
    log.info(
        "refresh_index.done",
        scope=payload.scope,
        counts={k: len(v) for k, v in nodes.items()},
    )
    return HandlerResult(ok=True, message="index refreshed")
