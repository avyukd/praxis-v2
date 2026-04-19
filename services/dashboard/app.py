from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import desc, func, select

from praxis_core.config import get_settings
from praxis_core.db.models import (
    DeadLetterTask,
    Event,
    Heartbeat,
    Investigation,
    SignalFired,
    Task,
)
from praxis_core.db.session import session_scope
from praxis_core.llm.rate_limit import RateLimitManager
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.cost import today_cost_rollup
from praxis_core.observability.heartbeat import beat
from praxis_core.time_et import et_iso, now_utc

log = get_logger("dashboard.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Start a heartbeat loop for ourselves
    stop = asyncio.Event()

    async def _hb():
        while not stop.is_set():
            try:
                await beat("dashboard.app", status={"alive_at": et_iso()})
            except Exception as e:
                log.warning("dashboard.heartbeat_fail", error=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except TimeoutError:
                pass

    task = asyncio.create_task(_hb())
    yield
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=5)
    except TimeoutError:
        task.cancel()


app = FastAPI(title="praxis-v2 dashboard", lifespan=lifespan)


@app.get("/api/health", response_class=JSONResponse)
async def health() -> dict[str, Any]:
    now = now_utc()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Heartbeat.component, Heartbeat.last_heartbeat, Heartbeat.status).order_by(
                    Heartbeat.component
                )
            )
        ).all()
    out = []
    for r in rows:
        age = int((now - r.last_heartbeat).total_seconds())
        out.append(
            {
                "component": r.component,
                "last_heartbeat": et_iso(r.last_heartbeat),
                "age_s": age,
                "stale": age > 120,
                "status": r.status,
            }
        )
    return {"heartbeats": out}


@app.get("/api/tasks", response_class=JSONResponse)
async def tasks_summary() -> dict[str, Any]:
    async with session_scope() as session:
        by_status_type = (
            await session.execute(
                select(Task.status, Task.type, func.count(Task.id)).group_by(Task.status, Task.type)
            )
        ).all()

        oldest_queued = (
            await session.execute(
                select(Task.type, func.min(Task.created_at))
                .where(Task.status.in_(["queued", "partial"]))
                .group_by(Task.type)
            )
        ).all()

        recent_failed = (
            await session.execute(
                select(Task.id, Task.type, Task.last_error, Task.finished_at)
                .where(Task.status.in_(["failed", "dead_letter"]))
                .order_by(desc(Task.finished_at))
                .limit(10)
            )
        ).all()

    summary: dict[str, dict[str, int]] = {}
    for status, task_type, count in by_status_type:
        summary.setdefault(status, {})[task_type] = int(count)

    now = now_utc()
    oldest = [
        {
            "type": t,
            "oldest_age_s": int((now - created).total_seconds()),
        }
        for t, created in oldest_queued
    ]
    failed = [
        {
            "id": str(r.id),
            "type": r.type,
            "last_error": r.last_error[:300] if r.last_error else None,
            "finished_at": et_iso(r.finished_at) if r.finished_at else None,
        }
        for r in recent_failed
    ]
    return {"by_status_type": summary, "oldest_queued_by_type": oldest, "recent_failed": failed}


@app.get("/api/rate_limit", response_class=JSONResponse)
async def rate_limit() -> dict[str, Any]:
    async with session_scope() as session:
        snap = await RateLimitManager().snapshot(session)
    return {
        "status": snap.status,
        "limited_until_ts": et_iso(snap.limited_until_ts) if snap.limited_until_ts else None,
        "consecutive_hits": snap.consecutive_hits,
        "last_hit_ts": et_iso(snap.last_hit_ts) if snap.last_hit_ts else None,
    }


@app.get("/api/cost", response_class=JSONResponse)
async def cost() -> dict[str, Any]:
    async with session_scope() as session:
        return await today_cost_rollup(session)


@app.get("/api/events", response_class=JSONResponse)
async def events(limit: int = 50) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (
            (await session.execute(select(Event).order_by(desc(Event.ts)).limit(limit)))
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "ts": et_iso(r.ts),
            "component": r.component,
            "event_type": r.event_type,
            "payload": r.payload,
        }
        for r in rows
    ]


@app.get("/api/signals", response_class=JSONResponse)
async def signals(hours: int = 24, limit: int = 50) -> list[dict[str, Any]]:
    since = now_utc() - timedelta(hours=hours)
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(SignalFired)
                    .where(SignalFired.fired_at >= since)
                    .order_by(desc(SignalFired.fired_at))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": str(r.id),
            "ticker": r.ticker,
            "signal_type": r.signal_type,
            "urgency": r.urgency,
            "fired_at": et_iso(r.fired_at),
            "payload": r.payload,
        }
        for r in rows
    ]


@app.get("/api/investigations", response_class=JSONResponse)
async def investigations(limit: int = 50) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Investigation)
                    .order_by(desc(Investigation.last_progress_at))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": str(r.id),
            "handle": r.handle,
            "status": r.status,
            "scope": r.scope,
            "hypothesis": r.hypothesis,
            "entry_nodes": r.entry_nodes,
            "last_progress_at": et_iso(r.last_progress_at) if r.last_progress_at else None,
        }
        for r in rows
    ]


@app.get("/api/dead_letters", response_class=JSONResponse)
async def dead_letters(limit: int = 50) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(DeadLetterTask).order_by(desc(DeadLetterTask.failed_at)).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": str(r.id),
            "failed_at": et_iso(r.failed_at),
            "final_error": r.final_error[:500] if r.final_error else None,
            "task_type": (r.original_task or {}).get("type"),
            "attempts": (r.original_task or {}).get("attempts"),
            "payload": (r.original_task or {}).get("payload"),
        }
        for r in rows
    ]


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return _default_html()


def _default_html() -> str:
    return """<!doctype html>
<html><head><meta charset='utf-8'><title>praxis-v2</title>
<style>
body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:1100px;margin:2em auto;padding:0 1em;color:#eee;background:#111}
h1{font-size:1.5em;color:#8f8}
h2{font-size:1.1em;color:#aaf;margin-top:1.5em;border-bottom:1px solid #333}
.red{color:#f66}.yellow{color:#fc6}.green{color:#6f6}.grey{color:#888}
table{width:100%;border-collapse:collapse;margin-top:.5em}
td,th{padding:.3em .5em;border-bottom:1px solid #222;text-align:left;font-size:.9em}
pre{background:#000;padding:.5em;overflow:auto;font-size:.8em}
.tile{display:inline-block;padding:.4em .8em;margin-right:.5em;background:#222;border-radius:3px}
.refresh{float:right;color:#888;font-size:.8em}
.spinner{display:inline-block;width:.7em;height:.7em;border:2px solid #444;border-top-color:#6f6;border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle;margin-right:.3em}
@keyframes spin{to{transform:rotate(360deg)}}
.stale{color:#f66}
button.refresh-btn{background:#333;color:#eee;border:1px solid #555;padding:.3em .6em;border-radius:3px;cursor:pointer;margin-left:.5em}
</style></head><body>
<h1>praxis-v2 <span class='refresh' id='refreshed'></span>
<button class='refresh-btn' onclick='refresh()'>refresh</button></h1>

<h2>Rate limit</h2><div id='rl'>loading...</div>
<h2>Health</h2><div id='health'>loading...</div>
<h2>Task queue</h2><div id='tasks'>loading...</div>
<h2>Cost (today)</h2><div id='cost'>loading...</div>
<h2>Dead letter</h2><div id='dl'>loading...</div>
<h2>Investigations</h2><div id='inv'>loading...</div>
<h2>Signals (24h)</h2><div id='signals'>loading...</div>
<h2>Events (last 50)</h2><div id='events'>loading...</div>

<script>
async function j(u){const r=await fetch(u);return r.json();}
function el(id){return document.getElementById(id);}

function renderHealth(hb){
  const rows=hb.heartbeats.map(h=>{
    const c=h.stale?'red':(h.age_s>60?'yellow':'green');
    return `<tr><td>${h.component}</td><td class='${c}'>${h.age_s}s</td><td>${JSON.stringify(h.status||{})}</td></tr>`;
  }).join('');
  return `<table><tr><th>Component</th><th>Age</th><th>Status</th></tr>${rows}</table>`;
}

function renderTasks(t){
  const statuses=Object.keys(t.by_status_type||{}).sort();
  const rows=statuses.map(s=>{
    const cells=Object.entries(t.by_status_type[s]).map(([k,v])=>`${k}:${v}`).join(' ');
    return `<tr><td>${s}</td><td>${cells}</td></tr>`;
  }).join('');
  const oldest=(t.oldest_queued_by_type||[]).map(o=>`${o.type}:${Math.round(o.oldest_age_s/60)}m`).join(' ') || '-';
  let failed='';
  if((t.recent_failed||[]).length){
    failed='<h3 style="color:#f66">Recent failures</h3><table><tr><th>type</th><th>error</th></tr>'+
      t.recent_failed.map(f=>`<tr><td>${f.type}</td><td class='red'>${f.last_error||''}</td></tr>`).join('')+'</table>';
  }
  return `<table><tr><th>Status</th><th>Types</th></tr>${rows}</table>
  <p>Oldest queued: ${oldest}</p>${failed}`;
}

function renderRL(r){
  const c=r.status==='clear'?'green':(r.status==='probing'?'yellow':'red');
  return `<div class='tile ${c}'>${r.status}</div> hits: ${r.consecutive_hits}${r.limited_until_ts?' until '+r.limited_until_ts:''}`;
}

function renderCost(c){
  return `<p>Total today: $${(c.total_cost_usd||0).toFixed(4)} · tokens in: ${c.total_tokens_in} · tokens out: ${c.total_tokens_out}</p>`+
    '<table><tr><th>type</th><th>count</th><th>cost</th><th>in</th><th>out</th></tr>'+
    Object.entries(c.by_type||{}).map(([k,v])=>`<tr><td>${k}</td><td>${v.count}</td><td>$${v.cost_usd.toFixed(4)}</td><td>${v.tokens_in}</td><td>${v.tokens_out}</td></tr>`).join('')+'</table>';
}

function renderInv(inv){
  if(!inv.length) return '<p class="grey">No investigations yet.</p>';
  return '<table><tr><th>handle</th><th>status</th><th>scope</th><th>hypothesis</th><th>last progress</th></tr>'+
    inv.map(i=>`<tr><td>${i.handle}</td><td>${i.status}</td><td>${i.scope}</td><td>${i.hypothesis||''}</td><td>${i.last_progress_at||''}</td></tr>`).join('')+'</table>';
}

function renderSignals(sg){
  if(!sg.length) return '<p class="grey">No signals yet.</p>';
  return '<table><tr><th>fired</th><th>ticker</th><th>urgency</th><th>type</th><th>title</th></tr>'+
    sg.map(s=>`<tr><td>${s.fired_at}</td><td>${s.ticker||'-'}</td><td>${s.urgency}</td><td>${s.signal_type}</td><td>${(s.payload||{}).title||''}</td></tr>`).join('')+'</table>';
}

function renderEvents(ev){
  return '<table><tr><th>ts</th><th>component</th><th>type</th><th>payload</th></tr>'+
    ev.map(e=>`<tr><td>${e.ts}</td><td>${e.component}</td><td>${e.event_type}</td><td><pre>${JSON.stringify(e.payload||{})}</pre></td></tr>`).join('')+'</table>';
}

function renderDL(dl){
  if(!dl.length) return '<p class="grey">No dead letters.</p>';
  return `<p class='red'>${dl.length} dead-lettered task(s) — use MCP requeue_dead_letter(id) after fixing root cause.</p>`+
    '<table><tr><th>id</th><th>type</th><th>failed</th><th>attempts</th><th>error</th></tr>'+
    dl.map(d=>`<tr><td>${d.id.slice(0,8)}</td><td>${d.task_type||'?'}</td><td>${d.failed_at}</td><td>${d.attempts||'?'}</td><td class='red'>${(d.final_error||'').slice(0,200)}</td></tr>`).join('')+'</table>';
}

let inflight=0;
async function refresh(){
  inflight++;
  el('refreshed').innerHTML="<span class='spinner'></span>refreshing...";
  const started=Date.now();
  try{
    const [h,t,rl,c,dl,inv,sg,ev]=await Promise.all([
      j('/api/health'),j('/api/tasks'),j('/api/rate_limit'),j('/api/cost'),
      j('/api/dead_letters'),j('/api/investigations'),j('/api/signals'),j('/api/events')
    ]);
    el('health').innerHTML=renderHealth(h);
    el('tasks').innerHTML=renderTasks(t);
    el('rl').innerHTML=renderRL(rl);
    el('cost').innerHTML=renderCost(c);
    el('dl').innerHTML=renderDL(dl);
    el('inv').innerHTML=renderInv(inv);
    el('signals').innerHTML=renderSignals(sg);
    el('events').innerHTML=renderEvents(ev);
    el('refreshed').innerText='refreshed '+new Date().toLocaleTimeString()+` (${Date.now()-started}ms)`;
  }catch(e){
    el('refreshed').innerHTML='<span class="stale">ERROR: '+e+' — showing stale data</span>';
  }finally{
    inflight--;
  }
}
refresh();
setInterval(()=>{if(inflight===0)refresh();},10000);
</script>
</body></html>"""


def main() -> None:
    import uvicorn

    configure_logging()
    settings = get_settings()
    uvicorn.run(
        "services.dashboard.app:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
