"""Scale benchmark: does the graph logic hold up beyond the 116-node demo company?

    cd backend && uv run python scripts/bench_scale.py --services 5000

Builds a synthetic company in a throwaway graph (layered service DAG, people, teams, incidents, products, customers),
fires a 60-alert storm, then times the same tool functions the agents call. Deletes the graph afterwards.
"""

import argparse
import random
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ravenhold.db import graph  # noqa: E402
from ravenhold.tools import run_tool  # noqa: E402


def build(name: str, n: int, rnd: random.Random):
    g = graph(name)
    layers = 8
    per = n // layers
    svc = [{"id": f"svc-{i}", "layer": i // per, "tier": min(2, (i // per) // 3)} for i in range(per * layers)]
    g.query("CREATE INDEX FOR (n:Service) ON (n.id)")
    for idx in ("Person", "Team", "Incident", "Product", "Customer"):
        g.query(f"CREATE INDEX FOR (n:{idx}) ON (n.id)")
    for i in range(0, len(svc), 2000):
        g.query("UNWIND $rows AS r CREATE (:Service {id: r.id, name: r.id, tier: r.tier, status: 'healthy'})",
                {"rows": svc[i:i + 2000]})
    deps = []
    for s in svc:  # each service depends on 2-3 services one layer below (deeper layers = infrastructure)
        if s["layer"] < layers - 1:
            below = range((s["layer"] + 1) * per, (s["layer"] + 2) * per)
            for t in rnd.sample(below, rnd.randint(2, 3)):
                deps.append({"a": s["id"], "b": f"svc-{t}"})
    for i in range(0, len(deps), 5000):
        g.query("UNWIND $rows AS r MATCH (a:Service {id: r.a}), (b:Service {id: r.b}) CREATE (a)-[:DEPENDS_ON]->(b)",
                {"rows": deps[i:i + 5000]})
    teams = n // 50
    g.query("UNWIND range(0, $t - 1) AS i CREATE (:Team {id: 'team-' + toString(i), name: 'Team ' + toString(i)})", {"t": teams})
    g.query("UNWIND $rows AS r MATCH (t:Team {id: r.t}), (s:Service {id: r.s}) CREATE (t)-[:OWNS]->(s)",
            {"rows": [{"t": f"team-{rnd.randrange(teams)}", "s": s["id"]} for s in svc]})
    people = n // 2
    g.query("UNWIND range(0, $p - 1) AS i CREATE (:Person {id: 'p-' + toString(i), name: 'Person ' + toString(i)})", {"p": people})
    g.query("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (t:Team {id: r.t}) CREATE (p)-[:MEMBER_OF {from: date('2024-01-15'), to: null}]->(t)",
            {"rows": [{"p": f"p-{i}", "t": f"team-{rnd.randrange(teams)}"} for i in range(people)]})
    # One in ten people changed teams (like Sarah in the demo), so the team-history path is measured at scale too.
    g.query("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (t:Team {id: r.t}) "
            "CREATE (p)-[:MEMBER_OF {from: date('2023-01-10'), to: date('2025-06-01')}]->(t)",
            {"rows": [{"p": f"p-{i}", "t": f"team-{rnd.randrange(teams)}"} for i in range(0, people, 10)]})
    g.query("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (t:Team {id: r.t}) CREATE (p)-[:ON_CALL_FOR]->(t)",
            {"rows": [{"p": f"p-{rnd.randrange(people)}", "t": f"team-{t}"} for t in range(teams)]})
    contrib = [{"p": f"p-{rnd.randrange(people)}", "s": f"svc-{rnd.randrange(len(svc))}", "c": rnd.randint(1, 200)} for _ in range(n * 4)]
    for i in range(0, len(contrib), 5000):
        g.query("UNWIND $rows AS r MATCH (p:Person {id: r.p}), (s:Service {id: r.s}) CREATE (p)-[:CONTRIBUTED_TO {commits: r.c, last_at: date('2026-08-01')}]->(s)",
                {"rows": contrib[i:i + 5000]})
    incs = [{"id": f"inc-{i}", "root": f"svc-{rnd.randrange(len(svc))}", "aff": f"svc-{rnd.randrange(len(svc))}",
             "who": f"p-{rnd.randrange(people)}", "sev": rnd.randint(1, 3)} for i in range(n // 2)]
    for i in range(0, len(incs), 3000):
        g.query("""UNWIND $rows AS r MATCH (root:Service {id: r.root}), (a:Service {id: r.aff}), (p:Person {id: r.who})
                   CREATE (i:Incident {id: r.id, title: 'Incident ' + r.id, summary: 'synthetic', sev: r.sev, status: 'resolved', date: '2026-01-01'})
                   CREATE (i)-[:ROOT_CAUSE]->(root), (i)-[:AFFECTED]->(a), (p)-[:RESOLVED]->(i)""", {"rows": incs[i:i + 3000]})
    prods = n // 25
    g.query("UNWIND range(0, $n - 1) AS i CREATE (:Product {id: 'prod-' + toString(i), name: 'Product ' + toString(i)})", {"n": prods})
    g.query("UNWIND $rows AS r MATCH (s:Service {id: r.s}), (p:Product {id: r.p}) CREATE (s)-[:POWERS]->(p)",
            {"rows": [{"s": f"svc-{i}", "p": f"prod-{i % prods}"} for i in range(per)]})
    custs = n // 5
    g.query("UNWIND range(0, $n - 1) AS i CREATE (:Customer {id: 'c-' + toString(i), name: 'Customer ' + toString(i), arr: 100000, sla: 'gold'})", {"n": custs})
    g.query("UNWIND $rows AS r MATCH (c:Customer {id: r.c}), (p:Product {id: r.p}) CREATE (c)-[:USES]->(p)",
            {"rows": [{"c": f"c-{i}", "p": f"prod-{rnd.randrange(prods)}"} for i in range(custs) for _ in range(2)]})
    # A 60-alert storm: one deep infrastructure root and everything above it that depends on it, plus noise.
    # Worst case: the deep infrastructure service with the most dependents.
    root = g.ro_query("""MATCH (d:Service)-[:DEPENDS_ON]->(s:Service) WHERE s.id IN $ids
                         RETURN s.id, count(d) AS n ORDER BY n DESC LIMIT 1""",
                      {"ids": [f"svc-{i}" for i in range((layers - 2) * per, (layers - 1) * per)]}).result_set[0][0]
    above = [r[0] for r in g.ro_query("MATCH (d:Service)-[:DEPENDS_ON*1..3]->(:Service {id: $r}) RETURN DISTINCT d.id LIMIT 50",
                                       {"r": root}).result_set]
    noise = [f"svc-{rnd.randrange(per)}" for _ in range(5)]
    alerts = [{"s": s, "sev": "critical" if s == root else "warning"} for s in [root, root, *above, *noise]]
    g.query("""UNWIND $rows AS r MATCH (s:Service {id: r.s})
               CREATE (:Alert {id: 'a-' + r.s + toString(rand()), title: 'synthetic', severity: r.sev, status: 'firing'})-[:ON]->(s)""",
            {"rows": alerts})
    return root, svc, len(deps), len(alerts)


def timed(fn, reps=3):
    xs = []
    for _ in range(reps):
        t = time.time()
        out = fn()
        xs.append((time.time() - t) * 1000)
    return st.median(xs), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--services", type=int, default=5000)
    args = ap.parse_args()
    rnd = random.Random(42)
    name = f"bench-{args.services}"
    try:
        graph(name).delete()
    except Exception:
        pass
    t = time.time()
    root, svc, n_deps, n_alerts = build(name, args.services, rnd)
    nodes = graph(name).ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = graph(name).ro_query("MATCH ()-[e]->() RETURN count(e)").result_set[0][0]
    print(f"built {nodes:,} nodes / {edges:,} relationships in {time.time() - t:.0f}s")
    mid = f"svc-{3 * (len(svc) // 8) + 7}"
    rows = []
    for label, tool, a in [
        ("Alert storm → root cause (correlate_alerts)", "correlate_alerts", {}),
        ("Blast radius, deep infrastructure service", "blast_radius", {"service_id": root}),
        ("Blast radius, mid-stack service", "blast_radius", {"service_id": mid}),
        ("Root-cause candidates (walk down 4 hops)", "root_cause_candidates", {"service_id": mid}),
        ("Who knows this system (find_experts)", "find_experts", {"service_id": root}),
        ("Similar past incidents", "similar_incidents", {"service_id": mid, "description": "latency"}),
        ("PageRank + betweenness + WCC", "critical_services", {}),
    ]:
        ms, out = timed(lambda: run_tool(name, "bench", tool, a))
        res = out["result"]
        note = res.get("error") or (res.get("summary") or "")[:70]
        rows.append((label, ms, note))
    print(f"\n| Query ({nodes:,} nodes, {edges:,} relationships, {n_alerts}-alert storm) | Median time |\n|---|---|")
    for label, ms, note in rows:
        print(f"| {label} | {ms:,.0f} ms |" + (f"  <!-- {note} -->" if note else ""))
    graph(name).delete()


if __name__ == "__main__":
    main()
