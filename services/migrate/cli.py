"""praxis-migrate CLI — run from repo root.

Usage:
  uv run python -m services.migrate.cli plan \
      --autoresearch-vault ~/dev/praxis-autoresearch/vault \
      --copilot-workspace ~/dev/praxis-copilot/workspace \
      --copilot-data ~/dev/praxis-copilot/data \
      --target ~/vault-staging

  uv run python -m services.migrate.cli apply --target ~/vault-staging (same flags)

  uv run python -m services.migrate.cli validate --target ~/vault-staging

  uv run python -m services.migrate.cli import-copilot-state \
      --copilot-data ~/dev/praxis-copilot/data --apply
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import click

from praxis_core.db.session import session_scope
from praxis_core.logging import configure_logging, get_logger
from praxis_core.vault.writer import atomic_write
from services.migrate.copilot_state import import_copilot_state
from services.migrate.vault_migrator import apply as apply_vault
from services.migrate.vault_migrator import plan as plan_vault
from services.migrate.workspace_migrator import migrate_workspace

log = get_logger("migrate.cli")


@click.group()
def cli() -> None:
    configure_logging()


def _pp(p: str | None) -> Path | None:
    return Path(p).expanduser() if p else None


@cli.command()
@click.option("--autoresearch-vault", required=True, type=click.Path())
@click.option("--copilot-workspace", type=click.Path())
@click.option("--copilot-data", type=click.Path())
@click.option("--target", required=True, type=click.Path())
@click.option("--report-path", type=click.Path(), default=None)
def plan(
    autoresearch_vault: str,
    copilot_workspace: str | None,
    copilot_data: str | None,
    target: str,
    report_path: str | None,
) -> None:
    """Dry-run; emit a migration report. No writes to target."""
    ar = Path(autoresearch_vault).expanduser()
    tgt = Path(target).expanduser()

    click.echo(f"Planning vault migration: {ar} → {tgt}")
    _, vault_report = plan_vault(ar, tgt)
    full_report = vault_report.render()

    if copilot_workspace:
        ws = Path(copilot_workspace).expanduser()
        ticker_dirs = sorted(d for d in ws.iterdir() if d.is_dir() and d.name != "analyst")
        full_report += "\n\n" + "\n".join(
            [
                "# Workspace migration (plan)",
                "",
                f"Would scan {len(ticker_dirs)} ticker directories in {ws}.",
                "Use `apply` to execute.",
                "",
            ]
        )

    if report_path:
        out = Path(report_path).expanduser()
    else:
        out = tgt.parent / f"{tgt.name}-migration-plan.md"
    atomic_write(out, full_report)
    click.echo(f"Plan report written to {out}")
    click.echo("")
    click.echo("Summary:")
    click.echo(f"  Files considered: {vault_report.entries_total}")
    click.echo(f"  Planned writes: {vault_report.files_written}")
    click.echo(f"  Drops: {vault_report.files_dropped}")
    click.echo(f"  Passthrough/unhandled: {vault_report.files_passthrough}")
    click.echo(f"  Wikilinks to rewrite: {vault_report.wikilinks_rewritten}")
    click.echo(f"  Unresolved wikilinks (would break): {len(vault_report.unresolved_wikilinks)}")


@cli.command()
@click.option("--autoresearch-vault", required=True, type=click.Path())
@click.option("--copilot-workspace", type=click.Path())
@click.option("--target", required=True, type=click.Path())
@click.option(
    "--force/--no-force",
    default=False,
    help="Allow writing into a non-empty target (otherwise refuse to clobber).",
)
def apply(
    autoresearch_vault: str,
    copilot_workspace: str | None,
    target: str,
    force: bool,
) -> None:
    """Execute the migration: writes into target vault."""
    ar = Path(autoresearch_vault).expanduser()
    tgt = Path(target).expanduser()

    if tgt.exists() and any(tgt.iterdir()) and not force:
        raise click.ClickException(
            f"target {tgt} is non-empty. Pass --force to proceed, "
            "or delete / move the existing contents first."
        )
    tgt.mkdir(parents=True, exist_ok=True)

    click.echo(f"Applying vault migration: {ar} → {tgt}")
    vault_report = apply_vault(ar, tgt)
    click.echo(vault_report.render())

    if copilot_workspace:
        ws = Path(copilot_workspace).expanduser()
        click.echo(f"Applying workspace migration: {ws} → {tgt}")
        # Build rename map once more so workspace memos inherit wikilink rewriting
        from services.migrate.rename_map import build_rename_map, discover_known_tickers

        rename_map = build_rename_map(ar, known_tickers=discover_known_tickers(ar))
        ws_report = migrate_workspace(ws, tgt, rename_map=rename_map)
        click.echo(ws_report.render())

    # Write combined report
    report_path = tgt / "_migration_report.md"
    combined = vault_report.render()
    if copilot_workspace:
        combined += "\n\n" + ws_report.render()  # type: ignore[name-defined]
    atomic_write(report_path, combined)
    click.echo(f"Final report at {report_path}")


@cli.command()
@click.option("--target", required=True, type=click.Path())
def validate(target: str) -> None:
    """Post-run validation: count files, scan for broken wikilinks, produce summary."""
    tgt = Path(target).expanduser()
    if not tgt.exists():
        raise click.ClickException(f"target {tgt} does not exist")

    total_files = 0
    total_links = 0
    broken_links: list[tuple[str, str]] = []

    # Build a set of all existing stems in target
    stems_by_relpath: set[str] = set()
    stems_by_slug: dict[str, str] = {}
    for p in tgt.rglob("*.md"):
        if any(part in {".cache", ".obsidian"} for part in p.parts):
            continue
        rel = p.relative_to(tgt).as_posix()[:-3]  # strip .md
        stems_by_relpath.add(rel)
        stems_by_slug.setdefault(Path(rel).name, rel)
        total_files += 1

    wikilink_re = re.compile(r"\[\[([^\[\]]+)\]\]")
    for p in tgt.rglob("*.md"):
        if any(part in {".cache", ".obsidian"} for part in p.parts):
            continue
        if "_raw" in p.parts or "_analyzed" in p.parts:
            continue
        # Skip the migration report itself — it contains example unresolved links as text
        if p.name == "_migration_report.md":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.relative_to(tgt).as_posix()
        for m in wikilink_re.finditer(text):
            raw = m.group(1).split("|", 1)[0].split("#", 1)[0].strip()
            if raw.endswith(".md"):
                raw = raw[:-3]
            total_links += 1
            if raw in stems_by_relpath:
                continue
            if Path(raw).name in stems_by_slug:
                continue
            broken_links.append((rel, raw))

    click.echo(f"Files scanned: {total_files}")
    click.echo(f"Wikilinks scanned: {total_links}")
    click.echo(f"Broken wikilinks: {len(broken_links)}")
    if broken_links:
        click.echo("\nFirst 20 broken:")
        for src, tgt_link in broken_links[:20]:
            click.echo(f"  {src} → [[{tgt_link}]]")


@cli.command()
@click.option("--staging", required=True, type=click.Path())
@click.option("--production", required=True, type=click.Path())
@click.option("--force/--no-force", default=False, help="Allow non-empty production dir")
@click.option(
    "--merge/--replace",
    default=True,
    help="Merge staging into production (rsync; preserves live data) vs replace",
)
def cutover(staging: str, production: str, force: bool, merge: bool) -> None:
    """D57 cutover: staging vault → production vault.

    Default: merge mode (rsync --ignore-existing) preserves anything
    already in production (critical if the live system is running).
    Use --replace for a clean swap (destructive to production).
    """
    import shutil
    import subprocess

    stg = Path(staging).expanduser()
    prod = Path(production).expanduser()

    if not stg.is_dir():
        raise click.ClickException(f"staging {stg} does not exist")

    if prod.exists() and any(prod.iterdir()) and not force and not merge:
        raise click.ClickException(
            f"production {prod} is non-empty. Use --merge to preserve "
            "existing content, or --force --replace to clobber."
        )

    prod.mkdir(parents=True, exist_ok=True)

    if merge:
        # rsync --ignore-existing: only write files not already present in prod
        # (live system wins for any conflicting path)
        result = subprocess.run(
            [
                "rsync",
                "-a",
                "--ignore-existing",
                f"{stg}/",
                f"{prod}/",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise click.ClickException(f"rsync failed: {result.stderr}")
        click.echo(f"Merged {stg} → {prod} (live data preserved)")
    else:
        if prod.exists():
            shutil.rmtree(prod)
        shutil.copytree(stg, prod)
        click.echo(f"Replaced {prod} with {stg} contents")

    log_path = prod / "_cutover.log"
    from praxis_core.time_et import et_iso

    with open(log_path, "a") as f:
        f.write(f"{et_iso()}\tsource={stg}\tmode={'merge' if merge else 'replace'}\n")
    click.echo(f"Cutover logged: {log_path}")
    click.echo("")
    click.echo("Next steps:")
    click.echo("  1. scripts/smoke.sh")
    click.echo("  2. Restart pollers if they were stopped")


@cli.command("import-copilot-state")
@click.option("--copilot-data", required=True, type=click.Path())
@click.option("--apply/--dry-run", default=False, help="Actually write to Postgres")
def import_state_cmd(copilot_data: str, apply: bool) -> None:
    """Import praxis-copilot local state YAML into Postgres signals_fired.

    Requires DATABASE_URL pointing at the target DB (usually praxis or a test DB).
    """
    data_dir = Path(copilot_data).expanduser()
    if not data_dir.is_dir():
        raise click.ClickException(f"copilot data dir not found: {data_dir}")

    async def _run() -> None:
        async with session_scope() as session:
            report = await import_copilot_state(session, data_dir, dry_run=not apply)
            click.echo(report.render())
            if not apply:
                click.echo("\n(dry-run — no writes. Re-run with --apply to commit.)")

    asyncio.run(_run())


@cli.command("import-copilot-filings")
@click.option("--vault", required=True, type=click.Path(), help="Target vault root")
@click.option("--concurrency", type=int, default=16)
@click.option("--limit-filings", type=int, default=None, help="Cap number of filings (test runs)")
@click.option("--limit-press", type=int, default=None, help="Cap number of press releases")
@click.option("--skip-filings/--no-skip-filings", default=False)
@click.option("--skip-press/--no-skip-press", default=False)
def import_copilot_filings_cmd(
    vault: str,
    concurrency: int,
    limit_filings: int | None,
    limit_press: int | None,
    skip_filings: bool,
    skip_press: bool,
) -> None:
    """D58 — backfill praxis-copilot S3 analyses into the vault.

    Pulls analysis.json + index.json for every filing + press-release ever
    analyzed by copilot, translates into our AnalysisResult schema, writes
    _analyzed/ + _raw/ artifacts, and populates sources rows so the live
    pollers won't re-ingest.
    """
    from services.migrate.copilot_filings import run_backfill

    vault_root = Path(vault).expanduser()
    if not vault_root.exists():
        raise click.ClickException(f"vault root does not exist: {vault_root}")

    async def _run() -> None:
        report = await run_backfill(
            vault_root,
            concurrency=concurrency,
            limit_filings=limit_filings,
            limit_press=limit_press,
            skip_filings=skip_filings,
            skip_press=skip_press,
        )
        click.echo(report.render())

    asyncio.run(_run())


@cli.command("import-copilot-events")
@click.option("--concurrency", type=int, default=32)
@click.option("--limit", type=int, default=None, help="Cap number of events (test runs)")
def import_copilot_events_cmd(concurrency: int, limit: int | None) -> None:
    """D59 — backfill praxis-copilot S3 daily events into the events table
    with event_type='filing_ingested_historical' / 'release_ingested_historical'
    and payload.event_id for dedup.
    """
    from services.migrate.copilot_events import run_events_backfill

    async def _run() -> None:
        report = await run_events_backfill(concurrency=concurrency, limit=limit)
        click.echo(report.render())

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
