from __future__ import annotations

import asyncio
import hashlib
import json
import re
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from praxis_core.config import get_settings
from praxis_core.db.models import Source
from praxis_core.db.session import session_scope
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("pollers.inbox_watcher")

SUPPORTED_EXTS = {".md", ".txt", ".html"}


def _slugify(name: str) -> str:
    base = Path(name).stem
    base = re.sub(r"[^a-zA-Z0-9_\-]+", "-", base.lower()).strip("-")
    return base or "unnamed"


async def _process_file(file_path: Path) -> bool:
    settings = get_settings()
    if file_path.suffix.lower() not in SUPPORTED_EXTS:
        log.info("inbox.skip_unsupported", path=str(file_path))
        return False

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("inbox.read_fail", path=str(file_path), error=str(e))
        return False

    dedup = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    dt = datetime.now(timezone.utc)
    slug = _slugify(file_path.name)
    target = vc.raw_manual_path(settings.vault_root, dt, f"{slug}-{dedup}")

    async with session_scope() as session:
        stmt = insert(Source).values(
            dedup_key=f"manual:{dedup}",
            source_type="manual",
            vault_path=str(target.relative_to(settings.vault_root)),
            extra={
                "original_name": file_path.name,
                "ingested_at": dt.isoformat(),
            },
        ).on_conflict_do_nothing(index_elements=[Source.dedup_key]).returning(Source.id)
        row = (await session.execute(stmt)).first()
        was_new = row is not None

    if not was_new:
        log.info("inbox.duplicate", path=str(file_path))
        try:
            file_path.unlink()
        except OSError:
            pass
        return False

    # Wrap content with frontmatter marker so downstream tasks can identify it
    body = content
    if not body.startswith("---"):
        body = (
            "---\n"
            "type: source\n"
            f"source_kind: manual\n"
            f"original_name: {file_path.name}\n"
            f"ingested_at: {dt.isoformat()}\n"
            "---\n\n" + body
        )

    atomic_write(target, body)

    rel = str(target.relative_to(settings.vault_root))
    async with session_scope() as session:
        await enqueue_task(
            session,
            task_type=TaskType.COMPILE_TO_WIKI,
            payload={
                "source_kind": "manual_source",
                "analysis_path": rel,
            },
            priority=3,  # P3 for human drops
            dedup_key=f"compile_manual:{dedup}",
        )

    try:
        file_path.unlink()
    except OSError:
        pass

    await emit_event(
        "pollers.inbox_watcher",
        "manual_ingested",
        {"dedup": dedup, "target": rel},
    )
    log.info("inbox.ingested", file=file_path.name, target=rel)
    return True


async def _scan_once() -> int:
    settings = get_settings()
    inbox = settings.inbox_root
    inbox.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in sorted(inbox.iterdir()):
        if entry.is_file():
            if await _process_file(entry):
                count += 1
    return count


async def run_loop(interval_s: int = 10) -> None:
    configure_logging()
    log.info("inbox.start", interval_s=interval_s)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    while not stop_event.is_set():
        try:
            count = await _scan_once()
            await beat(
                "pollers.inbox_watcher",
                status={
                    "last_scan_at": datetime.now(timezone.utc).isoformat(),
                    "ingested": count,
                },
            )
        except Exception as e:
            log.exception("inbox.scan_fail", error=str(e))
            await beat(
                "pollers.inbox_watcher",
                status={"last_scan_at": datetime.now(timezone.utc).isoformat(), "error": str(e)[:200]},
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass

    log.info("inbox.shutdown")


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
