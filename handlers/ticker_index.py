"""ticker_index — create a thin companies/<T>/ directory for every ticker
that has _analyzed/ data but no existing company node.

This is the "orphan resolver". The live vault has ~1600 tickers whose
press releases / filings have been analyzed but have no `companies/<T>/`
dir, so dives / surface_ideas / observer can't link to them as graph
nodes. This handler creates a minimal `companies/<T>/index.md` for each
orphan — just a frontmatter shell + a grouped list of wikilinks to
every `_analyzed/` artifact we have.

No LLM calls. No notes.md synthesis (that stays organic via
compile_to_wiki as the ticker accumulates events). Just opens up the
graph so everything can be cross-referenced properly.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from handlers import HandlerContext, HandlerResult
from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso
from praxis_core.vault.writer import atomic_write

log = get_logger("handlers.ticker_index")


def _ticker_from_pr_path(p: Path) -> str | None:
    """_analyzed/press_releases/<source>/<ticker>/<release_id>/analysis.json"""
    parts = p.parts
    if "press_releases" in parts:
        i = parts.index("press_releases")
        if i + 2 < len(parts):
            return parts[i + 2]
    return None


def _ticker_from_filing_meta(vault: Path, accession_dir: Path) -> str | None:
    """Try analysis.md frontmatter first, then signals.json, for a ticker."""
    for fn in ("analysis.md", "analysis.json", "signals.json"):
        f = accession_dir / fn
        if not f.exists():
            continue
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if fn == "analysis.md":
            for line in t.splitlines()[:20]:
                if line.startswith("ticker:"):
                    v = line.split(":", 1)[1].strip()
                    if v and v != "UNKNOWN":
                        return v
        else:
            import json
            try:
                d = json.loads(t)
                tk = d.get("ticker")
                if tk and tk != "UNKNOWN":
                    return str(tk)
            except Exception:
                continue
    return None


def _collect_ticker_artifacts(vault: Path) -> dict[str, dict[str, list[str]]]:
    """Returns {ticker: {"filings": [relpath], "press": [relpath]}}"""
    out: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"filings": [], "press": []}
    )

    # Press releases
    pr_base = vault / "_analyzed" / "press_releases"
    if pr_base.exists():
        for p in pr_base.rglob("analysis.json"):
            ticker = _ticker_from_pr_path(p)
            if not ticker:
                continue
            parent = p.parent  # release_id dir
            rel = parent.relative_to(vault).as_posix()
            out[ticker.upper()]["press"].append(rel)

    # Filings
    filings_base = vault / "_analyzed" / "filings"
    if filings_base.exists():
        for acc_dir in filings_base.rglob("*"):
            if not acc_dir.is_dir():
                continue
            if not (acc_dir / "analysis.json").exists():
                continue
            ticker = _ticker_from_filing_meta(vault, acc_dir)
            if not ticker or ticker == "UNKNOWN":
                continue
            rel = acc_dir.relative_to(vault).as_posix()
            out[ticker.upper()]["filings"].append(rel)

    return out


def render_ticker_index(ticker: str, arts: dict[str, list[str]], ran_at: str) -> str:
    n_fil = len(arts["filings"])
    n_pr = len(arts["press"])
    header = f"""---
type: company_index
ticker: {ticker}
status: auto
data_vintage: {ran_at[:10]}
last_refresh: {ran_at}
source: ticker_index handler (auto-generated)
tags: [company, index, auto]
---

# {ticker} — Event Index

_Auto-generated list of every `_analyzed/` artifact keyed to this ticker.
This is a graph stub, not a research document. For synthesis see
`notes.md` (if present). For deep research see the `dives/` folder
(if present)._

**Counts:** {n_fil} filings / {n_pr} press releases.

"""
    parts = [header]

    if arts["filings"]:
        parts.append(f"## Filings ({n_fil})")
        parts.append("")
        for rel in sorted(arts["filings"]):
            acc = Path(rel).name
            parts.append(f"- [[{rel}/analysis.json|{acc}]]")
        parts.append("")

    if arts["press"]:
        parts.append(f"## Press releases ({n_pr})")
        parts.append("")
        for rel in sorted(arts["press"]):
            rid = Path(rel).name
            parts.append(f"- [[{rel}/analysis.json|{rid}]]")
        parts.append("")

    return "\n".join(parts) + "\n"


async def handle(ctx: HandlerContext) -> HandlerResult:
    vault = ctx.vault_root
    ran_at = et_iso()

    artifacts = _collect_ticker_artifacts(vault)

    companies_dir = vault / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)

    created_dirs = 0
    updated_indexes = 0
    for ticker, arts in artifacts.items():
        if not ticker or "/" in ticker or ticker in ("UNKNOWN", ""):
            continue
        t_dir = companies_dir / ticker
        if not t_dir.exists():
            t_dir.mkdir(parents=True, exist_ok=True)
            created_dirs += 1
        idx_path = t_dir / "index.md"
        new_content = render_ticker_index(ticker, arts, ran_at)
        try:
            existing = idx_path.read_text(encoding="utf-8") if idx_path.exists() else ""
        except OSError:
            existing = ""
        # Only write if content differs (idempotent — new artifacts trigger rewrite)
        if new_content != existing:
            atomic_write(idx_path, new_content)
            updated_indexes += 1

    log.info(
        "ticker_index.done",
        tickers_seen=len(artifacts),
        dirs_created=created_dirs,
        indexes_updated=updated_indexes,
    )
    return HandlerResult(
        ok=True,
        message=(
            f"{len(artifacts)} tickers; "
            f"{created_dirs} new dirs; "
            f"{updated_indexes} indexes written"
        ),
    )
