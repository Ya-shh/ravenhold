"""Graph tools the agents call. Every tool returns:

    {"result": <json for the model>, "cypher": [queries run], "subgraph": {"nodes": [...], "edges": [...]}}

The subgraph is what the UI highlights, so every answer shows the path behind it.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from falkordb import Edge, Node, Path

from .db import graph, rows
from .embed import embed

READ_LIMIT = 100
QUERY_TIMEOUT_MS = 3000  # model-written Cypher can't hog the FalkorDB instance every sandbox shares


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Subgraph collection

class Trace:
    """Collects the Cypher run and the nodes/edges touched by one tool call."""

    def __init__(self, tenant: str):
        self.g = graph(tenant)
        self.cypher: list[str] = []
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}

    def q(self, cypher: str, params: dict | None = None, write: bool = False, timeout: int | None = None):
        self.cypher.append(" ".join(cypher.split()))
        res = (self.g.query(cypher, params or {}, timeout=timeout) if write
               else self.g.ro_query(cypher, params or {}, timeout=timeout))
        self._walk(res.result_set)
        return res

    def rows(self, cypher: str, params: dict | None = None, write: bool = False) -> list[dict]:
        return rows(self.q(cypher, params, write))

    # FalkorDB returns Node / Edge / Path objects; we walk them to build the highlight subgraph.
    def _node(self, n: Node) -> str:
        props = n.properties or {}
        key = str(props.get("id", f"n{n.id}"))
        labels = [l for l in (n.labels or []) if l != "Source"] or (n.labels or ["Node"])
        if key not in self.nodes:
            self.nodes[key] = {
                "id": key,
                "label": labels[0],
                "name": props.get("name") or props.get("title") or key,
            }
        return key

    def _walk(self, value, idmap: dict | None = None):
        if isinstance(value, list):
            for v in value:
                self._walk(v, idmap)
        elif isinstance(value, Node):
            self._node(value)
        elif isinstance(value, Path):
            local = {n.id: self._node(n) for n in value.nodes()}
            for e in value.edges():
                self._edge(e, local)

    def _edge(self, e: Edge, idmap: dict):
        src = e.src_node.id if isinstance(e.src_node, Node) else e.src_node
        dst = e.dest_node.id if isinstance(e.dest_node, Node) else e.dest_node
        if src in idmap and dst in idmap:
            k = (idmap[src], e.relation, idmap[dst])
            self.edges[k] = {"source": k[0], "type": k[1], "target": k[2]}

    def link(self, src: str, rel: str, dst: str, src_label="Node", dst_label="Node", src_name=None, dst_name=None):
        """Add an edge we know about without asking FalkorDB for a Path object."""
        for key, label, name in ((src, src_label, src_name), (dst, dst_label, dst_name)):
            if key not in self.nodes:
                self.nodes[key] = {"id": key, "label": label, "name": name or key}
        self.edges[(src, rel, dst)] = {"source": src, "type": rel, "target": dst}

    def out(self, result) -> dict:
        return {
            "result": result,
            "cypher": self.cypher,
            "subgraph": {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())},
        }


# ---------------------------------------------------------------------------
# Read tools: Track 1 (act on connected data) + Track 3 (company brain)

def blast_radius(tenant: str, service_id: str, max_hops: int = 6) -> dict:
    """Everything that breaks if `service_id` goes down: dependent services, products, customers."""
    t = Trace(tenant)
    max_hops = max(1, min(int(max_hops), 8))
    exists = t.rows("MATCH (s:Service {id: $id}) RETURN s.id AS id, s.name AS name, s.tier AS tier", {"id": service_id})
    if not exists:
        return t.out({"error": f"unknown service '{service_id}'"})
    t.q(f"MATCH p = (:Service)-[:DEPENDS_ON*1..{max_hops}]->(:Service {{id: $id}}) RETURN p", {"id": service_id})
    dependents = t.rows(
        f"""MATCH p = (d:Service)-[:DEPENDS_ON*1..{max_hops}]->(:Service {{id: $id}})
            RETURN d.id AS id, d.name AS name, d.tier AS tier, min(length(p)) AS hops
            ORDER BY hops, tier""",
        {"id": service_id},
    )
    t.q(
        f"""MATCH (s:Service {{id: $id}})
            OPTIONAL MATCH (d:Service)-[:DEPENDS_ON*1..{max_hops}]->(s)
            WITH s, collect(DISTINCT d) AS deps WITH deps + [s] AS hit
            UNWIND hit AS x
            MATCH p = (x)-[:POWERS]->(:Product)<-[:USES]-(:Customer)
            RETURN p""",
        {"id": service_id},
    )
    products = t.rows(
        f"""MATCH (s:Service {{id: $id}})
            OPTIONAL MATCH (d:Service)-[:DEPENDS_ON*1..{max_hops}]->(s)
            WITH s, collect(DISTINCT d) AS deps WITH deps + [s] AS hit
            UNWIND hit AS x
            MATCH (x)-[:POWERS]->(prod:Product)
            OPTIONAL MATCH (c:Customer)-[:USES]->(prod)
            RETURN prod.id AS product, prod.name AS name, collect(DISTINCT x.id) AS via,
                   collect(DISTINCT c.id) AS customers""",
        {"id": service_id},
    )
    cust_ids = sorted({c for p in products for c in p["customers"]})
    customers = t.rows(
        "MATCH (c:Customer) WHERE c.id IN $ids RETURN c.id AS id, c.name AS name, c.arr AS arr, c.sla AS sla ORDER BY c.arr DESC",
        {"ids": cust_ids},
    )
    return t.out({
        "service": exists[0],
        "dependent_services": dependents,
        "products": products,
        "customers": customers,
        "arr_at_risk_usd": sum(c["arr"] for c in customers),
        "tier0_dependents": [d["id"] for d in dependents if d["tier"] == 0],
    })


def root_cause_candidates(tenant: str, service_id: str) -> dict:
    """Walk DOWN the dependency graph from a symptomatic service: what could be causing it?
    Ranked by how often each dependency has been a past root cause, and by PageRank."""
    t = Trace(tenant)
    t.q("MATCH p = (:Service {id: $id})-[:DEPENDS_ON*1..4]->(:Service) RETURN p", {"id": service_id})
    cands = t.rows(
        """MATCH p = (:Service {id: $id})-[:DEPENDS_ON*1..4]->(d:Service)
           WITH d, min(length(p)) AS hops
           OPTIONAL MATCH (i:Incident)-[:ROOT_CAUSE]->(d)
           RETURN d.id AS id, d.name AS name, d.kind AS kind, d.status AS status, hops,
                  count(DISTINCT i) AS past_root_causes, collect(DISTINCT i.id) AS incidents
           ORDER BY past_root_causes DESC, hops""",
        {"id": service_id},
    )
    pr = {r["id"]: r["score"] for r in t.rows(
        "CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score RETURN node.id AS id, score")}
    for c in cands:
        c["pagerank"] = round(pr.get(c["id"], 0), 4)
    cands.sort(key=lambda c: (c["status"] != "healthy", c["past_root_causes"], c["pagerank"]), reverse=True)
    return t.out({"symptom": service_id, "candidates": cands[:10]})


def find_experts(tenant: str, service_id: str) -> dict:
    """Who actually knows this system: resolved incidents on it, commits, docs written.
    Compared against the official owner team and who is on call right now."""
    t = Trace(tenant)
    experts = t.rows(
        """MATCH (s:Service {id: $id}), (p:Person)
           OPTIONAL MATCH (p)-[c:CONTRIBUTED_TO]->(s)
           OPTIONAL MATCH (p)-[:RESOLVED]->(i:Incident)-[:ROOT_CAUSE|AFFECTED]->(s)
           OPTIONAL MATCH (p)-[:AUTHORED]->(d:Doc)-[:ABOUT]->(s)
           WITH p, coalesce(max(c.commits), 0) AS commits, max(c.last_at) AS last_commit,
                collect(DISTINCT i.id) AS incidents, collect(DISTINCT d.id) AS docs
           WHERE commits > 0 OR size(incidents) > 0 OR size(docs) > 0
           OPTIONAL MATCH (p)-[m:MEMBER_OF]->(team:Team) WHERE m.to IS NULL
           OPTIONAL MATCH (p)-[:ON_CALL_FOR]->(oc:Team)
           WITH p, team, oc, commits, last_commit, incidents, docs,
                last_commit IS NOT NULL AND last_commit >= date() - duration('P180D') AS active_last_6_months
           RETURN p.id AS id, p.name AS name, p.role AS role, team.name AS current_team,
                  oc.name AS on_call_for, commits, toString(last_commit) AS last_commit, active_last_6_months,
                  incidents, docs,
                  size(incidents) * 30 + commits / 5 + size(docs) * 10
                    + CASE WHEN active_last_6_months THEN 10 ELSE 0 END AS score
           ORDER BY score DESC LIMIT 5""",
        {"id": service_id},
    )
    for e in experts:
        t.q("MATCH p = (:Person {id: $p})-[:RESOLVED]->(:Incident)-[:ROOT_CAUSE|AFFECTED]->(:Service {id: $s}) RETURN p",
            {"p": e["id"], "s": service_id})
        t.q("MATCH p = (:Person {id: $p})-[:CONTRIBUTED_TO]->(:Service {id: $s}) RETURN p", {"p": e["id"], "s": service_id})
    owner = t.rows(
        """MATCH (team:Team)-[:OWNS]->(s:Service {id: $id})
           OPTIONAL MATCH (oc:Person)-[:ON_CALL_FOR]->(team)
           OPTIONAL MATCH (oc)-[c:CONTRIBUTED_TO]->(s)
           OPTIONAL MATCH (oc)-[:RESOLVED]->(i:Incident)-[:ROOT_CAUSE|AFFECTED]->(s)
           RETURN team.id AS team_id, team.name AS team, team.slack AS slack, oc.id AS on_call_id, oc.name AS on_call,
                  coalesce(c.commits, 0) AS on_call_commits, count(DISTINCT i) AS on_call_incidents_resolved""",
        {"id": service_id},
    )
    t.q("MATCH p = (:Person)-[:ON_CALL_FOR]->(:Team)-[:OWNS]->(:Service {id: $id}) RETURN p", {"id": service_id})
    # Team history explains *why* an expert is no longer on the owning team.
    moves = t.rows(
        """MATCH (p:Person)-[m:MEMBER_OF]->(team:Team) WHERE p.id IN $ids AND m.to IS NOT NULL
           RETURN p.id AS person, team.name AS former_team, toString(m.from) AS from, toString(m.to) AS to""",
        {"ids": [e["id"] for e in experts]},
    )
    from datetime import date as _date
    for m in moves:  # days since they left, computed here (FalkorDB has no duration.between)
        try:
            m["days_since_move"] = (_date.today() - _date.fromisoformat(m["to"])).days
        except (TypeError, ValueError):  # a malformed end date shouldn't sink the whole expert lookup
            m["days_since_move"] = None
    return t.out({"owner": owner[0] if owner else None, "experts": experts, "team_moves": moves})


def similar_incidents(tenant: str, service_id: str, description: str = "") -> dict:
    """Past incidents near this service in the graph (same service or <=2 dependency hops),
    boosted by fulltext match on the description. Includes what fixed them and lessons learned."""
    t = Trace(tenant)
    near = t.rows(
        """MATCH (s:Service {id: $id})
           OPTIONAL MATCH (s)-[:DEPENDS_ON*1..2]-(n:Service)
           WITH s, collect(DISTINCT n) AS ns WITH ns + [s] AS hood
           UNWIND hood AS x
           MATCH (i:Incident)-[:ROOT_CAUSE|AFFECTED]->(x) WHERE i.status = 'resolved'
           RETURN i.id AS id, max(CASE WHEN x.id = $id THEN 2 ELSE 1 END) AS proximity""",
        {"id": service_id},
    )
    score = {r["id"]: float(r["proximity"]) for r in near}
    via: dict[str, set] = {r["id"]: {"graph"} for r in near}
    if description.strip():
        terms = " ".join(w for w in "".join(ch if ch.isalnum() or ch in " -" else " " for ch in description).split() if len(w) > 2)
        if terms:
            try:
                for r in t.rows(
                    "CALL db.idx.fulltext.queryNodes('Incident', $q) YIELD node, score "
                    "WHERE node.status = 'resolved' RETURN node.id AS id, score",
                    {"q": " | ".join(terms.split())},
                ):
                    if r["id"]:
                        score[r["id"]] = score.get(r["id"], 0) + min(float(r["score"]), 5.0)
                        via.setdefault(r["id"], set()).add("text")
            except Exception:
                pass
        # Vector index: nearest incidents by embedding (cosine distance), so different wording still matches.
        try:
            for r in t.rows(
                "CALL db.idx.vector.queryNodes('Incident', 'embedding', 6, vecf32($v)) YIELD node, score "
                "WHERE node.status = 'resolved' RETURN node.id AS id, score",
                {"v": embed(description)},
            ):
                sim = max(0.0, 1.0 - float(r["score"]))
                if sim > 0.05:
                    score[r["id"]] = score.get(r["id"], 0) + sim * 3
                    via.setdefault(r["id"], set()).add("vector")
        except Exception:
            pass
    top = sorted(score, key=score.get, reverse=True)[:5]
    details = t.rows(
        """UNWIND $ids AS iid
           MATCH (i:Incident {id: iid})
           OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(root:Service)
           OPTIONAL MATCH (p:Person)-[:RESOLVED]->(i)
           OPTIONAL MATCH (i)-[:FIXED_WITH]->(rb:Runbook)
           OPTIONAL MATCH (d:Doc)-[:DOCUMENTS]->(i)
           OPTIONAL MATCH (tk:Ticket)-[:FOLLOW_UP_OF]->(i)
           OPTIONAL MATCH (l:Lesson)-[:ABOUT_INCIDENT]->(i)
           RETURN i.id AS id, i.title AS title, i.date AS date, i.sev AS sev, i.status AS status,
                  i.summary AS summary, root.id AS root_cause,
                  collect(DISTINCT p.name) AS resolved_by,
                  collect(DISTINCT rb.id) AS runbooks,
                  collect(DISTINCT d.id + ': ' + d.text) AS postmortems,
                  collect(DISTINCT tk.id + ' [' + tk.status + '] ' + tk.title) AS follow_ups,
                  collect(DISTINCT l.text) AS lessons""",
        {"ids": top},
    )
    for d in details:
        d["matched_by"] = sorted(via.get(d["id"], set()))  # graph proximity, full-text, vector
        d["date"] = str(d["date"]) if d.get("date") else None
        # Severity breaks ties: a SEV1 on the same path is more instructive than a SEV3.
        d["similarity"] = round(score[d["id"]] + (4 - (d["sev"] or 3)) * 0.5, 2)
        t.q("""MATCH (i:Incident {id: $id})
               OPTIONAL MATCH p1 = (i)-[:ROOT_CAUSE]->(:Service)
               OPTIONAL MATCH p2 = (:Person)-[:RESOLVED]->(i)
               OPTIONAL MATCH p3 = (i)-[:FIXED_WITH]->(:Runbook)
               OPTIONAL MATCH p4 = (:Doc)-[:DOCUMENTS]->(i)
               RETURN collect(DISTINCT p1) + collect(DISTINCT p2) + collect(DISTINCT p3) + collect(DISTINCT p4)""",
            {"id": d["id"]})
    details.sort(key=lambda d: d["similarity"], reverse=True)
    return t.out({"incidents": details})


def latest_runbook_id(t: "Trace", runbook_id: str) -> str | None:
    """Follow SUPERSEDES edges to the newest APPROVED version of a runbook. Proposed versions (from a correction)
    are ignored until a human approves them."""
    res = t.rows(
        """MATCH (b:Runbook {id: $id})
           OPTIONAL MATCH p = (n:Runbook)-[:SUPERSEDES*1..10]->(b)
           WHERE all(x IN nodes(p) WHERE coalesce(x.status, 'active') = 'active')
           RETURN b.id AS base, n.id AS newer, length(p) AS depth ORDER BY depth DESC LIMIT 1""",
        {"id": runbook_id},
    )
    if not res:
        return None
    return res[0]["newer"] or res[0]["base"]


def get_runbook(tenant: str, runbook_id: str) -> dict:
    """The LATEST version of a runbook (even if asked for an older one), its version history with the corrections
    that produced each version, and its track record."""
    t = Trace(tenant)
    latest = latest_runbook_id(t, runbook_id)
    if not latest:
        return t.out({"error": f"unknown runbook '{runbook_id}'"})
    rb = t.rows(
        """MATCH (b:Runbook {id: $id})-[:FOR]->(s:Service)
           RETURN b.id AS id, b.title AS title, b.steps AS steps, coalesce(b.version, 1) AS version, s.id AS service""",
        {"id": latest},
    )[0]
    history = t.rows(
        """MATCH (latest:Runbook {id: $id})-[:SUPERSEDES*0..10]->(v:Runbook)
           OPTIONAL MATCH (l:Lesson)-[:PRODUCED]->(v)
           OPTIONAL MATCH (l)-[:LEARNED_IN]->(sess:Session)
           OPTIONAL MATCH (i:Incident)-[:FIXED_WITH]->(v)
           RETURN v.id AS id, coalesce(v.version, 1) AS version, v.created_at AS created_at,
                  l.text AS changed_because, sess.id AS session, collect(DISTINCT i.id) AS used_in
           ORDER BY version DESC""",
        {"id": latest},
    )
    t.q("MATCH p = (:Runbook {id: $id})-[:SUPERSEDES*0..10]->(:Runbook)-[:FOR]->(:Service) RETURN p", {"id": latest})
    t.q("MATCH p = (:Lesson)-[:PRODUCED|REFINES]->(:Runbook)<-[:SUPERSEDES*0..10]-(:Runbook {id: $id}) RETURN p",
        {"id": latest})
    rb["requested"] = runbook_id
    rb["pending_proposals"] = t.rows(
        """MATCH (p:Runbook {status: 'proposed'})-[:SUPERSEDES]->(:Runbook {id: $id})
           OPTIONAL MATCH (l:Lesson)-[:PRODUCED]->(p)
           RETURN p.id AS id, p.steps AS steps, l.text AS because""", {"id": latest})
    if latest != runbook_id:
        rb["note"] = f"{runbook_id} has been superseded; this is the current version {latest}."
    rb["history"] = history
    rb["times_used"] = sum(len(h["used_in"]) for h in history)
    return t.out(rb)


def open_follow_ups(tenant: str, service_id: str) -> dict:
    """Unfinished action items from past incidents on this service or its neighbours."""
    t = Trace(tenant)
    res = t.rows(
        """MATCH (s:Service {id: $id})
           OPTIONAL MATCH (s)-[:DEPENDS_ON*1..2]-(n:Service)
           WITH s, collect(DISTINCT n) AS ns WITH ns + [s] AS hood
           UNWIND hood AS x
           MATCH (tk:Ticket)-[:ABOUT]->(x) WHERE tk.status <> 'done'
           OPTIONAL MATCH (tk)-[:FOLLOW_UP_OF]->(i:Incident)
           OPTIONAL MATCH (tk)-[:ASSIGNED_TO]->(p:Person)
           RETURN DISTINCT tk.id AS id, tk.title AS title, tk.status AS status, x.id AS service,
                  i.id AS from_incident, i.date AS incident_date, p.name AS assignee""",
        {"id": service_id},
    )
    for r in res:
        t.q("MATCH p = (:Incident {id: $i})<-[:FOLLOW_UP_OF]-(:Ticket {id: $t})-[:ASSIGNED_TO]->(:Person) RETURN p",
            {"i": r["from_incident"], "t": r["id"]})
    return t.out({"open_follow_ups": res})


def trace_decisions(tenant: str, service_id: str) -> dict:
    """Decisions that affect this service, who made them, and the sources they came from."""
    t = Trace(tenant)
    res = t.rows(
        """MATCH (d:Decision)-[:AFFECTS]->(:Service {id: $id})
           OPTIONAL MATCH (p:Person)-[:MADE]->(d)
           OPTIONAL MATCH (d)-[:DERIVED_FROM]->(src:Source)
           OPTIONAL MATCH (author:Person)-[:AUTHORED|POSTED]->(src)
           RETURN d.id AS id, d.title AS title, d.date AS date, d.status AS status, p.name AS made_by,
                  collect(DISTINCT {id: src.id, kind: src.kind, title: src.title, by: author.name,
                                    excerpt: substring(src.text, 0, 160)}) AS sources
           ORDER BY d.date DESC""",
        {"id": service_id},
    )
    t.q("""MATCH p = (:Person)-[:MADE]->(:Decision)-[:AFFECTS]->(:Service {id: $id}) RETURN p""", {"id": service_id})
    t.q("""MATCH p = (:Source)<-[:DERIVED_FROM]-(:Decision)-[:AFFECTS]->(:Service {id: $id}) RETURN p""", {"id": service_id})
    return t.out({"decisions": res})


def search_knowledge(tenant: str, query: str) -> dict:
    """Fulltext search over docs, chat messages, tickets and learned lessons."""
    t = Trace(tenant)
    out = []
    for index in ("Source", "Lesson"):
        try:
            out += t.rows(
                f"""CALL db.idx.fulltext.queryNodes('{index}', $q) YIELD node, score
                    RETURN node.id AS id, labels(node) AS labels, node.title AS title,
                           substring(node.text, 0, 240) AS text, score
                    ORDER BY score DESC LIMIT 8""",
                {"q": query},
            )
        except Exception as e:
            out.append({"error": str(e)})
    out.sort(key=lambda r: r.get("score", 0), reverse=True)
    for r in out[:8]:
        if r.get("id"):
            t.q("MATCH p = (n {id: $id})-[:ABOUT|MENTIONS|APPLIES_TO|DOCUMENTS]->() RETURN p LIMIT 5", {"id": r["id"]})
    return t.out({"matches": out[:8]})


def critical_services(tenant: str) -> dict:
    """Graph algorithms as a tool: PageRank (most depended-on), betweenness (chokepoints), harmonic centrality
    (services that reach the most of the system), label propagation (failure domains: clusters of services that
    depend on each other), WCC (isolated services)."""
    t = Trace(tenant)
    pr = t.rows("CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score "
                "RETURN node.id AS id, node.tier AS tier, score ORDER BY score DESC LIMIT 8")
    bt = t.rows("CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['DEPENDS_ON']}) YIELD node, score "
                "RETURN node.id AS id, score ORDER BY score DESC LIMIT 8")
    wcc = t.rows("CALL algo.WCC({nodeLabels: ['Service'], relationshipTypes: ['DEPENDS_ON']}) YIELD node, componentId "
                 "RETURN componentId, collect(node.id) AS services, count(*) AS size ORDER BY size")
    hc = t.rows("CALL algo.HarmonicCentrality({nodeLabels: ['Service'], relationshipTypes: ['DEPENDS_ON']}) "
                "YIELD node, score RETURN node.id AS id, score ORDER BY score DESC LIMIT 5")
    cdlp = t.rows("CALL algo.labelPropagation({nodeLabels: ['Service'], relationshipTypes: ['DEPENDS_ON']}) "
                  "YIELD node, communityId RETURN communityId, collect(node.id) AS services, count(*) AS size "
                  "ORDER BY size DESC")
    for r in pr:
        r["score"] = round(r["score"], 4)
    for r in bt:
        r["score"] = round(r["score"], 1)
    for r in hc:
        r["score"] = round(r["score"], 2)
    # show the direct dependents of the top-ranked services on the map
    t.q("MATCH p = (:Service)-[:DEPENDS_ON]->(s:Service) WHERE s.id IN $ids RETURN p",
        {"ids": list({r["id"] for r in pr[:3] + bt[:3]})})
    return t.out({
        "most_depended_on_pagerank": pr,
        "chokepoints_betweenness": bt,
        "widest_reach_harmonic": hc,
        "failure_domains_label_propagation": [{"size": c["size"], "services": c["services"]} for c in cdlp if c["size"] > 1],
        "isolated_components": [c for c in wcc if c["size"] < 3],
    })


def shortest_connection(tenant: str, from_id: str, to_id: str) -> dict:
    """How are two things connected? Shortest path between any two nodes by id."""
    t = Trace(tenant)
    res = t.q(
        """MATCH (a {id: $a}), (b {id: $b})
           WITH a, b WHERE a <> b
           RETURN shortestPath((a)-[*..6]->(b)) AS p""",
        {"a": from_id, "b": to_id},
    )
    path = res.result_set[0][0] if res.result_set else None
    if path is None:
        res = t.q(
            """MATCH (a {id: $a}), (b {id: $b})
               WITH a, b WHERE a <> b
               RETURN shortestPath((b)-[*..6]->(a)) AS p""",
            {"a": from_id, "b": to_id},
        )
        path = res.result_set[0][0] if res.result_set else None
    if path is None:
        return t.out({"path": None, "note": "no directed path within 6 hops"})
    steps = []
    nodes = path.nodes()
    for i, e in enumerate(path.edges()):
        a, b = nodes[i], nodes[i + 1]
        steps.append(f"{a.properties.get('id')} -[{e.relation}]-> {b.properties.get('id')}")
    return t.out({"path": steps, "hops": len(steps)})


def graph_query(tenant: str, cypher: str) -> dict:
    """Escape hatch: run a READ-ONLY Cypher query for questions no other tool answers."""
    t = Trace(tenant)
    lowered = cypher.lower()
    if any(w in lowered for w in ("create", "merge", "delete", "set ", "remove", "drop", "call db.idx.fulltext.create")):
        return t.out({"error": "graph_query is read-only"})
    if "limit" not in lowered:
        cypher = cypher.rstrip().rstrip(";") + f" LIMIT {READ_LIMIT}"
    try:
        res = t.q(cypher, timeout=QUERY_TIMEOUT_MS)
        data = []
        for r in rows(res):
            data.append({k: _plain(v) for k, v in r.items()})
        return t.out({"rows": data[:50]})
    except Exception as e:
        return t.out({"error": str(e)})


def _plain(v):
    if isinstance(v, Node):
        return {"labels": v.labels, **(v.properties or {})}
    if isinstance(v, Edge):
        return {"type": v.relation, **(v.properties or {})}
    if isinstance(v, Path):
        return [n.properties.get("id") for n in v.nodes()]
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


# ---------------------------------------------------------------------------
# Memory tools: Track 2 (memory that persists across sessions)

def recall_memory(tenant: str, service_ids: list[str], session_id: str = "") -> dict:
    """Lessons learned in earlier sessions (incl. human corrections) for these services
    and their direct neighbours, newest and most reinforced first."""
    t = Trace(tenant)
    res = t.rows(
        """MATCH (s:Service) WHERE s.id IN $ids
           OPTIONAL MATCH (s)-[:DEPENDS_ON]-(n:Service)
           WITH collect(DISTINCT s) + collect(DISTINCT n) AS hood
           UNWIND hood AS x
           MATCH (l:Lesson)-[:APPLIES_TO]->(x)
           OPTIONAL MATCH (l)-[:LEARNED_IN]->(sess:Session)
           OPTIONAL MATCH (l)-[:REFINES]->(rb:Runbook)
           OPTIONAL MATCH (l)-[:ABOUT_INCIDENT]->(i:Incident)
           RETURN DISTINCT l.id AS id, l.text AS text, l.kind AS kind, l.source AS source,
                  l.created_at AS learned_at, coalesce(l.reinforced, 0) AS reinforced,
                  sess.id AS session, rb.id AS refines_runbook, i.id AS incident,
                  collect(DISTINCT x.id) AS applies_to
           ORDER BY reinforced DESC, learned_at DESC LIMIT 10""",
        {"ids": service_ids},
    )
    t.q("""MATCH p = (l:Lesson)-[:APPLIES_TO]->(s:Service) WHERE s.id IN $ids RETURN p""", {"ids": service_ids})
    t.q("""MATCH p = (l:Lesson)-[:REFINES]->(:Runbook) MATCH (l)-[:APPLIES_TO]->(s:Service) WHERE s.id IN $ids RETURN p""",
        {"ids": service_ids})
    t.q("""MATCH p = (l:Lesson)-[:LEARNED_IN]->(:Session) MATCH (l)-[:APPLIES_TO]->(s:Service) WHERE s.id IN $ids RETURN p""",
        {"ids": service_ids})
    past = t.rows(
        """MATCH (sess:Session)-[:HANDLED]->(i:Incident)-[:AFFECTED|ROOT_CAUSE]->(s:Service) WHERE s.id IN $ids
           RETURN DISTINCT sess.id AS session, sess.started_at AS at, i.id AS incident, i.title AS title, i.status AS status
           ORDER BY at DESC LIMIT 5""",
        {"ids": service_ids},
    )
    # The agents don't know their own session id, so the tool says which lessons come from an earlier conversation.
    for l in res:
        l["earlier_session"] = bool(session_id) and l.get("session") not in (None, session_id)
    return t.out({"lessons": res, "past_sessions": past})


def record_lesson(tenant: str, session_id: str, text: str, service_ids: list[str], kind: str = "observation",
                  source: str = "agent", incident_id: str | None = None, refines_runbook: str | None = None,
                  updated_steps: str | None = None) -> dict:
    """Write a lesson to long-term memory. kind: correction (from a human) | observation | procedure.
    If a near-identical lesson exists for the same services, reinforce it instead of duplicating.
    With refines_runbook + updated_steps, also write a new runbook version that SUPERSEDES the current one."""
    t = Trace(tenant)
    find_dup = """MATCH (l:Lesson)-[:APPLIES_TO]->(s:Service) WHERE s.id IN $ids AND toLower(l.text) = toLower($text)
                  RETURN l.id AS id LIMIT 1"""

    def reinforce(lesson_id: str) -> dict:
        t.q("MATCH (l:Lesson {id: $id}) SET l.reinforced = coalesce(l.reinforced, 0) + 1, l.updated_at = $now",
            {"id": lesson_id, "now": now()}, write=True)
        return t.out({"lesson_id": lesson_id, "reinforced": True})

    if dup := t.rows(find_dup, {"ids": service_ids, "text": text}):
        return reinforce(dup[0]["id"])
    lid = short_id("LSN")
    # Check and create in ONE write query: FalkorDB runs writes to a graph one at a time, so two agents saving the
    # same lesson at the same moment can't both create it (a separate check-then-create could).
    created = t.rows(
        """MATCH (sess:Session {id: $sid})
           OPTIONAL MATCH (d:Lesson)-[:APPLIES_TO]->(ds:Service) WHERE ds.id IN $ids AND toLower(d.text) = toLower($text)
           WITH sess, count(d) AS dups WHERE dups = 0
           CREATE (l:Lesson {id: $lid, text: $text, kind: $kind, source: $source, created_at: $now, reinforced: 0})
           CREATE (l)-[:LEARNED_IN]->(sess)
           WITH l UNWIND $ids AS svc MATCH (s:Service {id: svc}) CREATE (l)-[:APPLIES_TO]->(s)
           RETURN DISTINCT l.id AS id""",
        {"sid": session_id, "lid": lid, "text": text, "kind": kind, "source": source, "now": now(), "ids": service_ids},
        write=True,
    )
    if not created:  # another agent saved the same lesson a moment ago
        if dup := t.rows(find_dup, {"ids": service_ids, "text": text}):
            return reinforce(dup[0]["id"])
        return t.out({"error": f"session {session_id} or services {service_ids} not found"})
    if incident_id:
        t.q("MATCH (l:Lesson {id: $lid}), (i:Incident {id: $iid}) CREATE (l)-[:ABOUT_INCIDENT]->(i)",
            {"lid": lid, "iid": incident_id}, write=True)
    if refines_runbook:
        t.q("MATCH (l:Lesson {id: $lid}), (b:Runbook {id: $rb}) CREATE (l)-[:REFINES]->(b)",
            {"lid": lid, "rb": refines_runbook}, write=True)
    for s in service_ids:
        t.link(lid, "APPLIES_TO", s, "Lesson", "Service", text[:40])
    if refines_runbook:
        t.link(lid, "REFINES", refines_runbook, "Lesson", "Runbook")
    out = {"lesson_id": lid, "reinforced": False}
    if refines_runbook and updated_steps and (current := latest_runbook_id(t, refines_runbook)):
        cur = t.rows("MATCH (b:Runbook {id: $id})-[:FOR]->(s:Service) "
                     "RETURN b.title AS title, s.id AS service", {"id": current})[0]
        base = current.split("-v")[0]
        version = t.rows("MATCH (r:Runbook) WHERE r.id = $b OR r.id STARTS WITH $prefix "
                         "RETURN max(coalesce(r.version, 1)) AS v", {"b": base, "prefix": f"{base}-v"})[0]["v"] + 1
        new_id = f"{base}-v{version}"
        t.q(
            """MATCH (old:Runbook {id: $old}), (s:Service {id: $svc}), (l:Lesson {id: $lid})
               CREATE (nv:Runbook {id: $nid, title: $title, steps: $steps, version: $version, created_at: $now,
                                   source: 'correction', status: 'proposed'})
               CREATE (nv)-[:SUPERSEDES]->(old), (nv)-[:FOR]->(s), (l)-[:PRODUCED]->(nv)""",
            {"old": current, "svc": cur["service"], "lid": lid, "nid": new_id, "title": cur["title"],
             "steps": updated_steps, "version": version, "now": now()},
            write=True,
        )
        t.link(new_id, "SUPERSEDES", current, "Runbook", "Runbook", cur["title"])
        t.link(lid, "PRODUCED", new_id, "Lesson", "Runbook")
        out["new_runbook_version"] = new_id
        out["supersedes"] = current
        out["status"] = "proposed: a human must approve it before agents use it"
    return t.out(out)


# ---------------------------------------------------------------------------
# Situational awareness: never stall on a vague report

def situation_report(tenant: str, money_usd: float | None = None, keywords: str = "") -> dict:
    """A graph-wide sweep for vague reports: firing alerts, active incidents, degraded services, which products and
    customers could account for a money figure, risky open follow-ups, the most critical services, and knowledge
    matching the keywords."""
    t = Trace(tenant)
    alerts = t.rows(
        """MATCH (a:Alert {status: 'firing'})-[:ON]->(s:Service)
           RETURN s.id AS service, count(a) AS alerts, collect(a.severity) AS severities ORDER BY alerts DESC""")
    t.q("MATCH p = (:Alert {status: 'firing'})-[:ON]->(:Service) RETURN p")
    active = t.rows(
        """MATCH (i:Incident {status: 'active'})
           OPTIONAL MATCH (i)-[:AFFECTED]->(s:Service)
           RETURN i.id AS id, i.title AS title, i.sev AS sev, collect(s.id) AS services""")
    degraded = [r["id"] for r in t.rows("MATCH (s:Service) WHERE s.status <> 'healthy' RETURN s.id AS id")]
    products = t.rows(
        """MATCH (p:Product)
           OPTIONAL MATCH (c:Customer)-[:USES]->(p)
           WITH p, sum(c.arr) AS arr, collect({id: c.id, name: c.name, arr: c.arr, sla: c.sla}) AS customers
           OPTIONAL MATCH (s:Service)-[:POWERS]->(p)
           RETURN p.id AS product, p.name AS name, arr, customers, collect(s.id) AS powered_by ORDER BY arr DESC""")
    for p in products:
        p["customers"] = sorted([c for c in p["customers"] if c.get("id")], key=lambda c: -(c["arr"] or 0))
        if money_usd:
            p["could_account_for_money"] = (p["arr"] or 0) >= money_usd
    t.q("MATCH p = (:Customer)-[:USES]->(:Product)<-[:POWERS]-(:Service) RETURN p")
    risky = t.rows(
        """MATCH (tk:Ticket)-[:FOLLOW_UP_OF]->(i:Incident) WHERE tk.status <> 'done' AND i.sev <= 2
           OPTIONAL MATCH (tk)-[:ABOUT]->(s:Service)
           OPTIONAL MATCH (tk)-[:ASSIGNED_TO]->(p:Person)
           RETURN tk.id AS ticket, tk.title AS title, tk.status AS status, s.id AS service, i.id AS from_incident,
                  i.sev AS incident_sev, p.name AS assignee ORDER BY i.sev""")
    critical = [r["id"] for r in t.rows(
        "CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score RETURN node.id AS id ORDER BY score DESC LIMIT 5")]
    matches = []
    words = " | ".join(w for w in "".join(ch if ch.isalnum() else " " for ch in keywords).split() if len(w) > 2)
    if words:
        try:
            matches = t.rows(
                """CALL db.idx.fulltext.queryNodes('Incident', $q) YIELD node, score
                   RETURN node.id AS id, node.title AS title, score ORDER BY score DESC LIMIT 4""", {"q": words})
        except Exception:
            pass
    recent = t.rows(
        """MATCH (i:Incident {status: 'resolved'})-[:ROOT_CAUSE]->(s:Service)
           WHERE i.date >= date() - duration('P180D')
           RETURN s.id AS service, count(i) AS incidents, collect(i.id) AS ids ORDER BY incidents DESC LIMIT 5""")
    big = [c for p in products for c in p["customers"] if money_usd and (c["arr"] or 0) >= money_usd * 0.5]
    return t.out({
        "firing_alerts": alerts, "active_incidents": active, "degraded_services": degraded,
        "money_usd": money_usd, "products_by_arr": products,
        "customers_large_enough": sorted({c["name"]: c for c in big}.values(), key=lambda c: -c["arr"])[:5],
        "risky_open_follow_ups": risky, "most_critical_services": critical, "matching_past_incidents": matches,
        "repeat_offenders_last_6_months": recent,
    })


def correlate_alerts(tenant: str) -> dict:
    """Collapse an alert storm into root causes using the dependency graph: an alerting service with no alerting
    dependency beneath it is a root candidate, and every alerting service that depends on it is explained by it."""
    t = Trace(tenant)
    alerts = t.rows(
        """MATCH (a:Alert {status: 'firing'})-[:ON]->(s:Service)
           RETURN a.id AS id, a.title AS title, a.severity AS severity, s.id AS service""")
    if not alerts:
        return t.out({"alerts_firing": 0, "note": "No alerts are firing."})
    hot = sorted({a["service"] for a in alerts})
    rel = t.rows(
        """UNWIND $hot AS cid
           MATCH (c:Service {id: cid})
           OPTIONAL MATCH (d:Service)-[:DEPENDS_ON*1..6]->(c) WHERE d.id IN $hot
           WITH c, collect(DISTINCT d.id) AS explains
           OPTIONAL MATCH (c)-[:DEPENDS_ON*1..6]->(u:Service) WHERE u.id IN $hot
           RETURN c.id AS service, explains, count(DISTINCT u) AS alerting_below""",
        {"hot": hot},
    )
    by_service: dict[str, list] = {}
    for a in alerts:
        by_service.setdefault(a["service"], []).append(a)
    roots = []
    for r in rel:
        if r["alerting_below"] == 0:
            covered = [r["service"], *r["explains"]]
            past = t.rows("MATCH (i:Incident {status: 'resolved'})-[:ROOT_CAUSE]->(:Service {id: $s}) "
                          "RETURN i.id AS id, i.title AS title, i.sev AS sev ORDER BY i.sev", {"s": r["service"]})
            roots.append({"service": r["service"], "own_alerts": [a["title"] for a in by_service[r["service"]]],
                          "explains_services": r["explains"],
                          "alerts_explained": sum(len(by_service.get(s, [])) for s in covered),
                          "past_incidents_with_this_root": past})
            t.q("""MATCH p = (d:Service)-[:DEPENDS_ON*1..6]->(:Service {id: $root}) WHERE d.id IN $hot RETURN p""",
                {"root": r["service"], "hot": hot})
    # Greedy cover: the root that explains the most alerts claims its services first; later roots only keep the
    # services nobody explained yet. A root left explaining nothing but its own minor alert is likely noise.
    roots.sort(key=lambda x: -x["alerts_explained"])
    claimed: set[str] = set()
    noise = []
    for r in roots:
        new_services = [s for s in r["explains_services"] if s not in claimed]
        r["explains_services"] = new_services
        r["alerts_explained"] = sum(len(by_service.get(s, [])) for s in [r["service"], *new_services])
        if claimed and not new_services and not any(a["severity"] == "critical" for a in by_service[r["service"]]):
            noise.append(r)
        claimed.update([r["service"], *new_services])
    roots = [r for r in roots if r not in noise]
    explained = {s for r in roots for s in [r["service"], *r["explains_services"]]}
    t.q("MATCH p = (:Alert {status: 'firing'})-[:ON]->(:Service) RETURN p")
    return t.out({
        "alerts_firing": len(alerts), "services_alerting": len(hot),
        "summary": (f"{len(alerts)} alerts on {len(hot)} services: "
                    + "; ".join(f"{r['alerts_explained']} trace back to {r['service']}" for r in roots)
                    + (f"; {len(noise)} {'looks' if len(noise) == 1 else 'look'} unrelated ({', '.join(n['service'] for n in noise)})" if noise else "")),
        "root_cause_candidates": roots,
        "likely_unrelated_noise": [{"service": n["service"], "alerts": n["own_alerts"]} for n in noise],
        "unexplained_services": [s for s in hot if s not in explained and s not in {n["service"] for n in noise}],
    })


# ---------------------------------------------------------------------------
# Action tools: the agent changes the graph (and, in a real deployment, the world)

def next_number(t: "Trace", kind: str, start: int) -> int:
    """Race-free sequence: one MERGE+SET write query is atomic in FalkorDB, so two concurrent incidents never
    get the same number (counting nodes would)."""
    return t.rows("MERGE (c:Counter {id: $kind}) SET c.n = coalesce(c.n, $start) + 1 RETURN c.n AS n",
                  {"kind": kind, "start": start}, write=True)[0]["n"]


def declare_incident(tenant: str, session_id: str, title: str, service_id: str, sev: int = 2, summary: str = "") -> dict:
    """Open an active incident on a service and mark the service degraded."""
    t = Trace(tenant)
    iid = f"INC-{next_number(t, 'incident', 300)}"
    t.q(
        """MATCH (s:Service {id: $svc}), (sess:Session {id: $sid})
           CREATE (i:Incident {id: $iid, title: $title, summary: $summary, sev: $sev, status: 'active',
                               date: date($date), opened_at: $now, embedding: vecf32($emb)})
           CREATE (i)-[:AFFECTED]->(s), (sess)-[:HANDLED]->(i)
           SET s.status = 'degraded'""",
        {"svc": service_id, "sid": session_id, "iid": iid, "title": title, "summary": summary, "sev": int(sev),
         "date": now()[:10], "now": now(), "emb": embed(f"{title}. {summary}")},
        write=True,
    )
    t.link(iid, "AFFECTED", service_id, "Incident", "Service", title)
    return t.out({"incident_id": iid, "status": "active"})


APPROVE_PAGES = os.getenv("RAVENHOLD_APPROVE_PAGES", "on") != "off"


def page_person(tenant: str, session_id: str, person_id: str, incident_id: str, message: str) -> dict:
    """Request a page for a person about an incident. Human in the loop: the page waits as
    (:Page {status: 'awaiting_approval'}) until a person approves it in the UI (a real deployment would then call
    PagerDuty or Slack)."""
    t = Trace(tenant)
    pid = short_id("PAGE")
    status = "awaiting_approval" if APPROVE_PAGES else "sent"
    res = t.rows(
        """MATCH (p:Person {id: $person}), (i:Incident {id: $iid})
           CREATE (pg:Page {id: $pid, message: $msg, at: $now, status: $status})
           CREATE (pg)-[:TO]->(p), (pg)-[:FOR]->(i)
           RETURN p.name AS name""",
        {"person": person_id, "iid": incident_id, "pid": pid, "msg": message, "now": now(), "status": status},
        write=True,
    )
    if not res:
        return t.out({"error": "unknown person or incident"})
    t.link(pid, "TO", person_id, "Page", "Person", "page")
    t.link(pid, "FOR", incident_id, "Page", "Incident", "page")
    if status == "sent":
        return t.out({"paged": res[0]["name"], "page_id": pid, "status": "sent"})
    return t.out({"page_id": pid, "person": res[0]["name"], "status": "awaiting human approval",
                  "note": "The on-call human must approve this page in the UI before it is sent."})


def decide_runbook(tenant: str, runbook_id: str, approve: bool) -> dict:
    """The human's decision on a proposed runbook version (not an LLM tool)."""
    res = rows(graph(tenant).query(
        """MATCH (r:Runbook {id: $id})
           SET r.status = CASE WHEN r.status = 'proposed' THEN $status ELSE r.status END,
               r.decided_at = coalesce(r.decided_at, $now)
           RETURN r.id AS id, r.status AS status""",
        {"id": runbook_id, "status": "active" if approve else "rejected", "now": now()},
    ))
    return res[0] if res else {"error": "unknown runbook"}


def decide_page(tenant: str, page_id: str, approve: bool) -> dict:
    """The human's decision on a page request (not an LLM tool)."""
    res = rows(graph(tenant).query(
        """MATCH (pg:Page {id: $id})-[:TO]->(p:Person)
           SET pg.status = CASE WHEN pg.status = 'awaiting_approval' THEN $status ELSE pg.status END,
               pg.decided_at = coalesce(pg.decided_at, $now)
           RETURN pg.status AS status, p.name AS name""",
        {"id": page_id, "status": "sent" if approve else "declined", "now": now()},
    ))
    return res[0] if res else {"error": "unknown page"}


def page_statuses(tenant: str, ids: list[str]) -> dict:
    return {r["id"]: r["status"] for r in rows(graph(tenant).ro_query(
        "MATCH (pg:Page) WHERE pg.id IN $ids RETURN pg.id AS id, coalesce(pg.status, 'sent') AS status", {"ids": ids}))}


def create_ticket(tenant: str, title: str, service_id: str, assignee_id: str, incident_id: str) -> dict:
    """Create a follow-up ticket linked to an incident, a service and an owner."""
    t = Trace(tenant)
    tid = f"TKT-{next_number(t, 'ticket', 600)}"
    t.q(
        """MATCH (s:Service {id: $svc}), (p:Person {id: $who}), (i:Incident {id: $iid})
           CREATE (tk:Source:Ticket {id: $tid, kind: 'ticket', title: $title, text: $title, status: 'open', created_at: $now})
           CREATE (tk)-[:ABOUT]->(s), (tk)-[:ASSIGNED_TO]->(p), (tk)-[:FOLLOW_UP_OF]->(i)""",
        {"svc": service_id, "who": assignee_id, "iid": incident_id, "tid": tid, "title": title, "now": now()},
        write=True,
    )
    t.link(tid, "FOLLOW_UP_OF", incident_id, "Ticket", "Incident", title)
    t.link(tid, "ASSIGNED_TO", assignee_id, "Ticket", "Person", title)
    return t.out({"ticket_id": tid})


def resolve_incident(tenant: str, incident_id: str, root_cause_service: str, resolved_by: list[str],
                     runbook_id: str | None = None, summary: str = "") -> dict:
    """Close an incident with its root cause and fix. Future sessions will find it via similar_incidents."""
    t = Trace(tenant)
    t.q(
        """MATCH (i:Incident {id: $iid}), (root:Service {id: $root})
           SET i.status = 'resolved', i.resolved_at = $now, i.summary = CASE WHEN $summary = '' THEN i.summary ELSE $summary END,
               i.embedding = CASE WHEN $summary = '' THEN i.embedding ELSE vecf32($emb) END
           MERGE (i)-[:ROOT_CAUSE]->(root)
           WITH i MATCH (i)-[:AFFECTED|ROOT_CAUSE]->(s:Service) SET s.status = 'healthy'""",
        {"iid": incident_id, "root": root_cause_service, "now": now(), "summary": summary, "emb": embed(summary or "x")},
        write=True,
    )
    t.q("""MATCH (i:Incident {id: $iid}) UNWIND $people AS pid MATCH (p:Person {id: pid}) MERGE (p)-[:RESOLVED]->(i)""",
        {"iid": incident_id, "people": resolved_by}, write=True)
    if runbook_id and (rb := latest_runbook_id(t, runbook_id)):
        t.q("MATCH (i:Incident {id: $iid}), (b:Runbook {id: $rb}) MERGE (i)-[:FIXED_WITH]->(b)",
            {"iid": incident_id, "rb": rb}, write=True)
    # The incident is over: stop the alerts it caused and mark everything healthy again.
    t.q("""MATCH (a:Alert {status: 'firing'})-[:ON]->(s:Service)
           SET a.status = 'resolved', a.resolved_at = $now, s.status = 'healthy'""", {"now": now()}, write=True)
    t.link(incident_id, "ROOT_CAUSE", root_cause_service, "Incident", "Service")
    return t.out({"incident_id": incident_id, "status": "resolved"})


# ---------------------------------------------------------------------------
# Closed loop: propose a fix -> a human approves -> it is applied -> recovery is verified from the graph.

FIX_SCOPE = """MATCH (root:Service {id: $svc})
               OPTIONAL MATCH (up:Service)-[:DEPENDS_ON*1..6]->(root)
               WITH root, collect(DISTINCT up) + [root] AS scope
               UNWIND scope AS s
               MATCH (a:Alert {status: 'firing'})-[:ON]->(s)
               RETURN DISTINCT a.id AS id, a.title AS title, s.id AS service, s.name AS name"""


def _seconds_between(a: str | None, b: str | None) -> int | None:
    from datetime import datetime
    try:
        return int((datetime.fromisoformat(b.replace("Z", "+00:00")) - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds())
    except Exception:
        return None


def propose_fix(tenant: str, session_id: str, incident_id: str, service_id: str, runbook_id: str, reason: str = "") -> dict:
    """Propose applying a runbook (its latest approved version) to the root-cause service. Nothing changes until a
    human approves it in the UI; then the fix is applied and recovery is checked from the graph."""
    t = Trace(tenant)
    rb = latest_runbook_id(t, runbook_id) or runbook_id
    scope = t.rows(FIX_SCOPE, {"svc": service_id})
    fid = short_id("FIX")
    res = t.rows(
        """MATCH (i:Incident {id: $iid}), (s:Service {id: $svc}), (b:Runbook {id: $rb}), (sess:Session {id: $sid})
           CREATE (f:Fix {id: $fid, status: 'awaiting_approval', reason: $reason, at: $now, expected: $n})
           CREATE (f)-[:FOR]->(i), (f)-[:ON]->(s), (f)-[:APPLIES]->(b), (sess)-[:PROPOSED]->(f)
           RETURN b.title AS title, b.steps AS steps, s.name AS service""",
        {"iid": incident_id, "svc": service_id, "rb": rb, "sid": session_id, "fid": fid, "reason": reason,
         "now": now(), "n": len(scope)}, write=True)
    if not res:
        return t.out({"error": f"unknown incident {incident_id}, service {service_id} or runbook {runbook_id}"})
    for rel, dst, label in (("FOR", incident_id, "Incident"), ("ON", service_id, "Service"), ("APPLIES", rb, "Runbook")):
        t.link(fid, rel, dst, "Fix", label)
    return t.out({"fix_id": fid, "status": "awaiting human approval", "runbook": rb, "runbook_title": res[0]["title"],
                  "service": service_id, "service_name": res[0]["service"], "steps": res[0]["steps"],
                  "alarms_it_should_clear": len(scope), "systems_covered": sorted({a["name"] for a in scope})})


def check_recovery(tenant: str, incident_id: str) -> dict:
    """After a fix: which alarms still fire, whether they belong to this incident, and how long recovery took."""
    t = Trace(tenant)
    inc = t.rows(
        """MATCH (i:Incident {id: $iid})
           OPTIONAL MATCH (f:Fix)-[:FOR]->(i) OPTIONAL MATCH (f)-[:ON]->(s:Service)
           RETURN i.status AS status, i.opened_at AS opened, i.mitigated_at AS mitigated,
                  collect({id: f.id, status: f.status, cleared: f.cleared, service: s.id}) AS fixes""",
        {"iid": incident_id})
    if not inc:
        return t.out({"error": f"unknown incident {incident_id}"})
    fixes = [f for f in inc[0]["fixes"] if f.get("id")]
    applied = [f for f in fixes if f["status"] == "applied"]
    in_scope = {a["id"] for f in applied for a in t.rows(FIX_SCOPE, {"svc": f["service"]})}
    firing = t.rows("MATCH (a:Alert {status: 'firing'})-[:ON]->(s:Service) RETURN a.id AS id, a.title AS title, s.id AS service")
    for a in firing:
        a["related_to_fix"] = a["id"] in in_scope
    t.q("MATCH p = (:Fix)-[:FOR]->(:Incident {id: $iid}) RETURN p", {"iid": incident_id})
    return t.out({"incident": incident_id, "incident_status": inc[0]["status"], "fixes": fixes,
                  "alarms_cleared_by_fixes": sum(f.get("cleared") or 0 for f in applied),
                  "still_firing_related": [a for a in firing if a["related_to_fix"]],
                  "still_firing_unrelated": [a for a in firing if not a["related_to_fix"]],
                  "recovered": bool(applied) and not any(a["related_to_fix"] for a in firing),
                  "seconds_to_mitigate": _seconds_between(inc[0]["opened"], inc[0]["mitigated"])})


def decide_fix(tenant: str, fix_id: str, approve: bool) -> dict:
    """The human's decision on a proposed fix (not an LLM tool). Approving applies it to the system state in the graph
    (alarms explained by the root service clear, those services turn healthy) and then verifies recovery."""
    g = graph(tenant)
    f = rows(g.ro_query(
        """MATCH (f:Fix {id: $id})-[:ON]->(s:Service), (f)-[:FOR]->(i:Incident), (f)-[:APPLIES]->(b:Runbook)
           RETURN f.status AS status, s.id AS service, s.name AS service_name, i.id AS incident, b.id AS runbook""",
        {"id": fix_id}))
    if not f:
        return {"error": "unknown fix"}
    f = f[0]
    if f["status"] != "awaiting_approval":  # a decision can't be flipped afterwards
        return f
    if not approve:
        g.query("MATCH (f:Fix {id: $id}) SET f.status = 'declined', f.decided_at = $now", {"id": fix_id, "now": now()})
        return {**f, "status": "declined"}
    ids = [r["id"] for r in rows(g.ro_query(FIX_SCOPE, {"svc": f["service"]}))]
    stamp = now()
    g.query("""MATCH (a:Alert) WHERE a.id IN $ids
               SET a.status = 'resolved', a.resolved_at = $now, a.resolved_by = $fid
               WITH a MATCH (a)-[:ON]->(s:Service) SET s.status = 'healthy'""", {"ids": ids, "now": stamp, "fid": fix_id})
    g.query("""MATCH (f:Fix {id: $fid})-[:FOR]->(i:Incident), (f)-[:ON]->(root:Service)
               SET f.status = 'applied', f.decided_at = $now, f.cleared = $n, root.status = 'healthy',
                   i.status = 'mitigated', i.mitigated_at = $now""", {"fid": fix_id, "now": stamp, "n": len(ids)})
    verify = check_recovery(tenant, f["incident"])["result"]
    return {**f, "status": "applied", "cleared": len(ids), "recovered": verify["recovered"],
            "still_firing_unrelated": verify["still_firing_unrelated"], "seconds_to_mitigate": verify["seconds_to_mitigate"]}


def fix_statuses(tenant: str, ids: list[str]) -> dict:
    res = rows(graph(tenant).ro_query("MATCH (f:Fix) WHERE f.id IN $ids RETURN f.id AS id, f.status AS status, f.cleared AS cleared",
                                      {"ids": ids}))
    return {r["id"]: {"status": r["status"], "cleared": r["cleared"]} for r in res}


def update_knowledge(tenant: str, text: str) -> dict:
    """Company Brain: turn a statement about the company into graph changes with provenance (see ingest.py)."""
    from .ingest import ingest
    return ingest(tenant, text, kind="chat")


def project_overview(tenant: str, project_id: str = "", team_id: str = "", person_id: str = "", service_id: str = "") -> dict:
    """Who is working on what: projects with their owner team, lead, members, tickets, decisions and docs, plus risks
    worked out from the graph (a ticket held by someone with no history on its system, overdue work, decisions still
    only proposed, active incidents or firing alerts on what the project touches). Filters are optional."""
    t = Trace(tenant)
    projects = t.rows(
        """MATCH (pr:Project)
           OPTIONAL MATCH (team:Team)-[:OWNS]->(pr)
           OPTIONAL MATCH (lead:Person)-[:LEADS]->(pr)
           OPTIONAL MATCH (m:Person)-[:WORKS_ON]->(pr)
           OPTIONAL MATCH (m)-[cur:MEMBER_OF]->(mt:Team) WHERE cur.to IS NULL
           WITH pr, team, lead, collect(DISTINCT {id: m.id, name: m.name, team: mt.id}) AS members
           OPTIONAL MATCH (pr)-[:TOUCHES]->(s:Service)
           WITH pr, team, lead, members, collect(DISTINCT s.id) AS services
           OPTIONAL MATCH (pr)-[:IMPLEMENTS]->(d:Decision)
           WITH pr, team, lead, members, services, collect(DISTINCT {id: d.id, title: d.title, status: d.status}) AS decisions
           OPTIONAL MATCH (pr)-[:DOCUMENTED_IN]->(doc:Doc)
           RETURN pr.id AS id, pr.name AS name, pr.status AS status, pr.goal AS goal, toString(pr.due) AS due,
                  pr.due < date() AS past_due, team.id AS team, lead.id AS lead, lead.name AS lead_name, members,
                  services, decisions, collect(DISTINCT doc.id) AS docs
           ORDER BY due""")
    for p in projects:
        p["members"] = [m for m in p["members"] if m.get("id")]
        p["decisions"] = [d for d in p["decisions"] if d.get("id")]
    projects = [p for p in projects
                if (not project_id or p["id"] == project_id) and (not team_id or p["team"] == team_id)
                and (not person_id or person_id == p["lead"] or any(m["id"] == person_id for m in p["members"]))
                and (not service_id or service_id in p["services"])]
    ids = [p["id"] for p in projects]
    if not ids:
        return t.out({"projects": [], "note": "no project matches these filters"})

    tickets = t.rows(
        """MATCH (tk:Ticket)-[:PART_OF]->(pr:Project) WHERE pr.id IN $ids
           OPTIONAL MATCH (tk)-[:ASSIGNED_TO]->(a:Person)
           OPTIONAL MATCH (tk)-[:ABOUT]->(s:Service)
           OPTIONAL MATCH (a)-[c:CONTRIBUTED_TO]->(s)
           OPTIONAL MATCH (a)-[:RESOLVED]->(i:Incident)-[:ROOT_CAUSE|AFFECTED]->(s)
           RETURN pr.id AS project, tk.id AS id, tk.title AS title, tk.status AS status, a.id AS assignee,
                  a.name AS assignee_name, s.id AS service, coalesce(max(c.commits), 0) AS assignee_commits,
                  count(DISTINCT i) AS assignee_incidents""", {"ids": ids})
    blind = sorted({tk["service"] for tk in tickets if tk["status"] != "done" and tk["service"]
                    and not tk["assignee_commits"] and not tk["assignee_incidents"]})
    best = {}
    if blind:
        for r in t.rows(
            """UNWIND $sids AS sid MATCH (s:Service {id: sid}), (p:Person)
               OPTIONAL MATCH (p)-[c:CONTRIBUTED_TO]->(s)
               OPTIONAL MATCH (p)-[:RESOLVED]->(i:Incident)-[:ROOT_CAUSE|AFFECTED]->(s)
               WITH sid, p, coalesce(max(c.commits), 0) AS commits, count(DISTINCT i) AS incidents
               WHERE commits > 0 OR incidents > 0
               RETURN sid, p.id AS id, p.name AS name, commits, incidents ORDER BY incidents DESC, commits DESC""",
                {"sids": blind}):
            best.setdefault(r["sid"], r)
    live = t.rows(
        """MATCH (pr:Project)-[:TOUCHES]->(s:Service) WHERE pr.id IN $ids
           OPTIONAL MATCH (i:Incident {status: 'active'})-[:AFFECTED|ROOT_CAUSE]->(s)
           OPTIONAL MATCH (a:Alert {status: 'firing'})-[:ON]->(s)
           WITH pr, s, collect(DISTINCT i.id) AS incidents, count(DISTINCT a) AS alerts
           WHERE size(incidents) > 0 OR alerts > 0
           RETURN pr.id AS project, s.id AS service, incidents, alerts""", {"ids": ids})

    for p in projects:
        mine = [tk for tk in tickets if tk["project"] == p["id"]]
        p["tickets"] = [{k: tk[k] for k in ("id", "title", "status", "assignee", "service")} for tk in mine]
        risks = []
        team_ids = {m["id"] for m in p["members"]} | {p["lead"]}
        for tk in mine:
            if tk["status"] != "done" and tk["service"] and not tk["assignee_commits"] and not tk["assignee_incidents"]:
                b = best.get(tk["service"])
                hint = (f"; {b['name']} [{b['id']}] knows it best ({b['incidents']} incident{'s' if b['incidents'] != 1 else ''} "
                        f"resolved, {b['commits']} commits)"
                        + ("" if b["id"] in team_ids else " and is not on this project")) if b else ""
                risks.append(f"{tk['id']} ({tk['status']}) is assigned to {tk['assignee_name']} [{tk['assignee']}], "
                             f"who has no commits and no incidents on {tk['service']}{hint}")
        open_n = sum(tk["status"] != "done" for tk in mine)
        if p["status"] != "done" and p["past_due"] and open_n:
            risks.append(f"past its due date ({p['due']}) with {open_n} open ticket{'s' if open_n > 1 else ''}")
        risks += [f"decision {d['id']} is still {d['status']}, not accepted" for d in p["decisions"] if d["status"] != "accepted"]
        risks += [f"{lv['service']} has " + ", ".join(filter(None, [f"active incident {', '.join(lv['incidents'])}" if lv["incidents"] else "",
                                                                    f"{lv['alerts']} firing alerts" if lv["alerts"] else ""]))
                  for lv in live if lv["project"] == p["id"]]
        p["risks"] = risks
        p.pop("past_due", None)
    t.q("""MATCH p = (x)-[:OWNS|LEADS|WORKS_ON|PART_OF]->(pr:Project) WHERE pr.id IN $ids RETURN p""", {"ids": ids})
    t.q("""MATCH p = (pr:Project)-[:TOUCHES|IMPLEMENTS|DOCUMENTED_IN]->() WHERE pr.id IN $ids RETURN p""", {"ids": ids})
    return t.out({"projects": projects})


# ---------------------------------------------------------------------------
# Tool registry exposed to the LLM

def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
SVC = {"type": "string", "description": "Service id, e.g. 'payments-db'"}

TOOLS = {
    "blast_radius": (blast_radius, "What breaks if this service is down: dependent services (multi-hop), products, customers, ARR at risk.",
                     _schema({"service_id": SVC, "max_hops": {"type": "integer"}}, ["service_id"])),
    "root_cause_candidates": (root_cause_candidates, "Walk down the dependency graph from a symptomatic service to rank likely root causes.",
                              _schema({"service_id": SVC}, ["service_id"])),
    "find_experts": (find_experts, "Who really knows a service (resolved incidents, commits, docs) vs. the owner team and current on-call.",
                     _schema({"service_id": SVC}, ["service_id"])),
    "similar_incidents": (similar_incidents, "Past incidents near this service in the graph, with fixes, resolvers, postmortems, follow-ups, lessons.",
                          _schema({"service_id": SVC, "description": S}, ["service_id"])),
    "get_runbook": (get_runbook, "Runbook steps, track record, and lessons that refine it.",
                    _schema({"runbook_id": S}, ["runbook_id"])),
    "open_follow_ups": (open_follow_ups, "Unfinished action items from past incidents on this service or neighbours.",
                        _schema({"service_id": SVC}, ["service_id"])),
    "trace_decisions": (trace_decisions, "Architecture decisions affecting this service, who made them, and their source docs/chats.",
                        _schema({"service_id": SVC}, ["service_id"])),
    "search_knowledge": (search_knowledge, "Fulltext search across docs, chat, tickets and learned lessons.",
                         _schema({"query": S}, ["query"])),
    "project_overview": (project_overview, "Who is working on what: projects with owner team, lead, members, tickets, "
                         "decisions and docs, and risks worked out from the graph (tickets held by someone with no "
                         "history on that system, overdue work, unapproved decisions, incidents or alerts on what the "
                         "project touches). All filters optional.",
                         _schema({"project_id": S, "team_id": S, "person_id": S, "service_id": SVC}, [])),
    "critical_services": (critical_services, "PageRank / betweenness / WCC over the dependency graph.", _schema({}, [])),
    "shortest_connection": (shortest_connection, "Shortest path between any two node ids (people, services, incidents, customers...).",
                            _schema({"from_id": S, "to_id": S}, ["from_id", "to_id"])),
    "graph_query": (graph_query, "Read-only Cypher for questions no other tool answers. Labels: Service, Team, Person, Incident, "
                    "Runbook, Doc, Message, Ticket, Decision, Product, Customer, Lesson, Session, Project. Use ids like 'payments-db'.",
                    _schema({"cypher": S}, ["cypher"])),
    "recall_memory": (recall_memory, "Lessons and past sessions remembered for these services (from earlier sessions).",
                      _schema({"service_ids": {"type": "array", "items": S}}, ["service_ids"])),
    "record_lesson": (record_lesson, "Save a lesson to long-term memory. Use kind='correction' when a human corrects you. "
                      "If the correction changes how a runbook should be run, pass refines_runbook AND updated_steps "
                      "(the full corrected steps); this writes a new runbook version that supersedes the old one.",
                      _schema({"text": S, "service_ids": {"type": "array", "items": S},
                               "kind": {"type": "string", "enum": ["correction", "observation", "procedure"]},
                               "source": S, "incident_id": S, "refines_runbook": S, "updated_steps": S},
                              ["text", "service_ids"])),
    "update_knowledge": (update_knowledge, "Update the company map from a statement of fact (someone changed team, "
                         "who is on call, who owns a service, a new or removed dependency, a decision, a to-do, who works on a "
                         "project, a project's status or due date, a ticket's new assignee or status). Stores "
                         "the statement as a source and records exactly what changed.",
                         _schema({"text": {"type": "string", "description": "the human's statement, verbatim"}}, ["text"])),
    "situation_report": (situation_report, "Graph-wide sweep for vague or unclear reports: firing alerts, active incidents, "
                         "degraded services, products/customers that could account for a money figure, risky open "
                         "follow-ups, most critical services, matching past incidents.",
                         _schema({"money_usd": {"type": "number", "description": "money figure mentioned, in USD"},
                                  "keywords": {"type": "string", "description": "symptom words from the report"}}, [])),
    "correlate_alerts": (correlate_alerts, "Collapse all firing alerts into root-cause candidates by walking the dependency "
                         "graph (which alerting services are explained by an alerting dependency beneath them).",
                         _schema({}, [])),
    "propose_fix": (propose_fix, "Propose applying a runbook to the root-cause service of an incident (its latest "
                    "approved version is used). A human must approve it in the UI; then it is applied and the alarms "
                    "the root cause explains clear. Use after the cause is clear and the right person is paged.",
                    _schema({"incident_id": S, "service_id": SVC, "runbook_id": S,
                             "reason": {"type": "string", "description": "one line: why this fix, from the graph"}},
                            ["incident_id", "service_id", "runbook_id"])),
    "check_recovery": (check_recovery, "After a fix is applied: which alarms still fire, whether they belong to this "
                       "incident or are unrelated, and how long mitigation took.",
                       _schema({"incident_id": S}, ["incident_id"])),
    "declare_incident": (declare_incident, "Open an active incident on a service.",
                         _schema({"title": S, "service_id": SVC, "sev": {"type": "integer"}, "summary": S}, ["title", "service_id"])),
    "page_person": (page_person, "Page a person about an incident.",
                    _schema({"person_id": S, "incident_id": S, "message": S}, ["person_id", "incident_id", "message"])),
    "create_ticket": (create_ticket, "Create a follow-up ticket.",
                      _schema({"title": S, "service_id": SVC, "assignee_id": S, "incident_id": S},
                              ["title", "service_id", "assignee_id", "incident_id"])),
    "resolve_incident": (resolve_incident, "Resolve an incident with its root cause, resolvers and runbook.",
                         _schema({"incident_id": S, "root_cause_service": SVC, "resolved_by": {"type": "array", "items": S},
                                  "runbook_id": S, "summary": S}, ["incident_id", "root_cause_service", "resolved_by"])),
}

NEEDS_SESSION = {"record_lesson", "declare_incident", "page_person", "recall_memory", "propose_fix"}


SERVICE_ARGS = ("service_id", "root_cause_service")


def check_service_ids(tenant: str, args: dict) -> str | None:
    """Reject unknown service ids with a correction the model can act on (products -> their services,
    near-miss spellings -> closest ids), instead of letting a query silently return nothing."""
    ids = [args[k] for k in SERVICE_ARGS if isinstance(args.get(k), str) and args[k]]
    ids += [s for s in args.get("service_ids") or [] if isinstance(s, str) and s]
    if not ids:
        return None
    g = graph(tenant)
    # Check only the ids we were given (one indexed lookup), not the whole catalog: this must stay fast at scale.
    found = {r[0] for r in g.ro_query("MATCH (s:Service) WHERE s.id IN $ids RETURN s.id", {"ids": ids}).result_set}
    bad = [i for i in ids if i not in found]
    if not bad:
        return None
    import difflib
    known = [r[0] for r in g.ro_query("MATCH (s:Service) RETURN s.id LIMIT 50000").result_set]  # rare path: suggestions
    hints = []
    for b in bad:
        prod = g.ro_query("MATCH (s:Service)-[:POWERS]->(p:Product) WHERE p.id = $b OR toLower(p.name) = toLower($b) "
                          "RETURN collect(s.id)", {"b": b}).result_set
        if prod and prod[0][0]:
            hints.append(f"'{b}' is a Product, not a service; it is powered by services {prod[0][0]}")
        else:
            close = difflib.get_close_matches(b, sorted(known), n=3, cutoff=0.4)
            hints.append(f"unknown service '{b}'" + (f"; did you mean {close}?" if close else ""))
    return "; ".join(hints) + ". Call the tool again with valid service ids."


def run_tool(tenant: str, session_id: str, name: str, args: dict) -> dict:
    fn = TOOLS[name][0]
    if name in NEEDS_SESSION:
        args = {"session_id": session_id, **args}
    try:
        if err := check_service_ids(tenant, args):
            return {"result": {"error": err}, "cypher": [], "subgraph": {"nodes": [], "edges": []}}
        return fn(tenant, **args)
    except Exception as e:  # surface errors to the model instead of crashing the loop
        return {"result": {"error": f"{type(e).__name__}: {e}"}, "cypher": [], "subgraph": {"nodes": [], "edges": []}}


def tool_specs(names: list[str]) -> list[dict]:
    return [{"name": n, "description": TOOLS[n][1], "input_schema": TOOLS[n][2]} for n in names]


if __name__ == "__main__":
    import sys
    out = run_tool("ravenhold", "cli", sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    print(json.dumps(out["result"], indent=2, default=str))
    print(f"\n-- {len(out['cypher'])} queries, {len(out['subgraph']['nodes'])} nodes, {len(out['subgraph']['edges'])} edges highlighted")
