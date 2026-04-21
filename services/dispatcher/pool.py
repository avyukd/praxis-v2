from __future__ import annotations

import asyncio
from dataclasses import dataclass

from praxis_core.db.models import Task
from praxis_core.logging import get_logger

log = get_logger("dispatcher.pool")


@dataclass
class RunningTask:
    task: Task
    async_task: asyncio.Task
    worker_id: str
    resource_key: str | None


class WorkerPool:
    def __init__(self, size: int) -> None:
        self.size = size
        self._running: dict[str, RunningTask] = {}
        self._worker_seq = 0
        self._lock = asyncio.Lock()

    def available_slots(self) -> int:
        return max(0, self.size - len(self._running))

    def running_resource_keys(self) -> list[str]:
        return [r.resource_key for r in self._running.values() if r.resource_key]

    def running_tasks(self) -> list[RunningTask]:
        return list(self._running.values())

    def alloc_worker_id(self) -> str:
        self._worker_seq += 1
        return f"worker-{self._worker_seq:04d}"

    async def submit(self, task: Task, coro, worker_id: str | None = None) -> RunningTask:
        async with self._lock:
            if worker_id is None:
                worker_id = self.alloc_worker_id()
            async_task = asyncio.create_task(coro, name=f"{worker_id}:{task.type}:{task.id}")
            rt = RunningTask(
                task=task,
                async_task=async_task,
                worker_id=worker_id,
                resource_key=task.resource_key,
            )
            self._running[worker_id] = rt

            def _cleanup(_: asyncio.Task) -> None:
                self._running.pop(worker_id, None)

            async_task.add_done_callback(_cleanup)
            log.info(
                "pool.submit",
                worker_id=worker_id,
                task_id=str(task.id),
                task_type=task.type,
                resource_key=task.resource_key,
                running=len(self._running),
            )
            return rt

    async def drain(self, timeout_s: float = 30.0) -> None:
        pending = [r.async_task for r in self._running.values()]
        if not pending:
            return
        log.info("pool.drain.start", pending=len(pending), timeout_s=timeout_s)
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=timeout_s
            )
        except TimeoutError:
            for t in pending:
                if not t.done():
                    t.cancel()
        log.info("pool.drain.done")
