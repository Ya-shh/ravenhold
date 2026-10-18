"""Run every read tool once against the ravenhold tenant and print a compact summary."""

import json
import sys

from ravenhold.tools import run_tool

CASES = [
    ("blast_radius", {"service_id": "payments-db"}),
    ("root_cause_candidates", {"service_id": "payments-api"}),
    ("find_experts", {"service_id": "payments-db"}),
    ("similar_incidents", {"service_id": "payments-api", "description": "checkout timeouts connection pool"}),
    ("get_runbook", {"runbook_id": "RB-7"}),
    ("open_follow_ups", {"service_id": "payments-db"}),
    ("trace_decisions", {"service_id": "payments-db"}),
    ("search_knowledge", {"query": "pgbouncer"}),
    ("critical_services", {}),
    ("shortest_connection", {"from_id": "sarah", "to_id": "payments-db"}),
    ("graph_query", {"cypher": "MATCH (c:Customer) RETURN c.name, c.arr ORDER BY c.arr DESC"}),
    ("recall_memory", {"service_ids": ["pgbouncer"]}),
]

only = sys.argv[1:]
for name, args in CASES:
    if only and name not in only:
        continue
    out = run_tool("ravenhold", "smoke", name, args)
    print(f"=== {name} {args}")
    print(json.dumps(out["result"], default=str)[:1500])
    print(f"-- {len(out['cypher'])} queries, {len(out['subgraph']['nodes'])} nodes, {len(out['subgraph']['edges'])} edges\n")
