"""Load the synthetic company into FalkorDB.

    uv run python -m ravenhold.seed            # loads tenants ravenhold + ravenhold-labs
    uv run python -m ravenhold.seed --keep-memory   # reload company data, keep agent memory
"""

import sys

from . import seed_data as D
from .embed import embed
from .db import client, graph

INDEXES = [
    "CREATE INDEX FOR (n:Service) ON (n.id)",
    "CREATE INDEX FOR (n:Person) ON (n.id)",
    "CREATE INDEX FOR (n:Team) ON (n.id)",
    "CREATE INDEX FOR (n:Incident) ON (n.id)",
    "CREATE INDEX FOR (n:Customer) ON (n.id)",
    "CREATE INDEX FOR (n:Product) ON (n.id)",
    "CREATE INDEX FOR (n:Runbook) ON (n.id)",
    "CREATE INDEX FOR (n:Source) ON (n.id)",
    "CREATE INDEX FOR (n:Decision) ON (n.id)",
    "CREATE INDEX FOR (n:Session) ON (n.id)",
    "CREATE INDEX FOR (n:Task) ON (n.id)",
    "CREATE INDEX FOR (n:Lesson) ON (n.id)",
    "CREATE INDEX FOR (n:Alert) ON (n.id)",
    "CREATE INDEX FOR (n:Page) ON (n.id)",
    "CREATE INDEX FOR (n:Agent) ON (n.id)",
    "CREATE INDEX FOR (n:Counter) ON (n.id)",
    "CREATE INDEX FOR (n:Change) ON (n.id)",
    "CREATE INDEX FOR (n:Project) ON (n.id)",
]
# The database itself guarantees every id is unique (each needs the exact-match index above).
UNIQUE_IDS = ["Service", "Person", "Team", "Incident", "Customer", "Product", "Runbook", "Source", "Decision",
              "Session", "Task", "Lesson", "Alert", "Page", "Agent", "Counter", "Change", "Project"]
VECTOR = [  # hybrid retrieval: similar incidents by meaning-ish (see embed.py), alongside graph proximity
    "CREATE VECTOR INDEX FOR (i:Incident) ON (i.embedding) OPTIONS {dimension: 256, similarityFunction: 'cosine'}",
]
FULLTEXT = [
    "CALL db.idx.fulltext.createNodeIndex('Incident', 'title', 'summary')",
    "CALL db.idx.fulltext.createNodeIndex('Source', 'title', 'text')",
    "CALL db.idx.fulltext.createNodeIndex('Lesson', 'text')",
]


def _people_rows(people):
    out = []
    for pid, name, role, history in people:
        for team, frm, to in history:
            out.append({"id": pid, "name": name, "role": role, "team": team, "from": frm, "to": to})  # None = current team
    return out


def load_core(g, teams, people, on_call, services, depends_on):
    g.query("UNWIND $rows AS r MERGE (t:Team {id: r.id}) SET t.name = r.name, t.slack = r.slack", {"rows": teams})
    g.query(
        """UNWIND $rows AS r
           MERGE (p:Person {id: r.id}) SET p.name = r.name, p.role = r.role
           WITH p, r MATCH (t:Team {id: r.team})
           CREATE (p)-[:MEMBER_OF {from: date(r.from), to: date(r.to)}]->(t)""",
        {"rows": _people_rows(people)},
    )
    g.query(
        """UNWIND $rows AS r MATCH (p:Person {id: r.person}), (t:Team {id: r.team})
           CREATE (p)-[:ON_CALL_FOR]->(t)""",
        {"rows": [{"team": t, "person": p} for t, p in on_call.items()]},
    )
    g.query(
        """UNWIND $rows AS r
           MERGE (s:Service {id: r.id})
           SET s.name = r.name, s.tier = r.tier, s.kind = r.kind, s.description = r.desc, s.plain = r.plain,
               s.status = 'healthy'
           WITH s, r MATCH (t:Team {id: r.team}) CREATE (t)-[:OWNS]->(s)""",
        {"rows": [{"id": i, "name": n, "tier": t, "team": tm, "kind": k, "desc": d, "plain": D.PLAIN.get(i, d)}
                  for i, n, t, tm, k, d in services]},
    )
    g.query(
        """UNWIND $rows AS r MATCH (a:Service {id: r.a}), (b:Service {id: r.b})
           CREATE (a)-[:DEPENDS_ON]->(b)""",
        {"rows": [{"a": a, "b": b} for a, b in depends_on]},
    )


def load_company(g):
    load_core(g, D.TEAMS, D.PEOPLE, D.ON_CALL, D.SERVICES, D.DEPENDS_ON)

    g.query(
        """UNWIND $rows AS r MERGE (p:Product {id: r.id}) SET p.name = r.name
           WITH p, r UNWIND r.services AS sid MATCH (s:Service {id: sid}) CREATE (s)-[:POWERS]->(p)""",
        {"rows": [{"id": i, "name": n, "services": s} for i, n, s in D.PRODUCTS]},
    )
    g.query(
        """UNWIND $rows AS r MERGE (c:Customer {id: r.id}) SET c.name = r.name, c.arr = r.arr, c.sla = r.sla
           WITH c, r UNWIND r.products AS pid MATCH (p:Product {id: pid}) CREATE (c)-[:USES]->(p)""",
        {"rows": [{"id": i, "name": n, "arr": a, "sla": s, "products": p} for i, n, a, s, p in D.CUSTOMERS]},
    )
    g.query(
        """UNWIND $rows AS r MATCH (p:Person {id: r.p}), (s:Service {id: r.s})
           CREATE (p)-[:CONTRIBUTED_TO {commits: r.c, last_at: date(r.at)}]->(s)""",
        {"rows": [{"p": p, "s": s, "c": c, "at": at} for p, s, c, at in D.CONTRIBUTED_TO]},
    )
    g.query(
        """UNWIND $rows AS r MERGE (b:Runbook {id: r.id}) SET b.title = r.title, b.steps = r.steps
           WITH b, r MATCH (s:Service {id: r.service}) CREATE (b)-[:FOR]->(s)""",
        {"rows": D.RUNBOOKS},
    )
    g.query(
        """UNWIND $rows AS r
           MERGE (i:Incident {id: r.id})
           SET i.title = r.title, i.summary = r.summary, i.date = date(r.date), i.sev = r.sev,
               i.embedding = vecf32(r.embedding),
               i.duration_min = r.duration_min, i.status = 'resolved'
           WITH i, r MATCH (root:Service {id: r.root}) CREATE (i)-[:ROOT_CAUSE]->(root)
           WITH i, r UNWIND r.affected AS sid MATCH (s:Service {id: sid}) CREATE (i)-[:AFFECTED]->(s)""",
        {"rows": [{**i, "embedding": embed(f"{i['title']}. {i['summary']}")} for i in D.INCIDENTS]},
    )
    for inc in D.INCIDENTS:
        g.query(
            """MATCH (i:Incident {id: $id})
               UNWIND $people AS pid MATCH (p:Person {id: pid}) CREATE (p)-[:RESOLVED]->(i)""",
            {"id": inc["id"], "people": inc["resolved_by"]},
        )
        g.query(
            """MATCH (i:Incident {id: $id})
               UNWIND $customers AS cid MATCH (c:Customer {id: cid}) CREATE (i)-[:IMPACTED]->(c)""",
            {"id": inc["id"], "customers": inc["customers"]},
        )
        if inc["runbook"]:
            g.query(
                """MATCH (i:Incident {id: $id}), (b:Runbook {id: $rb}) CREATE (i)-[:FIXED_WITH]->(b)""",
                {"id": inc["id"], "rb": inc["runbook"]},
            )

    # Sources: docs, messages, tickets all carry :Source so provenance queries are uniform.
    g.query(
        """UNWIND $rows AS r
           CREATE (d:Source:Doc {id: r.id, kind: r.kind, title: r.title, date: date(r.date), text: r.text})
           WITH d, r MATCH (p:Person {id: r.author}) CREATE (p)-[:AUTHORED]->(d)
           WITH d, r UNWIND r.about AS sid MATCH (s:Service {id: sid}) CREATE (d)-[:ABOUT]->(s)""",
        {"rows": D.DOCS},
    )
    g.query(
        """UNWIND $rows AS r MATCH (d:Doc {id: r.id}), (i:Incident {id: r.incident})
           CREATE (d)-[:DOCUMENTS]->(i)""",
        {"rows": [d for d in D.DOCS if d["incident"]]},
    )
    g.query(
        """UNWIND $rows AS r
           CREATE (m:Source:Message {id: r.id, kind: 'chat', channel: r.channel, date: localdatetime(r.ts), title: r.channel, text: r.text})
           WITH m, r MATCH (p:Person {id: r.author}) CREATE (p)-[:POSTED]->(m)
           WITH m, r UNWIND r.services AS sid MATCH (s:Service {id: sid}) CREATE (m)-[:MENTIONS]->(s)""",
        {"rows": [{"id": i, "channel": c, "ts": t, "author": a, "text": x, "services": s}
                  for i, c, t, a, x, s, _ in D.MESSAGES]},
    )
    g.query(
        """UNWIND $rows AS r MATCH (m:Message {id: r.id})
           UNWIND r.incidents AS iid MATCH (i:Incident {id: iid}) CREATE (m)-[:MENTIONS]->(i)""",
        {"rows": [{"id": i, "incidents": inc} for i, *_, inc in D.MESSAGES if inc]},
    )
    g.query(
        """UNWIND $rows AS r
           CREATE (t:Source:Ticket {id: r.id, kind: 'ticket', title: r.title, text: r.title, status: r.status})
           WITH t, r MATCH (p:Person {id: r.assignee}), (s:Service {id: r.service}), (i:Incident {id: r.inc})
           CREATE (t)-[:ASSIGNED_TO]->(p), (t)-[:ABOUT]->(s), (t)-[:FOLLOW_UP_OF]->(i)""",
        {"rows": [{"id": i, "title": t, "status": st, "assignee": a, "service": s, "inc": inc}
                  for i, t, st, a, s, inc in D.TICKETS]},
    )
    g.query(
        """UNWIND $rows AS r
           CREATE (d:Decision {id: r.id, title: r.title, date: date(r.date), status: r.status})
           WITH d, r MATCH (p:Person {id: r.by}) CREATE (p)-[:MADE]->(d)
           WITH d, r UNWIND r.affects AS sid MATCH (s:Service {id: sid}) CREATE (d)-[:AFFECTS]->(s)""",
        {"rows": [{"id": i, "title": t, "date": dt, "status": st, "by": b, "affects": af}
                  for i, t, dt, st, b, af, _ in D.DECISIONS]},
    )
    g.query(
        """UNWIND $rows AS r MATCH (d:Decision {id: r.id})
           UNWIND r.sources AS src MATCH (s:Source {id: src}) CREATE (d)-[:DERIVED_FROM]->(s)""",
        {"rows": [{"id": i, "sources": src} for i, *_, src in D.DECISIONS]},
    )
    g.query(
        """UNWIND $rows AS r
           CREATE (pr:Project {id: r.id, name: r.name, status: r.status, goal: r.goal, due: date(r.due)})
           WITH pr, r MATCH (t:Team {id: r.team}), (l:Person {id: r.lead})
           CREATE (t)-[:OWNS]->(pr), (l)-[:LEADS]->(pr)
           WITH pr, r UNWIND r.members AS m MATCH (p:Person {id: m}) CREATE (p)-[:WORKS_ON]->(pr)""",
        {"rows": D.PROJECTS},
    )
    for rel, key, label, direction in [("TOUCHES", "touches", "Service", "out"), ("IMPLEMENTS", "implements", "Decision", "out"),
                                       ("DOCUMENTED_IN", "docs", "Doc", "out"), ("FOLLOWS_UP", "follows", "Incident", "out"),
                                       ("PART_OF", "tickets", "Ticket", "in")]:
        edge = f"(pr)-[:{rel}]->(x)" if direction == "out" else f"(x)-[:{rel}]->(pr)"
        g.query(f"""UNWIND $rows AS r MATCH (pr:Project {{id: r.id}})
                    UNWIND r.ids AS xid MATCH (x:{label} {{id: xid}}) CREATE {edge}""",
                {"rows": [{"id": p["id"], "ids": p[key]} for p in D.PROJECTS]})


def load_agents(g):
    g.query(
        """UNWIND $rows AS r MERGE (a:Agent {id: r.id}) SET a.name = r.name, a.role = r.role""",
        {"rows": [
            {"id": "commander", "name": "Commander", "role": "Plans the response and assigns tasks"},
            {"id": "investigator", "name": "Investigator", "role": "Blast radius, root cause, who to page"},
            {"id": "historian", "name": "Historian", "role": "Recalls past incidents and lessons; writes memory"},
        ]},
    )


def reset(tenant: str, keep_memory: bool = False):
    g = graph(tenant)
    if tenant in client().list_graphs():
        if keep_memory:
            g.query("MATCH (n) WHERE NOT (n:Session OR n:Episode OR n:Task OR n:Lesson OR n:Agent) DETACH DELETE n")
        else:
            g.delete()
            g = graph(tenant)
    for q in INDEXES:
        try:
            g.query(q)
        except Exception:
            pass  # index already exists
    for q in FULLTEXT + VECTOR:
        try:
            g.query(q)
        except Exception:
            pass
    for label in UNIQUE_IDS:
        try:
            g.create_node_unique_constraint(label, "id")
        except Exception:
            pass  # constraint already exists
    return g


def main():
    keep = "--keep-memory" in sys.argv
    g = reset("ravenhold", keep)
    load_company(g)
    load_agents(g)
    g2 = reset("ravenhold-labs", keep)
    G = D.LABS
    load_core(g2, G["teams"], G["people"], G["on_call"], G["services"], G["depends_on"])
    load_agents(g2)
    for t in ("ravenhold", "ravenhold-labs"):
        r = graph(t).query("MATCH (n) RETURN count(n)").result_set[0][0]
        e = graph(t).query("MATCH ()-[e]->() RETURN count(e)").result_set[0][0]
        print(f"{t}: {r} nodes, {e} edges")


if __name__ == "__main__":
    main()
