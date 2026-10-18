"""End-to-end test of the real agents (costs ~$0.10 with Claude). Run it only when you need to.

    cd backend && uv run python scripts/e2e_test.py

Resets the ravenhold tenant, then:
  Session 1: live incident -> human correction -> resolved
  Session 2: NEW session, same symptoms -> must recall the correction from session 1 (only the graph carries it)
and checks the graph-backed behaviour with simple assertions.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ravenhold import seed, usage  # noqa: E402
from ravenhold.agents import handle_message  # noqa: E402
from ravenhold.db import graph  # noqa: E402
from ravenhold.tools import decide_runbook  # noqa: E402

TURNS = [
    ("s1", "Checkout is throwing 5xx and payments-api has connection timeouts. Who do we page?"),
    ("s1", "Correction from Sarah: PAUSE in RB-7 froze checkout for 4 minutes at peak traffic. During peak, "
           "use RECONNECT instead of PAUSE, then raise the pool limits. Also the limits had reverted because "
           "TKT-512 was never done, so always check the live pgbouncer config against RB-7 first."),
    ("s1", "It's fixed. Root cause was pgbouncer again; Sarah resolved it with RB-7."),
    ("s2", "payments-api is timing out on database connections again. What should we do?"),
]


def q(cypher, params=None):
    return graph("ravenhold").ro_query(cypher, params or {}).result_set


def main():
    g = seed.reset("ravenhold")
    seed.load_company(g)
    seed.load_agents(g)
    spent0 = usage.summary()["spent_usd"]
    sessions, answers = {}, []
    for label, text in TURNS:
        t0 = time.time()
        calls = []
        out = handle_message("ravenhold", sessions.get(label), text,
                             lambda ev: calls.append(ev["tool"]) if ev["type"] == "tool_call" else None)
        sessions[label] = out["session_id"]
        for (rb,) in graph("ravenhold").ro_query("MATCH (r:Runbook {status: 'proposed'}) RETURN r.id").result_set:
            decide_runbook("ravenhold", rb, approve=True)  # the human approves the proposed runbook version
        answers.append(out["answer"])
        print(f"\n=== [{label}] {text[:80]}\n--- {len(calls)} tool calls, {time.time() - t0:.0f}s: {', '.join(calls)}")
        print(out["answer"])

    checks = {
        "session 1 paged priya": bool(q("MATCH (:Page)-[:TO]->(:Person {id: 'sarah'}) RETURN 1")),
        "correction stored as a Lesson refining RB-7":
            bool(q("MATCH (l:Lesson {kind: 'correction'})-[:REFINES]->(:Runbook {id: 'RB-7'}) RETURN 1")),
        "incident resolved with pgbouncer root cause":
            bool(q("MATCH (i:Incident {status: 'resolved'})-[:ROOT_CAUSE]->(:Service {id: 'pgbouncer'}) "
                   "WHERE i.id STARTS WITH 'INC-3' RETURN 1")),
        "workers coordinated through Task nodes": q("MATCH (t:Task {status: 'done'}) RETURN count(t)")[0][0] >= 2,
        "session 2 is a different session": sessions["s1"] != sessions["s2"],
        "session 2 recalled RECONNECT": "RECONNECT" in answers[-1].upper(),
    }
    print("\n" + "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in checks.items()))
    print(f"cost ${usage.summary()['spent_usd'] - spent0:.4f}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
