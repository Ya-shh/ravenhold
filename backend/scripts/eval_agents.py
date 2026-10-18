"""Repeated-run evaluation of the real agents (costs about $0.05 per run).

    cd backend && uv run python scripts/eval_agents.py --runs 3 --effort low

For each alert-storm scenario and each run: fresh copy of the template graph, fire the storm, ask the agents, then
score against ground truth computed from the graph itself:
  root cause   the answer names the scenario's real root service
  right person the page request goes to one of the top-2 experts on that root (find_experts), not just the on-call
  noise        the unrelated alert's service is called out as unrelated
  citations    share of cited [ids] that exist in the graph
"""

import argparse
import json
import os
import statistics as st
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GROUND = {"pool-exhaustion": ("pgbouncer", "email-sender"), "broker-loss": ("kafka", "push-service"),
          "cache-evictions": ("session-cache", "warehouse")}


def one_run(scenario: str) -> dict:
    from ravenhold import scenarios, usage
    from ravenhold.agents import handle_message
    from ravenhold.db import copy_graph, graph
    from ravenhold.tools import run_tool

    root, noise = GROUND[scenario]
    name = f"eval-{uuid.uuid4().hex[:8]}"
    copy_graph("ravenhold", name)
    try:
        info = scenarios.inject(name, scenario)
        experts = [e["id"] for e in run_tool(name, "x", "find_experts", {"service_id": root})["result"]["experts"][:2]]
        events: list[dict] = []
        t0 = time.time()
        out = handle_message(name, None, f"{info['alerts']} alerts are firing across {len(info['services'])} services. "
                             "What is going on and who do we page?", events.append)
        latency = time.time() - t0
        answer = out["answer"]
        final = next(e for e in events if e["type"] == "final")
        cites = final["citations"]
        paged = [r[0] for r in graph(name).ro_query("MATCH (:Page)-[:TO]->(p:Person) RETURN p.id").result_set]
        fixed = [r[0] for r in graph(name).ro_query("MATCH (:Fix)-[:ON]->(s:Service) RETURN s.id").result_set]
        return {
            "scenario": scenario,
            "root_ok": root in answer[:900],
            "person_ok": bool(paged) and paged[0] in experts,
            "paged": paged, "experts": experts,
            "fix_ok": bool(fixed) and fixed[0] == root, "fix_on": fixed,
            "noise_ok": noise in answer and "unrelated" in answer.lower(),
            "citations_valid": len(cites["verified"]) / max(1, len(cites["verified"]) + len(cites["unknown"])),
            "unknown_ids": cites["unknown"],
            "latency_s": round(latency, 1),
            "cost_usd": round(sum(e["usd"] for e in events if e["type"] == "usage"), 4),
            "tool_calls": sum(1 for e in events if e["type"] == "tool_call"),
        }
    finally:
        graph(name).delete()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--effort", default=os.getenv("RAVENHOLD_COMMANDER_EFFORT", "low"))
    args = ap.parse_args()
    os.environ["RAVENHOLD_COMMANDER_EFFORT"] = args.effort
    jobs = [s for s in GROUND for _ in range(args.runs)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(one_run, jobs))
    print(json.dumps(results, indent=1))
    pct = lambda k: f"{sum(r[k] for r in results)}/{len(results)}"  # noqa: E731
    lat = [r["latency_s"] for r in results]
    cost = [r["cost_usd"] for r in results]
    print(f"\n| Commander effort | Runs | Root cause correct | Right person paged | Noise called out | Cited ids valid | "
          f"Right fix proposed | Latency median (max) | Cost per answer (median) |\n|---|---|---|---|---|---|---|---|---|\n"
          f"| {args.effort} | {len(results)} | {pct('root_ok')} | {pct('person_ok')} | {pct('noise_ok')} | "
          f"{st.mean(r['citations_valid'] for r in results):.0%} | {pct('fix_ok')} | {st.median(lat):.0f} s ({max(lat):.0f} s) | "
          f"${st.median(cost):.3f} |")


if __name__ == "__main__":
    main()
