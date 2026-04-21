"""Vault memory search — the first-class retrieval layer every research
handler calls before doing anything expensive.

Two-stage design:

1. **Keyword filter (stage 1)**: walks the vault's indexable areas
   (themes/, questions/, concepts/, memos/, companies/<T>/notes.md,
   _raw/manual/), tokenizes each doc's frontmatter title + tags +
   first 2000 chars of body, scores against the query by normalized
   term overlap. Fast, pure-python, no deps. Returns top 40.

2. **Haiku rerank (stage 2)**: sends those 40 candidates + the query
   to Haiku with prompt "pick top N most relevant, emit {path,
   score 0-1, rationale}". Cheap (~$0.03/call) and understands
   semantic relevance better than keyword overlap alone.

Failure mode: if the Haiku rerank rate-limits or fails, stage 1
results are returned unchanged — still useful, just less smart.

Results are cached for 10 minutes on (query, scope, limit) so
repeated calls from the same planner don't re-rerank.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import frontmatter

from praxis_core.logging import get_logger
from praxis_core.schemas.task_types import TaskModel

log = get_logger("vault.memory")

Scope = Literal[
    "themes", "questions", "concepts", "memos", "sources", "companies"
]

_ALL_SCOPES: tuple[Scope, ...] = (
    "themes",
    "questions",
    "concepts",
    "memos",
    "sources",
    "companies",
)

_STAGE1_LIMIT = 40
_BODY_PREFIX_CHARS = 2000
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "it", "its", "this", "that", "as", "but", "if", "not", "no", "yes",
    "we", "they", "i", "you", "he", "she", "our", "their", "what",
    "when", "how", "why", "which", "who", "whom", "vs",
})

_CACHE_TTL_S = 600.0
_cache: dict[tuple[str, tuple[Scope, ...], int], tuple[float, list["VaultHit"]]] = {}


@dataclass
class VaultHit:
    path: str  # relative to vault_root
    node_type: str  # theme | question | concept | memo | source | company
    title: str
    snippet: str
    relevance_score: float  # 0-1
    why_relevant: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "node_type": self.node_type,
            "title": self.title,
            "snippet": self.snippet,
            "relevance_score": round(self.relevance_score, 3),
            "why_relevant": self.why_relevant,
            "tags": list(self.tags),
        }


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


def _score_overlap(query_tokens: set[str], doc_tokens: set[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens)
    return overlap / max(1, len(query_tokens))


def _scope_globs(vault_root: Path, scope: Scope) -> list[Path]:
    if scope == "themes":
        d = vault_root / "themes"
        return sorted(d.glob("*.md")) if d.exists() else []
    if scope == "questions":
        d = vault_root / "questions"
        return sorted(d.glob("*.md")) if d.exists() else []
    if scope == "concepts":
        d = vault_root / "concepts"
        return sorted(d.glob("*.md")) if d.exists() else []
    if scope == "memos":
        d = vault_root / "memos"
        return sorted(d.glob("*.md")) if d.exists() else []
    if scope == "sources":
        d = vault_root / "_raw" / "manual"
        return sorted(d.rglob("*.md")) if d.exists() else []
    if scope == "companies":
        d = vault_root / "companies"
        if not d.exists():
            return []
        out: list[Path] = []
        for company_dir in d.iterdir():
            if not company_dir.is_dir():
                continue
            notes = company_dir / "notes.md"
            if notes.exists():
                out.append(notes)
        return sorted(out)
    return []


def _node_type_for_scope(scope: Scope) -> str:
    return {
        "themes": "theme",
        "questions": "question",
        "concepts": "concept",
        "memos": "memo",
        "sources": "source",
        "companies": "company",
    }[scope]


def _load_doc(p: Path) -> tuple[str, dict, str]:
    """Return (title, frontmatter_dict, body_prefix)."""
    try:
        post = frontmatter.load(str(p))
    except Exception:
        try:
            return (p.stem, {}, p.read_text(encoding="utf-8", errors="replace")[:_BODY_PREFIX_CHARS])
        except OSError:
            return (p.stem, {}, "")
    meta = dict(post.metadata or {})
    body = (post.content or "")[:_BODY_PREFIX_CHARS]
    title = str(meta.get("title") or "").strip()
    if not title:
        first_heading = next((ln for ln in body.splitlines() if ln.startswith("# ")), "")
        title = first_heading.lstrip("# ").strip() or p.stem
    return (title, meta, body)


def _stage1_candidates(
    vault_root: Path, query: str, scopes: tuple[Scope, ...]
) -> list[VaultHit]:
    """Keyword-overlap filter. Returns up to _STAGE1_LIMIT candidates."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    scored: list[tuple[float, VaultHit]] = []

    for scope in scopes:
        node_type = _node_type_for_scope(scope)
        for p in _scope_globs(vault_root, scope):
            try:
                title, meta, body = _load_doc(p)
            except Exception as e:
                log.warning("memory.load_fail", path=str(p), error=str(e))
                continue
            tags = [str(t) for t in (meta.get("tags") or [])]
            doc_text = " ".join([title, " ".join(tags), body])
            doc_tokens = _tokenize(doc_text)
            score = _score_overlap(query_tokens, doc_tokens)
            if score <= 0:
                continue
            rel = str(p.relative_to(vault_root))
            snippet = _snippet(body, query_tokens)
            scored.append(
                (
                    score,
                    VaultHit(
                        path=rel,
                        node_type=node_type,
                        title=title,
                        snippet=snippet,
                        relevance_score=score,
                        tags=tags,
                    ),
                )
            )

    scored.sort(key=lambda t: t[0], reverse=True)
    return [hit for _, hit in scored[:_STAGE1_LIMIT]]


def _snippet(body: str, query_tokens: set[str], width: int = 200) -> str:
    """Extract a body window around the densest query-token occurrence."""
    if not body:
        return ""
    body_norm = body.lower()
    best_pos = 0
    best_density = 0
    step = max(width // 4, 1)
    for i in range(0, max(len(body_norm) - width, 1), step):
        window = body_norm[i:i + width]
        density = sum(1 for t in query_tokens if t in window)
        if density > best_density:
            best_density = density
            best_pos = i
    raw = body[best_pos:best_pos + width].replace("\n", " ").strip()
    return raw


_RERANK_SYSTEM = """You are the relevance judge for a research memory
search. You will be given a user query and a list of candidate
documents (with title + short snippet). Rank them by how directly
each answers or informs the query.

Return JSON only — no prose, no code fences:

{
  "ranked": [
    {"path": "<same path string>", "score": 0.0-1.0, "why": "<one short sentence>"},
    ...
  ]
}

Rules:
- Include only candidates with score > 0.15. Drop irrelevant ones.
- Prefer primary artifacts (themes, memos, company notes) over raw
  sources when they cover the topic with analysis.
- "why" must cite a concrete phrase or concept from the snippet.
- Emit at most the requested limit.
"""


async def _stage2_rerank(
    candidates: list[VaultHit], query: str, limit: int, vault_root: Path
) -> list[VaultHit] | None:
    """Returns reranked hits, or None if rerank failed/rate-limited."""
    if not candidates:
        return []
    from handlers._common import run_llm

    cand_lines = []
    for i, c in enumerate(candidates):
        cand_lines.append(
            f"[{i}] path={c.path} type={c.node_type} title={c.title!r}\n    snippet: {c.snippet[:200]}"
        )
    user_prompt = (
        f"Query: {query}\n\n"
        f"Return the top {limit} most relevant candidates.\n\n"
        "Candidates:\n" + "\n".join(cand_lines)
    )
    try:
        result = await run_llm(
            system_prompt=_RERANK_SYSTEM,
            user_prompt=user_prompt,
            model=TaskModel.HAIKU,
            max_budget_usd=0.10,
            vault_root=vault_root,
            allowed_tools=[],
        )
    except Exception as e:
        log.warning("memory.rerank_call_fail", error=str(e)[:200])
        return None
    if result.finish_reason == "rate_limit":
        log.info("memory.rerank_rate_limited")
        return None

    text = (result.text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        log.info("memory.rerank_no_json")
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        log.info("memory.rerank_bad_json")
        return None
    ranked = data.get("ranked") or []
    if not isinstance(ranked, list):
        return None

    by_path = {c.path: c for c in candidates}
    out: list[VaultHit] = []
    for item in ranked[:limit]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        score = float(item.get("score") or 0.0)
        why = str(item.get("why") or "").strip()
        hit = by_path.get(path)
        if hit is None:
            continue
        hit = VaultHit(
            path=hit.path,
            node_type=hit.node_type,
            title=hit.title,
            snippet=hit.snippet,
            relevance_score=max(0.0, min(1.0, score)),
            why_relevant=why[:240],
            tags=hit.tags,
        )
        out.append(hit)
    return out


async def search_vault_memory(
    vault_root: Path,
    query: str,
    *,
    limit: int = 10,
    scope: list[Scope] | None = None,
    skip_rerank: bool = False,
) -> list[VaultHit]:
    """Search the vault's indexable areas for documents relevant to `query`.

    Two stages:
      1. Keyword-overlap filter — fast, returns top 40.
      2. Haiku rerank — cheap, semantic. Optional (skipped if
         skip_rerank=True or if Haiku is rate-limited).

    Returns up to `limit` hits ranked by relevance.
    """
    scopes: tuple[Scope, ...] = tuple(scope) if scope else _ALL_SCOPES

    cache_key = (query.strip().lower(), scopes, limit)
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]

    candidates = _stage1_candidates(vault_root, query, scopes)
    if not candidates or skip_rerank:
        result = candidates[:limit]
        _cache[cache_key] = (now, result)
        return result

    reranked = await _stage2_rerank(candidates, query, limit, vault_root)
    if reranked is None:
        result = candidates[:limit]
    else:
        result = reranked
    _cache[cache_key] = (now, result)
    log.info(
        "memory.search",
        query=query[:80],
        stage1_count=len(candidates),
        final_count=len(result),
        reranked=reranked is not None,
    )
    return result


def clear_cache() -> None:
    """Manual cache reset — for tests."""
    _cache.clear()
