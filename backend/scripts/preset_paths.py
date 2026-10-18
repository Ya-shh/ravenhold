"""Check that each preset question in the UI takes the path we document in the README (costs about $0.25 per run).

    cd backend && uv run python scripts/preset_paths.py

Each question runs on a fresh copy of the template graph (no alerts firing, like a new visitor). For each one we list
the tools actually called, then compare against the tools we expect and the fact the answer must contain.
"""

import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# question -> (tools we expect to be called, text the answer must contain)
PRESETS = {
    "Live incident": ("Checkout is throwing 5xx and payments-api has connection timeouts. What is going on and who do we page?",
                      {"root_cause_candidates", "find_experts", "similar_incidents"}, ["pgbouncer", "Sarah"]),
    "Impact": ("What breaks if payments-db goes down? Which customers and how much ARR?", {"blast_radius"}, ["$"]),
    "Expertise": ("Who actually knows pgbouncer, and are they on call?", {"find_experts"}, ["Sarah"]),
    "Projects": ("What is the Platform team working on, and is anything at risk?", {"project_overview"}, ["TKT-512", "Sarah"]),
    "Decisions": ("Why do ledger writes go through Kafka? Trace the decision to its sources.", {"trace_decisions"}, ["ADR-"]),
    "Risk": ("Which services are our biggest single points of failure?", {"critical_services"}, []),
    "Vague report": ("We are losing a million dollars. What is going on?", {"situation_report"}, []),
}


def one(tag: str) -> dict:
    from ravenhold.agents import handle_message
    from ravenhold.db import copy_graph, graph

    text, expect, must = PRESETS[tag]
    name = f"preset-{uuid.uuid4().hex[:8]}"
    copy_graph("ravenhold", name)
    try:
        events: list[dict] = []
        t0 = time.time()
        out = handle_message(name, None, text, events.append)
        final = next(e for e in events if e["type"] == "final")
        steps = [f"{e['agent']}:{e['tool']}({json.dumps(e['input'], separators=(',', ':'))[:80]})"
                 for e in events if e["type"] == "tool_call"]
        called = {e["tool"] for e in events if e["type"] == "tool_call"}
        return {"question": tag, "steps": steps, "expected_tools": sorted(expect), "missing_tools": sorted(expect - called),
                "must_mention": must, "missing_facts": [m for m in must if m.lower() not in out["answer"].lower()],
                "citations": final["citations"], "latency_s": round(time.time() - t0, 1),
                "cost_usd": round(sum(e["usd"] for e in events if e["type"] == "usage"), 4),
                "answer_start": out["answer"][:400]}
    finally:
        graph(name).delete()


if __name__ == "__main__":
    only = [t for t in PRESETS if t in sys.argv[1:]] or list(PRESETS)  # e.g. scripts/preset_paths.py Projects
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(one, only))
    print(json.dumps(results, indent=1))
    print("\n| Question | Path taken (tools) | Expected tools called | Facts present | Cost |\n|---|---|---|---|---|")
    for r in results:
        tools = " → ".join(dict.fromkeys(s.split("(")[0] for s in r["steps"]))
        print(f"| {r['question']} | {tools} | {'yes' if not r['missing_tools'] else 'missing ' + ', '.join(r['missing_tools'])} | "
              f"{'yes' if not r['missing_facts'] else 'missing ' + ', '.join(r['missing_facts'])} | ${r['cost_usd']:.3f} |")
