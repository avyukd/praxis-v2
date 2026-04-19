from __future__ import annotations

import asyncio
import os
import signal

from praxis_core.config import get_settings
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.time_et import et_iso, now_et

log = get_logger("syncer.main")


async def _run_restic_backup() -> tuple[bool, str]:
    settings = get_settings()
    if not settings.restic_repository:
        return False, "RESTIC_REPOSITORY unset"
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = settings.restic_repository
    if settings.restic_password_file:
        env["RESTIC_PASSWORD_FILE"] = settings.restic_password_file

    cmd = [
        "restic",
        "backup",
        str(settings.vault_root),
        "--tag",
        f"vault-{now_et().strftime('%Y%m%d%H%M')}",
        "--exclude",
        ".obsidian",
        "--exclude",
        ".cache",
        "--exclude-caches",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)
        if proc.returncode == 0:
            return True, (stdout.decode("utf-8", errors="replace")[-500:])
        return False, (stderr.decode("utf-8", errors="replace")[:500])
    except TimeoutError:
        return False, "restic backup timed out after 15min"
    except FileNotFoundError:
        return False, "restic binary not found — install restic"


async def _run_restic_forget() -> tuple[bool, str]:
    settings = get_settings()
    if not settings.restic_repository:
        return False, "RESTIC_REPOSITORY unset"
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = settings.restic_repository
    if settings.restic_password_file:
        env["RESTIC_PASSWORD_FILE"] = settings.restic_password_file
    cmd = [
        "restic",
        "forget",
        "--keep-hourly",
        "24",
        "--keep-daily",
        "14",
        "--keep-weekly",
        "8",
        "--prune",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode == 0:
            return True, stdout.decode("utf-8", errors="replace")[-500:]
        return False, stderr.decode("utf-8", errors="replace")[:500]
    except TimeoutError:
        return False, "restic forget timed out"
    except FileNotFoundError:
        return False, "restic binary not found"


async def run_loop() -> None:
    configure_logging()
    settings = get_settings()
    log.info(
        "syncer.start",
        interval_s=settings.restic_snapshot_interval_s,
        repo=settings.restic_repository,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    tick = 0
    while not stop_event.is_set():
        tick += 1
        started_iso = et_iso()
        ok, output = await _run_restic_backup()
        status = {
            "last_backup_at": started_iso,
            "ok": ok,
            "tail": output[-200:],
        }
        await beat("syncer.main", status=status)
        await emit_event("syncer.main", "backup_result", {"ok": ok, "tail": output[-200:]})
        if not ok:
            log.warning("syncer.backup_failed", error=output[:500])
        else:
            log.info("syncer.backup_ok")

        # Every 24 ticks, run forget (~once a day for hourly cadence)
        if tick % 24 == 0:
            fok, foutput = await _run_restic_forget()
            await emit_event("syncer.main", "forget_result", {"ok": fok, "tail": foutput[-200:]})

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.restic_snapshot_interval_s)
        except TimeoutError:
            pass

    log.info("syncer.shutdown")


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
