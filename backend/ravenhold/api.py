"""HTTP API. Run: uv run uvicorn ravenhold.api:app --reload --port 8000"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import mcp_bridge, scenarios, seed, tools, usage
from .agents import handle_message
from .db import client, copy_graph, graph, rows

TENANTS = ["ravenhold", "ravenhold-labs"]
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")  # set on the public deployment; unset locally


def seed_tenant(tenant: str, keep_memory: bool = False) -> None:
    g = seed.reset(tenant, keep_memory)
    if tenant == "ravenhold":
        seed.load_company(g)
    else:
        G = seed.D.LABS
        seed.load_core(g, G["teams"], G["people"], G["on_call"], G["services"], G["depends_on"])
    seed.load_agents(g)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # A fresh FalkorDB (e.g. a new cloud instance) gets the demo company on first boot.
    existing = client().list_graphs()
    for t in TENANTS:
        if t not in existing:
            seed_tenant(t)
    # Start the agents' MCP server now, so the first question doesn't wait for it.
    threading.Thread(target=mcp_bridge.toolbox, name="mcp-start", daemon=True).start()
    yield


app = FastAPI(title="Ravenhold", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
# Compress the UI bundle and JSON. Starlette skips text/event-stream, so the agent stream is never buffered.
app.add_middleware(GZipMiddleware, minimum_size=1024)


# ---------------------------------------------------------------------------
# Private sandbox per visitor: each browser gets its own copy of the company graph (FalkorDB GRAPH.COPY), so
# visitors can't see or poison each other's incidents, lessons and history. The template graph stays pristine.
SANDBOXES = os.getenv("SANDBOXES", "on") != "off"
MAX_SANDBOXES = int(os.getenv("MAX_SANDBOXES", "300"))
SANDBOX_TTL_S = 24 * 3600
_VISITOR = re.compile(r"^[a-z0-9]{8,32}$")
_sandbox_lock = threading.Lock()


def _prune_sandboxes() -> None:
    """Drop sandboxes idle for a day, and the oldest ones if there are too many."""
    ops = graph(usage.OPS_GRAPH)
    boxes = rows(ops.query("MATCH (b:Sandbox) RETURN b.id AS id, b.last_seen AS seen ORDER BY seen"))
    cutoff = time.time() - SANDBOX_TTL_S
    doomed = [b["id"] for b in boxes if (b["seen"] or 0) < cutoff]
    keep = [b for b in boxes if b["id"] not in doomed]
    doomed += [b["id"] for b in keep[: max(0, len(keep) - MAX_SANDBOXES + 1)]]
    existing = set(client().list_graphs())
    for gid in doomed:
        if gid in existing:
            graph(gid).delete()
        ops.query("MATCH (b:Sandbox {id: $id}) DELETE b", {"id": gid})


def _tenant(t: str, visitor: str | None = None) -> str:
    """Resolve a workspace to the graph this visitor should use (their private copy when sandboxes are on)."""
    if t not in TENANTS:
        raise HTTPException(404, f"unknown workspace {t}")
    if not SANDBOXES or not visitor:
        return t
    if not _VISITOR.match(visitor):
        raise HTTPException(400, "invalid visitor id")
    name = f"{t}__{visitor}"
    with _sandbox_lock:
        if name not in client().list_graphs():
            _prune_sandboxes()
            copy_graph(t, name)
    graph(usage.OPS_GRAPH).query("MERGE (b:Sandbox {id: $id}) ON CREATE SET b.created_at = $now SET b.last_seen = $now",
                                 {"id": name, "now": time.time()})
    return name


def _mutating(t: str, visitor: str | None) -> str:
    """Writes must go to a sandbox on the public deployment, never to the shared template."""
    if ADMIN_TOKEN and SANDBOXES and not visitor:
        raise HTTPException(400, "Missing X-Visitor header.")
    return _tenant(t, visitor)


Visitor = Header(default=None, alias="X-Visitor")


class ChatIn(BaseModel):
    tenant: str = "ravenhold"
    session_id: str | None = Field(default=None, max_length=40)
    message: str = Field(min_length=1, max_length=2000)


@app.get("/api/health")
def health():
    return {"ok": True, "graphs": client().list_graphs()}


@app.get("/api/tenants")
def tenants():
    return TENANTS


# What the Guide's "Meet Ravenhold Pay" cards show: counted live from the graph, with the seed file each came from.
DATASETS = [  # key, Cypher label, seed_data.py list, what a real company would use instead
    ("systems", "Service", "SERVICES + DEPENDS_ON", "service catalog (Backstage) and tracing"),
    ("people", "Person", "PEOPLE + TEAMS + ON_CALL", "HR directory and PagerDuty schedules"),
    ("customers", "Customer", "CUSTOMERS + PRODUCTS", "CRM (Salesforce) and billing"),
    ("past problems", "Incident", "INCIDENTS", "incident tracker (PagerDuty, incident.io)"),
    ("documents", "Doc", "DOCS", "Confluence or Google Docs"),
    ("chats", "Message", "MESSAGES", "Slack"),
    ("to-dos", "Ticket", "TICKETS", "Jira or Linear"),
    ("fixes written down", "Runbook", "RUNBOOKS", "runbook wiki"),
    ("projects", "Project", "PROJECTS", "Jira projects, Linear or a roadmap tool"),
]


def _plainval(v):
    return v if isinstance(v, (str, int, float, bool, type(None), list)) else str(v)


@app.get("/api/about")
def about(tenant: str = "ravenhold"):
    """Live facts about the demo data and stack, so nothing in the Guide is hard-coded."""
    if tenant not in TENANTS:
        raise HTTPException(404, f"unknown workspace {tenant}")
    g = graph(tenant)
    data = []
    for key, label, src, real in DATASETS:
        r = g.ro_query(f"MATCH (n:{label}) WITH n ORDER BY n.id RETURN count(n), collect(n)[0]").result_set[0]
        sample = {k: _plainval(v) for k, v in (r[1].properties if r[1] else {}).items() if k not in ("embedding", "text")}
        data.append({"key": key, "label": label, "count": r[0], "source": f"backend/ravenhold/seed_data.py · {src}",
                     "real_world": real, "sample": sample})
    extra = rows(g.ro_query(
        """MATCH (c:Customer) WITH sum(c.arr) AS arr
           OPTIONAL MATCH (t:Team) WITH arr, count(t) AS teams
           OPTIONAL MATCH (:Person)-[m:MEMBER_OF]->() WHERE m.to IS NOT NULL
           WITH arr, teams, count(m) AS moves
           OPTIONAL MATCH (tk:Ticket) WHERE tk.status <> 'done'
           WITH arr, teams, moves, count(tk) AS open_todos
           OPTIONAL MATCH (d:Decision) RETURN arr, teams, moves, open_todos, count(d) AS decisions"""))[0]
    labels = rows(g.ro_query("MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS count ORDER BY count DESC"))
    rels = rows(g.ro_query("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC"))
    try:
        mods = [[x.decode() if isinstance(x, bytes) else x for x in m] for m in client().connection.execute_command("MODULE", "LIST")]
        ver = next(int(m[m.index("ver") + 1]) for m in mods if "graph" in m)
        falkor = f"{ver // 10000}.{ver // 100 % 100}.{ver % 100}"
    except Exception:
        falkor = None
    from .llm import active_models
    return {"datasets": data, "totals": extra, "labels": labels, "relationships": rels, "falkordb_version": falkor,
            "models": active_models(), "tools": mcp_bridge.status(), "nodes": g.ro_query("MATCH (n) RETURN count(n)").result_set[0][0], "scenarios": {k: len(v["alerts"]) for k, v in scenarios.SCENARIOS.items()}}


@app.get("/api/usage")
def get_usage():
    return usage.summary()


@app.get("/api/limits")
def limits():
    """The only limit is the shared credit budget, shown to every visitor; no per-visitor caps."""
    s = usage.summary()
    ok = s["spent_usd"] < s["budget_usd"]
    return {"can_run": ok, "reason": None if ok else "budget", "budget_usd": s["budget_usd"],
            "spent_usd": round(s["spent_usd"], 2), "budget_left_usd": round(s["remaining_usd"], 2)}


@app.get("/api/usage/calls")
def get_calls(limit: int = 100):
    return usage.recent_calls(min(limit, 500))


@app.get("/api/scenarios")
def get_scenarios():
    return [{"id": k, "name": v["name"], "summary": v["summary"], "impact": v["impact"], "alerts": len(v["alerts"])}
            for k, v in scenarios.SCENARIOS.items()]


@app.post("/api/simulate")
def simulate(scenario: str, tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    """Fire an alert storm into the graph so anyone can try the agents in one click (no LLM cost)."""
    if scenario not in scenarios.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {scenario}")
    if tenant != "ravenhold":
        raise HTTPException(400, "Scenarios are written for the Ravenhold Pay workspace.")
    return scenarios.inject(_mutating(tenant, x_visitor), scenario)


@app.get("/api/alerts")
def get_alerts(tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    return scenarios.firing(_tenant(tenant, x_visitor))


@app.post("/api/alerts/clear")
def clear_alerts(tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    scenarios.clear(_mutating(tenant, x_visitor))
    return {"ok": True}


@app.post("/api/chat")
async def chat(body: ChatIn, x_visitor: str | None = Visitor):
    tenant = _mutating(body.tenant, x_visitor)
    try:
        usage.check_budget()
    except usage.BudgetExceeded as e:
        raise HTTPException(503, str(e))
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(ev: dict):
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    def work():
        try:
            handle_message(tenant, body.session_id, body.message, emit)
        except Exception as e:
            emit({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            emit({"type": "done"})

    threading.Thread(target=work, daemon=True).start()

    async def stream():
        while True:
            ev = await queue.get()
            yield {"event": "message", "data": json.dumps(ev, default=str)}
            if ev["type"] == "done":
                break

    return EventSourceResponse(stream())


@app.get("/api/graph")
def full_graph(tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    """The whole company graph (minus conversation turns) for the base visualisation."""
    g = graph(_tenant(tenant, x_visitor))
    nodes = rows(g.ro_query(
        """MATCH (n) WHERE NOT n:Turn AND n.id IS NOT NULL AND NOT (n:Alert AND n.status <> 'firing')
           RETURN n.id AS id, [l IN labels(n) WHERE l <> 'Source'][0] AS label,
                  coalesce(n.name, n.title, n.id) AS name, n.status AS status, n.tier AS tier,
                  coalesce(n.plain, n.role, CASE WHEN n:Message OR n:Lesson THEN left(n.text, 140) ELSE n.kind END) AS about"""
    ))
    edges = rows(g.ro_query(
        """MATCH (a)-[r]->(b) WHERE a.id IS NOT NULL AND b.id IS NOT NULL AND NOT a:Turn AND NOT b:Turn
             AND NOT (a:Alert AND a.status <> 'firing')
           RETURN a.id AS source, type(r) AS type, b.id AS target"""
    ))
    return {"nodes": nodes, "edges": edges}


@app.get("/api/sessions")
def sessions(tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    return rows(graph(_tenant(tenant, x_visitor)).ro_query(
        """MATCH (s:Session)
           OPTIONAL MATCH (s)-[:HAS_TURN]->(t:Turn)
           OPTIONAL MATCH (s)-[:HANDLED]->(i:Incident)
           OPTIONAL MATCH (l:Lesson)-[:LEARNED_IN]->(s)
           RETURN s.id AS id, s.started_at AS started_at, count(DISTINCT t) AS turns,
                  collect(DISTINCT i.id) AS incidents, count(DISTINCT l) AS lessons
           ORDER BY started_at DESC LIMIT 30"""
    ))


@app.post("/api/pages/{page_id}/decision")
def page_decision(page_id: str, approve: bool, tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    """Human in the loop: approve or decline a page the agents requested."""
    return tools.decide_page(_mutating(tenant, x_visitor), page_id, approve)


class IngestIn(BaseModel):
    tenant: str = "ravenhold"
    text: str = Field(min_length=3, max_length=4000)
    kind: str = Field(default="chat", pattern="^(chat|doc)$")


@app.post("/api/ingest")
def ingest_knowledge(body: IngestIn, x_visitor: str | None = Visitor):
    """Company Brain: paste a chat message or document; the graph updates and reports exactly what changed."""
    tenant = _mutating(body.tenant, x_visitor)
    try:
        usage.check_budget()
    except usage.BudgetExceeded as e:
        raise HTTPException(503, str(e))
    from .ingest import ingest
    from .explain import explain
    out = ingest(tenant, body.text, body.kind)
    return {**out["result"], "summary": explain("update_knowledge", out["result"]), "subgraph": out["subgraph"]}


@app.get("/api/changes")
def recent_changes(tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    return rows(graph(_tenant(tenant, x_visitor)).ro_query(
        """MATCH (c:Change)-[:FROM_SOURCE]->(s:Source)
           RETURN c.id AS id, c.kind AS kind, c.summary AS summary, c.at AS at, s.id AS source, s.text AS source_text
           ORDER BY c.at DESC LIMIT 30"""))


@app.post("/api/runbooks/{runbook_id}/decision")
def runbook_decision(runbook_id: str, approve: bool, tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    """Human in the loop: approve or reject a runbook version the agents proposed from a correction."""
    return tools.decide_runbook(_mutating(tenant, x_visitor), runbook_id, approve)


@app.post("/api/fixes/{fix_id}/decision")
def fix_decision(fix_id: str, approve: bool, tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    """Human in the loop: approving applies the proposed fix and verifies recovery from the graph."""
    return tools.decide_fix(_mutating(tenant, x_visitor), fix_id, approve)


@app.get("/api/fixes")
def get_fixes(ids: str, tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    return tools.fix_statuses(_tenant(tenant, x_visitor), [i for i in ids.split(",") if i][:50])


@app.get("/api/pages")
def get_pages(ids: str, tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    return tools.page_statuses(_tenant(tenant, x_visitor), [i for i in ids.split(",") if i][:50])


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str, tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    g = graph(_tenant(tenant, x_visitor))
    turns = rows(g.ro_query(
        "MATCH (:Session {id: $id})-[:HAS_TURN]->(t:Turn) "
        "RETURN t.role AS role, t.text AS text, t.at AS at, t.events AS events ORDER BY t.seq",
        {"id": session_id},
    ))
    for t in turns:
        t["events"] = json.loads(t["events"]) if t.get("events") else []
    tasks = rows(g.ro_query(
        """MATCH (t:Task)-[:IN_SESSION]->(:Session {id: $id}), (t)-[:ASSIGNED_TO]->(a:Agent)
           RETURN t.id AS id, a.id AS agent, t.instructions AS instructions, t.status AS status, t.result AS result
           ORDER BY t.created_at""",
        {"id": session_id},
    ))
    return {"id": session_id, "turns": turns, "tasks": tasks}


@app.get("/api/memory")
def memory(tenant: str = "ravenhold", x_visitor: str | None = Visitor):
    return rows(graph(_tenant(tenant, x_visitor)).ro_query(
        """MATCH (l:Lesson)
           OPTIONAL MATCH (l)-[:APPLIES_TO]->(s:Service)
           OPTIONAL MATCH (l)-[:REFINES]->(rb:Runbook)
           OPTIONAL MATCH (l)-[:LEARNED_IN]->(sess:Session)
           OPTIONAL MATCH (l)-[:PRODUCED]->(nv:Runbook)
           RETURN l.id AS id, l.text AS text, l.kind AS kind, l.source AS source, l.created_at AS created_at,
                  coalesce(l.reinforced, 0) AS reinforced, collect(DISTINCT s.id) AS services,
                  rb.id AS runbook, sess.id AS session, nv.id AS proposed_version,
                  coalesce(nv.status, 'active') AS proposed_status, nv.steps AS proposed_steps
           ORDER BY created_at DESC"""
    ))


@app.post("/api/reset")
def reset(tenant: str = "ravenhold", keep_memory: bool = False, x_admin_token: str | None = Header(default=None),
          x_visitor: str | None = Visitor):
    """A visitor resets their own sandbox (a fresh copy of the template). Resetting the shared template itself
    needs the ADMIN_TOKEN header on the public deployment."""
    if SANDBOXES and x_visitor:
        name = _tenant(tenant, x_visitor)
        with _sandbox_lock:
            graph(name).delete()
            copy_graph(tenant, name)
        return {"ok": True, "sandbox": True}
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "Reset of the shared template is admin-only.")
    seed_tenant(_tenant(tenant), keep_memory)
    return {"ok": True}


# Serve the built UI from the same origin (one service, one URL) when it exists.
_dist = Path(os.getenv("FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
