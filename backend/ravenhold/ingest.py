"""Company Brain: keep the graph current from what people write.

Paste a chat message or a document; Claude Haiku extracts structured facts using only ids that exist in the graph;
each fact is applied with Cypher and recorded as a (:Change) linked to its (:Source), so every update has provenance.
History is kept, not overwritten: a team move closes the old MEMBER_OF edge (sets `to`) and opens a new one.

    extract(tenant, text) -> facts      (one LLM call, ~$0.002)
    apply_facts(tenant, source_id, facts) -> changes   (pure graph work, unit-tested without an LLM)
"""

from __future__ import annotations

from datetime import date

from . import usage
from .db import graph, rows
from .llm import make_llm
from .tools import Trace, next_number, now

KINDS = ["team_change", "on_call_change", "ownership_change", "dependency_added", "dependency_removed",
         "expertise", "decision", "follow_up", "project_member", "project_status", "ticket_update"]

RECORD_FACTS = {
    "name": "record_facts",
    "description": "Record every fact about the company stated in the text. Use only ids from the catalog.",
    "input_schema": {
        "type": "object",
        "properties": {"facts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": KINDS},
                "person_id": {"type": "string"}, "team_id": {"type": "string"}, "service_id": {"type": "string"},
                "depends_on_id": {"type": "string"}, "incident_id": {"type": "string"},
                "assignee_id": {"type": "string"}, "made_by": {"type": "string"},
                "affects": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string"},
                "project_id": {"type": "string"}, "ticket_id": {"type": "string"},
                "status": {"type": "string", "description": "project: planned|in_progress|blocked|done; ticket: open|in_progress|done"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "leaving": {"type": "boolean", "description": "project_member: true if the person leaves the project"},
                "effective_date": {"type": "string", "description": "YYYY-MM-DD if stated, else omit"},
            },
            "required": ["kind"],
        }}},
        "required": ["facts"],
    },
}

SYSTEM = """You turn what people write at a company into structured facts for its knowledge graph.
Call record_facts exactly once with every fact the text states (an empty list if none). Kinds:
- team_change: person_id moved to team_id (optional effective_date)
- on_call_change: person_id is now on call for team_id
- ownership_change: team_id now owns service_id
- dependency_added / dependency_removed: service_id depends (or no longer depends) on depends_on_id
- expertise: person_id has worked on / knows service_id
- decision: a decision with a short title; ALWAYS fill affects with every catalog service id the decision is about,
  and made_by with the person id who made or approved it if named
- follow_up: a NEW to-do with a short title, service_id, assignee_id if stated, incident_id if stated
- project_member: person_id joins project_id (leaving=true if they leave it)
- project_status: project_id has a new status and/or due_date
- ticket_update: an EXISTING ticket_id gets a new assignee_id and/or status
Use ONLY ids from the catalog. Skip anything you cannot map to catalog ids. Do not invent facts."""


def _catalog(tenant: str) -> str:
    g = graph(tenant)
    people = rows(g.ro_query(
        "MATCH (p:Person) OPTIONAL MATCH (p)-[m:MEMBER_OF]->(t:Team) WHERE m.to IS NULL "
        "RETURN p.id AS id, p.name AS name, t.id AS team ORDER BY id"))
    teams = [r[0] for r in g.ro_query("MATCH (t:Team) RETURN t.id ORDER BY t.id").result_set]
    svcs = [r[0] for r in g.ro_query("MATCH (s:Service) RETURN s.id ORDER BY s.id LIMIT 300").result_set]
    projects = g.ro_query("MATCH (p:Project) RETURN p.id, p.name ORDER BY p.id LIMIT 100").result_set
    tickets = g.ro_query("MATCH (t:Ticket) WHERE t.status <> 'done' RETURN t.id, t.title ORDER BY t.id LIMIT 100").result_set
    return ("Catalog:\nPeople (id: name, current team): " + "; ".join(f"{p['id']}: {p['name']} ({p['team']})" for p in people)
            + "\nTeams: " + ", ".join(teams) + "\nServices: " + ", ".join(svcs)
            + "\nProjects: " + "; ".join(f"{i}: {n}" for i, n in projects)
            + "\nOpen tickets: " + "; ".join(f"{i}: {n}" for i, n in tickets) + f"\nToday is {date.today()}.")


def extract(tenant: str, text: str) -> list[dict]:
    llm = make_llm()
    messages = llm.start(SYSTEM + "\n\n" + _catalog(tenant), [{"role": "user", "content": text}])
    reply = llm.call(messages, [RECORD_FACTS], worker=True)  # the cheap worker model is plenty for extraction
    for c in reply.calls:
        if c.name == "record_facts":
            return [f for f in c.input.get("facts", []) if isinstance(f, dict) and f.get("kind") in KINDS]
    return []


def _exists(t: Trace, label: str, id_: str | None) -> bool:
    return bool(id_) and bool(t.rows(f"MATCH (n:{label} {{id: $id}}) RETURN n.id AS id", {"id": id_}))


def _name(t: Trace, label: str, id_: str) -> str:
    r = t.rows(f"MATCH (n:{label} {{id: $id}}) RETURN coalesce(n.name, n.title, n.id) AS n", {"id": id_})
    return r[0]["n"] if r else id_


def apply_facts(tenant: str, source_id: str, facts: list[dict]) -> dict:
    """Apply extracted facts to the graph. Every change gets a (:Change)-[:FROM_SOURCE]->(:Source) record."""
    t = Trace(tenant)
    changes, skipped = [], []
    today = str(date.today())

    def record(kind: str, summary: str, touched: list[tuple[str, str]]):
        cid = f"CHG-{next_number(t, 'change', 0)}"
        t.q("""MATCH (src:Source {id: $src})
               CREATE (c:Change {id: $cid, kind: $kind, summary: $summary, at: $now})-[:FROM_SOURCE]->(src)""",
            {"src": source_id, "cid": cid, "kind": kind, "summary": summary, "now": now()}, write=True)
        for label, id_ in touched:
            t.q(f"MATCH (c:Change {{id: $cid}}), (n:{label} {{id: $id}}) CREATE (c)-[:CHANGED]->(n)",
                {"cid": cid, "id": id_}, write=True)
            t.link(cid, "CHANGED", id_, "Change", label, summary[:40])
        t.link(cid, "FROM_SOURCE", source_id, "Change", "Source")
        changes.append({"id": cid, "kind": kind, "summary": summary})

    def need(*pairs) -> bool:
        missing = [f"{label} '{i}'" for label, i in pairs if not _exists(t, label, i)]
        if missing:
            skipped.append(f"Not in the map: {', '.join(missing)}")
        return not missing

    for f in facts:
        k = f["kind"]
        when = f.get("effective_date") or today
        try:
            date.fromisoformat(when)
        except ValueError:
            when = today
        if k == "team_change" and need(("Person", f.get("person_id")), ("Team", f.get("team_id"))):
            cur = t.rows("MATCH (:Person {id: $p})-[m:MEMBER_OF]->(t:Team) WHERE m.to IS NULL RETURN t.id AS id",
                         {"p": f["person_id"]})
            if cur and cur[0]["id"] == f["team_id"]:
                skipped.append(f"{_name(t, 'Person', f['person_id'])} is already on {_name(t, 'Team', f['team_id'])}")
                continue
            t.q("""MATCH (p:Person {id: $p})-[m:MEMBER_OF]->(:Team) WHERE m.to IS NULL SET m.to = date($d)""",
                {"p": f["person_id"], "d": when}, write=True)
            t.q("""MATCH (p:Person {id: $p}), (tm:Team {id: $t}) CREATE (p)-[:MEMBER_OF {from: date($d), to: null}]->(tm)""",
                {"p": f["person_id"], "t": f["team_id"], "d": when}, write=True)
            old = _name(t, "Team", cur[0]["id"]) if cur else "no team"
            record(k, f"{_name(t, 'Person', f['person_id'])} moved from {old} to {_name(t, 'Team', f['team_id'])} on {when}. "
                      "The old membership was closed, not deleted, so history is kept.",
                   [("Person", f["person_id"]), ("Team", f["team_id"])])
        elif k == "on_call_change" and need(("Person", f.get("person_id")), ("Team", f.get("team_id"))):
            t.q("MATCH (:Person)-[r:ON_CALL_FOR]->(:Team {id: $t}) DELETE r", {"t": f["team_id"]}, write=True)
            t.q("MATCH (p:Person {id: $p}), (tm:Team {id: $t}) CREATE (p)-[:ON_CALL_FOR]->(tm)",
                {"p": f["person_id"], "t": f["team_id"]}, write=True)
            record(k, f"{_name(t, 'Person', f['person_id'])} is now on call for {_name(t, 'Team', f['team_id'])}.",
                   [("Person", f["person_id"]), ("Team", f["team_id"])])
        elif k == "ownership_change" and need(("Team", f.get("team_id")), ("Service", f.get("service_id"))):
            t.q("MATCH (:Team)-[r:OWNS]->(:Service {id: $s}) DELETE r", {"s": f["service_id"]}, write=True)
            t.q("MATCH (tm:Team {id: $t}), (s:Service {id: $s}) CREATE (tm)-[:OWNS]->(s)",
                {"t": f["team_id"], "s": f["service_id"]}, write=True)
            record(k, f"{_name(t, 'Team', f['team_id'])} now owns {_name(t, 'Service', f['service_id'])}.",
                   [("Team", f["team_id"]), ("Service", f["service_id"])])
        elif k in ("dependency_added", "dependency_removed") and need(("Service", f.get("service_id")),
                                                                       ("Service", f.get("depends_on_id"))):
            if k == "dependency_added":
                t.q("MATCH (a:Service {id: $a}), (b:Service {id: $b}) MERGE (a)-[:DEPENDS_ON]->(b)",
                    {"a": f["service_id"], "b": f["depends_on_id"]}, write=True)
                verb = "now depends on"
            else:
                t.q("MATCH (:Service {id: $a})-[r:DEPENDS_ON]->(:Service {id: $b}) DELETE r",
                    {"a": f["service_id"], "b": f["depends_on_id"]}, write=True)
                verb = "no longer depends on"
            record(k, f"{_name(t, 'Service', f['service_id'])} {verb} {_name(t, 'Service', f['depends_on_id'])}. "
                      "Blast-radius answers use this immediately.",
                   [("Service", f["service_id"]), ("Service", f["depends_on_id"])])
        elif k == "expertise" and need(("Person", f.get("person_id")), ("Service", f.get("service_id"))):
            t.q("""MATCH (p:Person {id: $p}), (s:Service {id: $s})
                   MERGE (p)-[c:CONTRIBUTED_TO]->(s)
                   SET c.commits = coalesce(c.commits, 0) + 1, c.last_at = date($d)""",
                {"p": f["person_id"], "s": f["service_id"], "d": today}, write=True)
            record(k, f"{_name(t, 'Person', f['person_id'])} knows {_name(t, 'Service', f['service_id'])}; "
                      "expert search will now consider them.",
                   [("Person", f["person_id"]), ("Service", f["service_id"])])
        elif k == "decision" and f.get("title"):
            affects = [s for s in f.get("affects", []) if _exists(t, "Service", s)]
            did = f"ADR-{next_number(t, 'decision', 20)}"
            t.q("""MATCH (src:Source {id: $src})
                   CREATE (d:Decision {id: $id, title: $title, date: date($d), status: 'accepted'})-[:DERIVED_FROM]->(src)
                   WITH d UNWIND $affects AS sid MATCH (s:Service {id: sid}) CREATE (d)-[:AFFECTS]->(s)""",
                {"src": source_id, "id": did, "title": f["title"], "d": today, "affects": affects}, write=True)
            if _exists(t, "Person", f.get("made_by")):
                t.q("MATCH (p:Person {id: $p}), (d:Decision {id: $id}) CREATE (p)-[:MADE]->(d)",
                    {"p": f["made_by"], "id": did}, write=True)
            record(k, f"New decision {did}: \"{f['title']}\""
                      + (f", affecting {', '.join(affects)}" if affects else "") + ", traced back to this source.",
                   [("Decision", did), *[("Service", s) for s in affects]])
        elif k == "follow_up" and f.get("title") and need(("Service", f.get("service_id"))):
            tid = f"TKT-{next_number(t, 'ticket', 600)}"
            t.q("""MATCH (s:Service {id: $s})
                   CREATE (tk:Source:Ticket {id: $id, kind: 'ticket', title: $title, text: $title, status: 'open',
                                             created_at: $now})-[:ABOUT]->(s)""",
                {"s": f["service_id"], "id": tid, "title": f["title"], "now": now()}, write=True)
            touched = [("Source", tid), ("Service", f["service_id"])]
            if _exists(t, "Person", f.get("assignee_id")):
                t.q("MATCH (tk:Ticket {id: $id}), (p:Person {id: $p}) CREATE (tk)-[:ASSIGNED_TO]->(p)",
                    {"id": tid, "p": f["assignee_id"]}, write=True)
                touched.append(("Person", f["assignee_id"]))
            if _exists(t, "Incident", f.get("incident_id")):
                t.q("MATCH (tk:Ticket {id: $id}), (i:Incident {id: $i}) CREATE (tk)-[:FOLLOW_UP_OF]->(i)",
                    {"id": tid, "i": f["incident_id"]}, write=True)
            record(k, f"New to-do {tid}: \"{f['title']}\" on {_name(t, 'Service', f['service_id'])}.", touched)
        elif k == "project_member" and need(("Person", f.get("person_id")), ("Project", f.get("project_id"))):
            who, prj = _name(t, "Person", f["person_id"]), _name(t, "Project", f["project_id"])
            if f.get("leaving"):
                t.q("MATCH (:Person {id: $p})-[r:WORKS_ON]->(:Project {id: $j}) DELETE r",
                    {"p": f["person_id"], "j": f["project_id"]}, write=True)
                summary = f"{who} left the project {prj}."
            else:
                t.q("MATCH (p:Person {id: $p}), (j:Project {id: $j}) MERGE (p)-[:WORKS_ON]->(j)",
                    {"p": f["person_id"], "j": f["project_id"]}, write=True)
                summary = f"{who} now works on {prj}."
            record(k, summary, [("Person", f["person_id"]), ("Project", f["project_id"])])
        elif k == "project_status" and need(("Project", f.get("project_id"))):
            status = f.get("status") if f.get("status") in ("planned", "in_progress", "blocked", "done") else None
            due = f.get("due_date")
            try:
                due = str(date.fromisoformat(due)) if due else None
            except ValueError:
                due = None
            if not status and not due:
                skipped.append(f"No status or due date to change for {f['project_id']}")
                continue
            t.q("MATCH (j:Project {id: $j}) SET j.status = coalesce($s, j.status), j.due = coalesce(date($d), j.due)",
                {"j": f["project_id"], "s": status, "d": due}, write=True)
            record(k, f"{_name(t, 'Project', f['project_id'])}: " + ", ".join(filter(None, [
                f"status is now {status.replace('_', ' ')}" if status else "", f"due {due}" if due else ""])) + ".",
                [("Project", f["project_id"])])
        elif k == "ticket_update" and need(("Ticket", f.get("ticket_id"))):
            parts = []
            if f.get("assignee_id") and need(("Person", f["assignee_id"])):
                t.q("MATCH (:Ticket {id: $t})-[r:ASSIGNED_TO]->(:Person) DELETE r", {"t": f["ticket_id"]}, write=True)
                t.q("MATCH (tk:Ticket {id: $t}), (p:Person {id: $p}) CREATE (tk)-[:ASSIGNED_TO]->(p)",
                    {"t": f["ticket_id"], "p": f["assignee_id"]}, write=True)
                parts.append(f"now assigned to {_name(t, 'Person', f['assignee_id'])}")
            if f.get("status") in ("open", "in_progress", "done"):
                t.q("MATCH (tk:Ticket {id: $t}) SET tk.status = $s", {"t": f["ticket_id"], "s": f["status"]}, write=True)
                parts.append(f"status {f['status'].replace('_', ' ')}")
            if not parts:
                continue
            record(k, f"{f['ticket_id']} is " + " and ".join(parts) + ".",
                   [("Ticket", f["ticket_id"])] + ([("Person", f["assignee_id"])] if f.get("assignee_id") else []))
    return t.out({"changes": changes, "skipped": skipped})


def ingest(tenant: str, text: str, kind: str = "chat", author_id: str | None = None) -> dict:
    """Store the text as a Source, extract facts with Claude, apply them, and report exactly what changed."""
    usage.check_budget()
    t = Trace(tenant)
    sid = f"SRC-{next_number(t, 'source', 100)}"
    label = "Message" if kind == "chat" else "Doc"
    t.q(f"""CREATE (s:Source:{label} {{id: $id, kind: $kind, title: $title, text: $text, date: localdatetime(),
                                         ingested: true}})""",
        {"id": sid, "kind": kind, "title": text[:60], "text": text}, write=True)
    if _exists(t, "Person", author_id):
        t.q("MATCH (p:Person {id: $p}), (s:Source {id: $id}) CREATE (p)-[:POSTED]->(s)",
            {"p": author_id, "id": sid}, write=True)
    facts = extract(tenant, text)
    out = apply_facts(tenant, sid, facts)
    out["result"]["source_id"] = sid
    out["result"]["facts_found"] = len(facts)
    return out
