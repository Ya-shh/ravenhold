"""Three agents that coordinate only through the graph.

Commander    talks to the human, delegates, takes actions, writes memory.
Investigator blast radius, root cause, who to page (Track 1 + 3).
Historian    past incidents, runbooks, lessons from earlier sessions (Track 2).

Delegation creates (:Task) nodes ASSIGNED_TO an (:Agent). Workers run in parallel
threads, read their task from the graph, and write their result and an (:Episode)
back to it. The Commander reads the results back from the graph, not from memory.
Conversation history is stored as (:Turn) nodes, so a session survives restarts.
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import mcp_bridge, usage
from .db import graph, rows
from .explain import explain
from .llm import ToolResult, make_llm
from .tools import now, short_id

MAX_STEPS = 12

Emit = Callable[[dict], None]

WORKER_TOOLS = {
    "investigator": ["correlate_alerts", "blast_radius", "root_cause_candidates", "find_experts", "open_follow_ups",
                     "critical_services", "shortest_connection", "trace_decisions", "graph_query"],
    "historian": ["similar_incidents", "get_runbook", "recall_memory", "search_knowledge",
                  "trace_decisions", "open_follow_ups", "project_overview", "graph_query"],
}
COMMANDER_TOOLS = ["situation_report", "correlate_alerts", "critical_services", "trace_decisions", "project_overview",
                   "update_knowledge", "propose_fix", "check_recovery",
                   "recall_memory", "declare_incident", "page_person", "create_ticket", "resolve_incident", "record_lesson",
                   "graph_query"]

SCHEMA_NOTE = """Graph model (FalkorDB, Cypher):
(:Person)-[:MEMBER_OF {from,to}]->(:Team)-[:OWNS]->(:Service)-[:DEPENDS_ON]->(:Service)
(:Person)-[:ON_CALL_FOR]->(:Team)   (:Person)-[:CONTRIBUTED_TO {commits,last_at: date}]->(:Service)
Dates are native types: Incident.date, MEMBER_OF.from/to, Decision.date are date(); MEMBER_OF.to IS NULL = current team.
Use date(), duration('P90D') etc. for time filters, e.g. WHERE i.date >= date() - duration('P90D').
(:Service)-[:POWERS]->(:Product)<-[:USES]-(:Customer {arr,sla})
(:Incident)-[:ROOT_CAUSE|AFFECTED]->(:Service)  (:Person)-[:RESOLVED]->(:Incident)-[:FIXED_WITH]->(:Runbook)-[:FOR]->(:Service)
(:Doc|:Message|:Ticket are all :Source)  (:Doc)-[:DOCUMENTS]->(:Incident)  (:Ticket)-[:FOLLOW_UP_OF]->(:Incident)
(:Decision)-[:DERIVED_FROM]->(:Source)  (:Decision)-[:AFFECTS]->(:Service)  (:Person)-[:MADE]->(:Decision)
Signals: (:Alert {severity,status:'firing'})-[:ON]->(:Service)
Work: (:Team)-[:OWNS]->(:Project {status, due: date})<-[:LEADS|WORKS_ON]-(:Person); (:Project)-[:TOUCHES]->(:Service),
      (:Project)-[:IMPLEMENTS]->(:Decision), (:Project)-[:DOCUMENTED_IN]->(:Doc), (:Ticket)-[:PART_OF]->(:Project)
Memory: (:Lesson)-[:APPLIES_TO]->(:Service), (:Lesson)-[:REFINES]->(:Runbook), (:Lesson)-[:LEARNED_IN]->(:Session),
        (:Lesson)-[:PRODUCED]->(:Runbook)-[:SUPERSEDES]->(:Runbook)   (runbooks are versioned: RB-7 -> RB-7-v2)
Ids: services are kebab-case (payments-db), people are first names (sarah), INC-142, RB-7, TKT-512, ADR-12, PRJ-POOL.
Products (checkout, payouts...) are NOT services: tools that take service_id need a service id."""

CITE = """Cite graph node ids in plain square brackets, e.g. [INC-142] [RB-7] [TKT-512] [sarah] [pgbouncer].
Never cite TASK-/EP-/SES- ids and never use 【】 brackets.
GROUNDING: state only facts that appear in tool results or worker reports. Do not add plausible-sounding details
(upgrades, alerts, metrics, dates) that the graph did not return. If something is unknown, say it is unknown."""

COMMANDER_SYSTEM = f"""You are the Commander of Ravenhold, an incident-response agent team. All company context
lives in a FalkorDB graph. You talk to the on-call human. You have two workers, reached with `delegate`; they run in
parallel and share state with you only through Task nodes in the graph.

INVESTIGATE FIRST. You are an agent with the whole company graph, not a form. Never reply with only questions.
- Alerts firing, or a report like "everything is slow" / "we're losing money" / "something is wrong" with no clear
  service: FIRST call `situation_report` (pass money_usd if a money figure is mentioned and keywords for symptoms) and,
  if alerts are firing, `correlate_alerts`. If correlation gives ONE clear root cause, run the live incident playbook
  on it straight away (declare on the most customer-facing alerting service, root candidate = correlated root).
  If correlate_alerts lists likely_unrelated_noise, say in one line that those alerts look unrelated and why
  (nothing alerting depends on them), so the human does not chase them.
  Otherwise answer with what the graph shows (exposure, active alerts/incidents, risky open follow-ups), 2-3 ranked
  hypotheses with evidence, and at most ONE question.
- Greetings or messages not about the company: 2-3 sentences on what you can do here, no tools.

LIVE INCIDENT PLAYBOOK (symptoms on a service):
1. In ONE response call both `declare_incident` (on the symptomatic service) and `delegate` with these two tasks:
   - investigator: "Symptom service: <id>. Symptoms: <...>. Run root_cause_candidates(<id>). Run blast_radius on <id>
     and on the top root-cause candidate. Run find_experts on the top 1-2 root-cause candidates, preferring ones that caused past incidents (not only on <id>).
     Run open_follow_ups on the top candidate. Report root cause hypothesis, impact, best person to page vs on-call."
     (If correlate_alerts already found the root, say so in the task and run blast_radius/find_experts on it.)
   - historian: "Symptom service: <id>. Symptoms: <...>. Run similar_incidents(<id>, description=<symptoms>).
     Run recall_memory on <id> and its dependencies. For the runbooks those incidents used, run get_runbook.
     Report what fixed it before, what did NOT work, and lessons from earlier sessions."
2. When the reports come back, ACT, do not just suggest: call `page_person` for the person the graph shows actually
   fixed / knows the likely root cause (if that is not the on-call, say why: e.g. resolved INC-x, moved teams).
   Paging is human-approved: page_person creates a page request that the on-call human approves in the UI, so write
   "I've asked to page X (waiting for your approval)", never "paged".
   Then call `propose_fix` with the incident id, the root-cause service and the runbook the Historian recommends
   (the latest approved version is used automatically), and a one-line reason from the graph. It waits for human
   approval: write "I've proposed applying RB-x to <service> (waiting for your approval)". When the human says it was
   applied, or asks whether it worked, call `check_recovery` and report what cleared, what still fires and whether
   that is related to this incident.
   Call `create_ticket` only for a clear gap. Never invent ids; use only ids that tools returned.
3. Then answer.

OTHER QUESTIONS: use the dedicated tool, not graph_query. Risks / single points of failure / what matters most:
call `critical_services` yourself and say the ranking comes from graph algorithms (PageRank, betweenness). Why
something was built a certain way: call `trace_decisions` yourself and cite its sources. Projects, who is working on
what, what a team or person is busy with, or what is at risk: call `project_overview` yourself, ONCE (filter by team_id,
person_id, project_id or service_id; one call returns members, tickets, decisions and risks), state the risks it
found and who could unblock them. Impact and experts: delegate
the investigator with concrete tool instructions. Use graph_query only when no dedicated tool answers the question.
CORRECTIONS: when the human corrects you or teaches you how a system behaves or should be fixed, call `record_lesson`
with kind='correction' and the service ids it applies to. If it changes how a runbook should be run, also pass
refines_runbook and updated_steps (the complete corrected steps, numbered); this PROPOSES a new runbook version (e.g.
RB-7-v2). Agents keep using the current version until the human approves the proposal in the UI, so say "I've
proposed RB-7-v2 (waiting for your approval)".
If the human says it is fixed, call `resolve_incident`.
KNOWLEDGE UPDATES: when the human states a fact about the company (someone moved team, who is on call now, who owns a
service, a new or removed dependency, a decision, a to-do, who works on a project, a project's status, who a ticket
is assigned to), call `update_knowledge` with their words, then say exactly
what changed in the map. Corrections about how to fix things go to record_lesson instead.

ANSWER FORMAT (the reader is mid-incident): FIRST line: `**In plain words:** ...`, one or two sentences a teenager
with no technical background would understand (what is wrong, what it means for customers, what happens next; no ids,
no jargon, use the systems' everyday names). Then a 1-line bold verdict. Then short sections:
**Likely root cause**, **Impact** (services, products, every affected customer BY NAME with its ARR, total ARR), **Who we paged and why**, **Do now**
(numbered steps, include what NOT to do from past incidents), **Open follow-ups**. Under 240 words. No tables.
NO BARE CODES OR JARGON: the reader may not be an engineer. The first time a system, record or person appears, add a
few everyday words right after it, then cite it, e.g. "PgBouncer (the connection pool in front of the payments
database) [pgbouncer]", "the March outage [INC-142]", "the written fix [RB-7]", "an open to-do [TKT-512]", "the design
decision [ADR-12]", "Sarah, the engineer who fixed it last time [sarah]". After that, the short name alone is fine.
Only when recall_memory marks a lesson earlier_session=true, or a worker report has a line "REMEMBERED FROM AN
EARLIER SESSION", say "Remembered from an earlier session: ..." and state what changed. Never say it about things said in this conversation,
and if no earlier lesson applies, do not mention memory at all. For non-incident questions use a structure that fits
the question instead of the incident sections.
{CITE}
LAST LINE, always: `NEXT: <option> | <option> | <option>`: 2-3 short follow-ups (max 7 words each), written as the
user would type them. If you asked a question, the options are its likely answers. Otherwise suggest the most useful
next steps (e.g. teach a correction, check impact of the root cause, mark it resolved).

{SCHEMA_NOTE}"""

WORKER_SYSTEM = {
    "investigator": f"""You are the Investigator agent of Ravenhold. Do the Commander's task with the graph tools over
a FalkorDB company graph. Use tools; never guess. Prefer dedicated tools over graph_query. Call independent tools in
parallel in one response. If a tool says an id is unknown, use the suggested ids. Finish with a compact bullet report:
root-cause hypothesis with evidence, impacted services/products/customers + ARR, best person to page and why vs the
official on-call (commits, incidents resolved, team moves), open follow-ups.
{CITE}

{SCHEMA_NOTE}""",
    "historian": f"""You are the Historian agent of Ravenhold. You own the company's memory in a FalkorDB graph: past
incidents, runbooks, postmortems and lessons learned in earlier agent sessions (including human corrections). Use
tools; never guess. Call independent tools in parallel in one response. Always call recall_memory. Finish with a compact
bullet report: most similar past incidents and what fixed them, what did NOT work, the runbook to use including any
refinements from memory, lessons from earlier sessions. get_runbook always returns the latest version: if a runbook
was updated by a human correction, give the new version id and what changed. For every lesson recall_memory marks
earlier_session=true, write a line starting "REMEMBERED FROM AN EARLIER SESSION:" with the lesson and its id.
{CITE}

{SCHEMA_NOTE}""",
}


def catalog(tenant: str) -> str:
    """Valid ids straight from the graph, so the model never has to guess them."""
    g = graph(tenant)
    cap = 150  # keep prompts small for big companies; anything else is found with search_knowledge / graph_query
    total = g.ro_query("MATCH (s:Service) RETURN count(s)").result_set[0][0]
    svcs = [r[0] for r in g.ro_query("MATCH (s:Service) RETURN s.id ORDER BY s.tier, s.id LIMIT $n", {"n": cap}).result_set]
    prods = rows(g.ro_query(
        "MATCH (s:Service)-[:POWERS]->(p:Product) WITH p, collect(s.id)[..8] AS s RETURN p.id AS p, s ORDER BY p LIMIT 40"))
    people = [r[0] for r in g.ro_query("MATCH (p:Person) RETURN p.id ORDER BY p.id LIMIT $n", {"n": cap}).result_set]
    more = f" (showing {cap} of {total}; use graph_query to find others)" if total > cap else ""
    return (f"\n\nCatalog for this tenant graph:\nService ids{more}: " + ", ".join(svcs)
            + "\nProducts (not services) -> powering services: "
            + "; ".join(f"{r['p']} -> {', '.join(r['s'])}" for r in prods)
            + "\nPeople ids: " + ", ".join(people)
            + "\nTeam ids: " + ", ".join(r[0] for r in g.ro_query("MATCH (t:Team) RETURN t.id ORDER BY t.id").result_set)
            + "\nProject ids: " + ", ".join(f"{r[0]} ({r[1]})" for r in g.ro_query(
                "MATCH (p:Project) RETURN p.id, p.name ORDER BY p.id LIMIT 60").result_set))

DELEGATE_SPEC = {
    "name": "delegate",
    "description": "Assign tasks to worker agents. They run in parallel; each writes its result to a Task node in the "
                   "graph, which is returned to you. Put all tasks for one step in a single call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "enum": ["investigator", "historian"]},
                        "instructions": {"type": "string"},
                    },
                    "required": ["agent", "instructions"],
                },
            }
        },
        "required": ["tasks"],
    },
}


# ---------------------------------------------------------------------------
# Session + conversation history in the graph

def ensure_session(tenant: str, session_id: str | None) -> str:
    g = graph(tenant)
    if session_id and g.ro_query("MATCH (s:Session {id: $id}) RETURN s.id", {"id": session_id}).result_set:
        return session_id
    sid = session_id or short_id("SES")
    g.query("CREATE (:Session {id: $id, started_at: $now})", {"id": sid, "now": now()})
    prev = g.ro_query(
        "MATCH (s:Session) WHERE s.id <> $id RETURN s.id ORDER BY s.started_at DESC LIMIT 1", {"id": sid}
    ).result_set
    if prev:
        g.query("MATCH (a:Session {id: $a}), (b:Session {id: $b}) CREATE (a)-[:PREVIOUS]->(b)",
                {"a": sid, "b": prev[0][0]})
    return sid


TRACE_EVENTS = {"task_created", "task_status", "tool_call", "tool_result", "usage", "approval", "runbook_proposal",
                "fix_proposal", "final"}


def add_turn(tenant: str, session_id: str, role: str, text: str, events: list | None = None) -> None:
    """A conversation turn. Assistant turns also keep their full trace, so a reopened session shows every step."""
    trace = json.dumps(events or [], default=str, separators=(",", ":"))
    if len(trace) > 400_000:  # keep turns small: drop the highlight subgraphs, keep steps and Cypher
        trace = json.dumps([{k: v for k, v in e.items() if k != "subgraph"} for e in events], default=str,
                           separators=(",", ":"))
    graph(tenant).query(
        """MATCH (s:Session {id: $sid})
           OPTIONAL MATCH (s)-[:HAS_TURN]->(t:Turn) WITH s, count(t) AS n
           CREATE (s)-[:HAS_TURN]->(:Turn {role: $role, text: $text, at: $now, seq: n, events: $events})""",
        {"sid": session_id, "role": role, "text": text, "now": now(), "events": trace},
    )


def history(tenant: str, session_id: str) -> list[dict]:
    return rows(graph(tenant).ro_query(
        "MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn) RETURN t.role AS role, t.text AS text ORDER BY t.seq",
        {"sid": session_id},
    ))


RESULT_CHARS = 5000  # keeps token use low enough for free-tier rate limits


def _summarize(result) -> str:
    s = json.dumps(result, default=str, separators=(",", ":"))
    return s if len(s) < RESULT_CHARS else s[:RESULT_CHARS] + "...(truncated)"


def verify_citations(tenant: str, text: str) -> dict:
    """Grounding check that does not trust the model: every [id] cited in the answer is looked up in the graph."""
    ids = sorted(set(re.findall(r"\[([A-Za-z0-9][\w.-]*)\]", text)))
    if not ids:
        return {"verified": [], "unknown": []}
    found = {r[0] for r in graph(tenant).ro_query("MATCH (n) WHERE n.id IN $ids RETURN n.id", {"ids": ids}).result_set}
    return {"verified": [i for i in ids if i in found], "unknown": [i for i in ids if i not in found]}


def split_suggestions(text: str) -> tuple[str, list[str]]:
    """Pull the trailing `NEXT: a | b | c` line out of an answer as clickable follow-ups."""
    m = re.search(r"(?:^|\n)[ \t]*\**(?:NEXT|Next)\**[ \t]*:\**[ \t]*([^\n]+?)[ \t]*$", text.rstrip())
    if not m or "|" not in m.group(1):
        return text.strip(), []
    opts = [o.strip(" `*-[]") for o in m.group(1).split("|")]
    return text.rstrip()[: m.start()].strip(), [o for o in opts if 0 < len(o) <= 80][:3]


def clean_citations(text: str) -> str:
    """Normalise citation styles some open models use (【x】) and drop internal task ids."""
    text = text.replace("【", "[").replace("】", "]")
    return re.sub(r"\s*\[(?:TASK|EP|SES)-[0-9a-f]{6}[^\]]*\]", "", text)


def tool_loop(tenant: str, session_id: str, agent: str, system: str, tool_names: list[str], history: list[dict],
              emit: Emit, extra_tools: dict | None = None) -> tuple[str, list[dict]]:
    """Run a tool-use loop on whichever LLM backend is configured. Returns (final_text, touched_subgraphs)."""
    llm = make_llm()
    # The model chooses from the MCP server's tools/list (see mcp_bridge.py); delegate stays in-process because it
    # starts the worker agents.
    graph_specs, _via = mcp_bridge.specs_for(tool_names)
    specs = graph_specs + [t["spec"] for t in (extra_tools or {}).values()]
    allowed = set(tool_names) | set(extra_tools or {})
    messages = llm.start(system, history)
    touched = []
    for _ in range(MAX_STEPS):
        reply = llm.call(messages, specs, worker=agent != "commander")
        llm.add_assistant(messages, reply)
        if reply.cost:  # every model call is logged and streamed, so spend is never hidden
            emit({"type": "usage", **usage.log_call(session_id, agent, reply.cost)})
        if reply.refused:
            return "The model declined this request.", touched
        if reply.text.strip() and reply.calls:
            emit({"type": "thought", "agent": agent, "text": reply.text})
        if not reply.calls:
            return reply.text or "(no answer)", touched

        def run(c):
            emit({"type": "tool_call", "agent": agent, "tool": c.name, "input": c.input, "id": c.id})
            if c.error:
                out = {"result": {"error": f"{c.error}. Call {c.name} again with valid JSON arguments."}}
            elif c.name not in allowed:
                out = {"result": {"error": f"unknown tool '{c.name}'. Available: {sorted(allowed)}"}}
            elif extra_tools and c.name in extra_tools:
                out = extra_tools[c.name]["fn"](c.input)
            else:
                out = mcp_bridge.call(tenant, session_id, c.name, dict(c.input))
            emit({"type": "tool_result", "agent": agent, "tool": c.name, "id": c.id, "via": out.get("via", "in-process"),
                  "cypher": out.get("cypher", []), "subgraph": out.get("subgraph"),
                  "summary": explain(c.name, out.get("result")),
                  "preview": _summarize(out["result"])[:600]})
            res = out.get("result") or {}
            if c.name == "record_lesson" and isinstance(res, dict) and res.get("new_runbook_version"):
                emit({"type": "runbook_proposal", "runbook_id": res["new_runbook_version"],
                      "supersedes": res.get("supersedes"), "steps": c.input.get("updated_steps", ""),
                      "because": c.input.get("text", "")})
            if c.name == "propose_fix" and isinstance(res, dict) and res.get("status") == "awaiting human approval":
                emit({"type": "fix_proposal", "fix_id": res["fix_id"], "runbook_id": res["runbook"],
                      "runbook_title": res.get("runbook_title"), "service": res["service"],
                      "service_name": res.get("service_name"), "incident_id": c.input.get("incident_id"),
                      "steps": res.get("steps") or "", "reason": c.input.get("reason", ""),
                      "alarms": res.get("alarms_it_should_clear", 0), "systems": res.get("systems_covered", [])})
            if c.name == "page_person" and isinstance(res, dict) and res.get("status") == "awaiting human approval":
                emit({"type": "approval", "page_id": res["page_id"], "person_id": c.input.get("person_id"),
                      "name": res.get("person"), "message": c.input.get("message", ""),
                      "incident_id": c.input.get("incident_id")})
            return c, out

        # Parallel tool calls run concurrently; all results go back together.
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(run, reply.calls))
        batch = []
        for c, out in results:
            if out.get("subgraph"):
                touched.append(out["subgraph"])
            err = isinstance(out["result"], dict) and "error" in out["result"]
            batch.append(ToolResult(c.id, c.name, _summarize(out["result"]), err))
        llm.add_tool_results(messages, batch)
    return "I hit my step limit before finishing.", touched


# ---------------------------------------------------------------------------
# Workers: read the task from the graph, write the result + an Episode back

def run_worker(tenant: str, session_id: str, task_id: str, emit: Emit) -> None:
    g = graph(tenant)
    task = rows(g.ro_query(
        "MATCH (t:Task {id: $id})-[:ASSIGNED_TO]->(a:Agent) RETURN t.instructions AS instructions, a.id AS agent",
        {"id": task_id},
    ))[0]
    agent = task["agent"]
    g.query("MATCH (t:Task {id: $id}) SET t.status = 'running', t.started_at = $now", {"id": task_id, "now": now()})
    emit({"type": "task_status", "task_id": task_id, "agent": agent, "status": "running"})
    try:
        text, touched = tool_loop(
            tenant, session_id, agent, WORKER_SYSTEM[agent] + catalog(tenant), WORKER_TOOLS[agent],
            [{"role": "user", "content": task["instructions"]}], emit,
        )
        status = "done"
    except Exception as e:
        text, touched, status = f"Worker failed: {type(e).__name__}: {e}", [], "failed"

    complete_task(tenant, session_id, task_id, agent, status, text, sorted({n["id"] for sg in touched for n in sg["nodes"]}))
    emit({"type": "task_status", "task_id": task_id, "agent": agent, "status": status, "result": text})


def create_task(tenant: str, session_id: str, agent: str, instructions: str) -> str:
    """The handoff: (Commander)-[:DELEGATED]->(:Task)-[:ASSIGNED_TO]->(worker), linked to the conversation."""
    tid = short_id("TASK")
    graph(tenant).query(
        """MATCH (c:Agent {id: 'commander'}), (w:Agent {id: $agent}), (s:Session {id: $sid})
           CREATE (t:Task {id: $tid, instructions: $ins, status: 'todo', created_at: $now})
           CREATE (c)-[:DELEGATED]->(t), (t)-[:ASSIGNED_TO]->(w), (t)-[:IN_SESSION]->(s)""",
        {"agent": agent, "sid": session_id, "tid": tid, "ins": instructions, "now": now()},
    )
    return tid


def complete_task(tenant: str, session_id: str, task_id: str, agent: str, status: str, text: str,
                  touched_ids: list[str]) -> str:
    """A worker's write-back: the result on the Task, plus an Episode that TOUCHED every node it read."""
    ep = short_id("EP")
    graph(tenant).query(
        """MATCH (t:Task {id: $tid}), (a:Agent {id: $agent}), (s:Session {id: $sid})
           SET t.status = $status, t.result = $text, t.done_at = $now
           CREATE (e:Episode {id: $ep, agent: $agent, summary: $text, at: $now})
           CREATE (a)-[:PERFORMED]->(e), (e)-[:FOR_TASK]->(t), (e)-[:IN_SESSION]->(s)
           WITH e UNWIND $touched AS nid
           MATCH (n {id: nid}) WHERE NOT n:Episode AND NOT n:Task
           CREATE (e)-[:TOUCHED]->(n)""",
        {"tid": task_id, "agent": agent, "sid": session_id, "status": status, "text": text, "now": now(),
         "ep": ep, "touched": touched_ids[:60]},
    )
    return ep


def delegate(tenant: str, session_id: str, tasks: list[dict], emit: Emit) -> dict:
    g = graph(tenant)
    ids = []
    for t in tasks:
        tid = create_task(tenant, session_id, t["agent"], t["instructions"])
        ids.append(tid)
        emit({"type": "task_created", "task_id": tid, "agent": t["agent"], "instructions": t["instructions"]})
    threads = [threading.Thread(target=run_worker, args=(tenant, session_id, tid, emit)) for tid in ids]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    # Read results back from the graph: the graph is the shared state, not Python memory.
    results = rows(g.ro_query(
        """MATCH (t:Task)-[:ASSIGNED_TO]->(a:Agent) WHERE t.id IN $ids
           RETURN t.id AS task_id, a.id AS agent, t.status AS status, t.result AS result""",
        {"ids": ids},
    ))
    return {"result": {"tasks": results}, "cypher": [], "subgraph": None}


# ---------------------------------------------------------------------------
# Entry point for one human message

def handle_message(tenant: str, session_id: str | None, text: str, emit: Emit) -> dict:
    usage.check_budget()  # fail fast, before any graph writes
    sid = ensure_session(tenant, session_id)
    emit({"type": "session", "session_id": sid})
    past = history(tenant, sid)
    add_turn(tenant, sid, "user", text)

    messages: list = [{"role": t["role"], "content": t["text"]} for t in past]
    messages.append({"role": "user", "content": text})

    recorded: list[dict] = []
    lock = threading.Lock()

    def rec(ev: dict) -> None:  # stream every event, and keep the trace for this turn
        if ev["type"] in TRACE_EVENTS:
            with lock:
                recorded.append(ev)
        emit(ev)

    extra = {"delegate": {"spec": DELEGATE_SPEC, "fn": lambda inp: delegate(tenant, sid, inp.get("tasks", []), rec)}}
    answer, _ = tool_loop(tenant, sid, "commander", COMMANDER_SYSTEM + catalog(tenant), COMMANDER_TOOLS, messages, rec,
                          extra_tools=extra)
    answer, suggestions = split_suggestions(clean_citations(answer))
    final = {"type": "final", "agent": "commander", "text": answer, "session_id": sid, "suggestions": suggestions,
             "citations": verify_citations(tenant, answer)}
    recorded.append(final)
    add_turn(tenant, sid, "assistant", answer, recorded)
    emit(final)
    return {"session_id": sid, "answer": answer}


if __name__ == "__main__":
    import sys

    def printer(ev):
        t = ev["type"]
        if t == "tool_call":
            print(f"  [{ev['agent']}] -> {ev['tool']}({json.dumps(ev['input'])[:120]})")
        elif t == "task_created":
            print(f"  [commander] delegated {ev['task_id']} to {ev['agent']}: {ev['instructions'][:100]}")
        elif t == "task_status" and ev["status"] != "running":
            print(f"  [{ev['agent']}] {ev['status']} {ev['task_id']}")
        elif t == "final":
            print("\n" + ev["text"])

    session = sys.argv[2] if len(sys.argv) > 2 else None
    before = usage.summary()["spent_usd"]
    out = handle_message("ravenhold", session, sys.argv[1], printer)
    s = usage.summary()
    print(f"\n(session {out['session_id']})  this run ${s['spent_usd'] - before:.4f} · "
          f"total ${s['spent_usd']:.4f} of ${s['budget_usd']:.2f} budget")
