"""Accuracy of the real agents, track by track (costs about $0.05 per agent question).

    cd backend && uv run python scripts/eval_tracks.py --runs 2            # Tracks 2 and 3 (Track 1: eval_agents.py)
    uv run python scripts/eval_tracks.py --runs 2 --only memory

Every check runs on a fresh GRAPH.COPY of the template and is scored against ground truth computed from the graph
(or from a planted fact), never against what the model says about itself.

  Track 1  impact      "If kafka goes down, which customers feel it first?"  share of blast_radius customers named
  Track 2  memory      session A: a correction -> Lesson + proposed RB-7-v2; a human approves it;
                       session B (new): a pgbouncer incident -> the answer uses RECONNECT, says it remembered,
                       the workers got Task handoffs and finished them
  Track 3  brain       projects (TKT-512 / Marcus / Sarah / ADR-19), experts (top expert + on-call),
                       decisions (ADR-12 + its sources), then knowledge updates: 8 pasted statements, each checked
                       in the graph before and after, plus one statement with no facts (nothing should change),
                       and the projects question again after "Sarah takes over TKT-512" (the risk must be gone)
"""

import argparse
import json
import re
import statistics as st
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ravenhold.agents import handle_message  # noqa: E402
from ravenhold.db import copy_graph, graph  # noqa: E402
from ravenhold.tools import decide_runbook, run_tool  # noqa: E402


class Sandbox:
    def __enter__(self):
        self.name = f"eval-{uuid.uuid4().hex[:8]}"
        copy_graph("ravenhold", self.name)
        return self.name

    def __exit__(self, *a):
        graph(self.name).delete()


def ask(g: str, text: str, sid: str | None = None) -> dict:
    ev: list[dict] = []
    t0 = time.time()
    out = handle_message(g, sid, text, ev.append)
    final = next(e for e in ev if e["type"] == "final")
    c = final["citations"]
    return {"answer": out["answer"], "session": out["session_id"], "events": ev, "seconds": round(time.time() - t0, 1),
            "cost": round(sum(e["usd"] for e in ev if e["type"] == "usage"), 4),
            "tools": [e["tool"] for e in ev if e["type"] == "tool_result"],
            "mcp": all(e.get("via") == "mcp" for e in ev if e["type"] == "tool_result" and e["tool"] != "delegate"),
            "cited": len(c["verified"]) + len(c["unknown"]), "cite_ok": len(c["verified"])}


def has(text: str, *words: str) -> bool:
    return all(w.lower() in text.lower() for w in words)


# ---------------------------------------------------------------------------
def track1_impact() -> dict:
    with Sandbox() as g:
        truth = run_tool(g, "x", "blast_radius", {"service_id": "kafka"})["result"]
        names = [c["name"] for c in truth["customers"]]
        r = ask(g, "If kafka goes down, which customers feel it first?")
    named = [n for n in names if n.lower() in r["answer"].lower()]
    return {"check": "impact", "score": len(named) / max(1, len(names)), "detail": f"{len(named)}/{len(names)} customers named",
            "ok": len(named) == len(names), **{k: r[k] for k in ("seconds", "cost", "cited", "cite_ok", "mcp")}}


def track2_memory() -> dict:
    with Sandbox() as g:
        a = ask(g, "Correction for pgbouncer: at peak traffic do NOT pause the pool, it makes checkout worse. "
                   "Reconnect the pool (RECONNECT) instead of PAUSE/RESUME. Please update the runbook.")
        lesson = graph(g).ro_query("MATCH (l:Lesson {kind: 'correction'})-[:APPLIES_TO]->(:Service {id: 'pgbouncer'}) "
                                   "RETURN count(l)").result_set[0][0] > 0
        proposals = [e["runbook_id"] for e in a["events"] if e["type"] == "runbook_proposal"]
        for rb in proposals:
            decide_runbook(g, rb, True)  # the human approves
        b = ask(g, "pgbouncer is at 100% and checkout is failing again. What do we do, step by step?")  # new session
        tasks = [e for e in b["events"] if e["type"] == "task_created"]
        done = {e["task_id"] for e in b["events"] if e["type"] == "task_status" and e["status"] == "done"}
        checks = {
            "lesson saved": lesson,
            "new runbook version proposed": bool(proposals),
            "different session": b["session"] != a["session"],
            "new session uses RECONNECT": has(b["answer"], "reconnect"),
            "says it remembered": "remembered" in b["answer"].lower(),
            "cites the new version": any(p in b["answer"] for p in proposals),
            "handoff: tasks created and all done": bool(tasks) and all(t["task_id"] in done for t in tasks),
        }
    return {"check": "memory", "score": sum(checks.values()) / len(checks), "ok": all(checks.values()),
            "detail": checks, "proposals": proposals, "seconds": a["seconds"] + b["seconds"], "cost": a["cost"] + b["cost"],
            "cited": a["cited"] + b["cited"], "cite_ok": a["cite_ok"] + b["cite_ok"], "mcp": a["mcp"] and b["mcp"]}


def track3_questions() -> list[dict]:
    out = []
    with Sandbox() as g:
        experts = run_tool(g, "x", "find_experts", {"service_id": "pgbouncer"})["result"]
        top = experts["experts"][0]["name"].split()[0]
        oncall = graph(g).ro_query("MATCH (p:Person)-[:ON_CALL_FOR]->(:Team)-[:OWNS]->(:Service {id: 'pgbouncer'}) "
                                   "RETURN p.name").result_set[0][0].split()[0]
        dec = run_tool(g, "x", "trace_decisions", {"service_id": "ledger-service"})["result"]
        adr = next(d for d in dec["decisions"] if d["id"] == "ADR-12")
        sources = [s["id"] for s in adr["sources"]]
        cases = [
            ("projects", "What is the Platform team working on, and is anything at risk?",
             {"TKT-512": "TKT-512", "Marcus holds it": "marcus", "Sarah knows it": "sarah", "ADR-19 pending": "ADR-19",
              "overdue": ("past", "overdue", "due date")}),
            ("experts", "Who actually knows pgbouncer, and are they on call?",
             {f"top expert ({top})": top, f"on-call ({oncall})": oncall}),
            ("decisions", "Why do ledger writes go through Kafka? Trace the decision to its sources.",
             {"ADR-12": "ADR-12", **{f"source {s}": s for s in sources}}),
        ]
        for name, q, want in cases:
            r = ask(g, q)
            low = r["answer"].lower()
            checks = {k: (any(x.lower() in low for x in v) if isinstance(v, tuple) else v.lower() in low) for k, v in want.items()}
            out.append({"check": name, "score": sum(checks.values()) / len(checks), "ok": all(checks.values()),
                        "detail": checks, **{k: r[k] for k in ("seconds", "cost", "cited", "cite_ok", "mcp", "tools")}})
    return out


# (statement, Cypher that must be false before and true after, expected number of changes)
UPDATES = [
    ("Sarah takes over TKT-512 and joins the PgBouncer pool hardening project.",
     "MATCH (:Ticket {id:'TKT-512'})-[:ASSIGNED_TO]->(:Person {id:'sarah'}), (:Person {id:'sarah'})-[:WORKS_ON]->(:Project {id:'PRJ-POOL'}) RETURN count(*) > 0", 2),
    ("James takes over Payments on-call this week.",
     "MATCH (:Person {id:'james'})-[:ON_CALL_FOR]->(:Team {id:'payments'}) RETURN count(*) > 0", 1),
    ("Hannah is moving to the Platform team starting today.",
     "MATCH (:Person {id:'hannah'})-[m:MEMBER_OF]->(:Team {id:'platform'}) WHERE m.to IS NULL RETURN count(*) > 0", 1),
    ("From now on the Data team owns promo-engine.",
     "MATCH (:Team {id:'data'})-[:OWNS]->(:Service {id:'promo-engine'}) RETURN count(*) > 0", 1),
    ("Sophie is leaving the fraud Redis project.",
     "OPTIONAL MATCH (:Person {id:'sophie'})-[r:WORKS_ON]->(:Project {id:'PRJ-FRAUD-REDIS'}) RETURN count(r) = 0", 1),
    ("The index safety checks project is now due on 2026-12-20.",
     "MATCH (p:Project {id:'PRJ-INDEX-CI'}) RETURN toString(p.due) = '2026-12-20'", 1),
    ("ledger-service now also depends on session-cache.",
     "MATCH (:Service {id:'ledger-service'})-[:DEPENDS_ON]->(:Service {id:'session-cache'}) RETURN count(*) > 0", 1),
    ("TKT-530 is done.", "MATCH (t:Ticket {id:'TKT-530'}) RETURN t.status = 'done'", 1),
    ("Lunch is at noon on Friday, see you all there.", None, 0),
]


def track3_updates() -> list[dict]:
    from ravenhold.ingest import ingest
    res = []
    for text, check, n in UPDATES:
        with Sandbox() as g:
            before = graph(g).ro_query(check).result_set[0][0] if check else False
            t0 = time.time()
            out = ingest(g, text)["result"]
            after = graph(g).ro_query(check).result_set[0][0] if check else True
            changes = len(out["changes"])
            res.append({"statement": text, "before": before, "after": after, "changes": changes, "expected": n,
                        "ok": (not before) and after and changes == n, "seconds": round(time.time() - t0, 1)})
    return res


def track3_after_update() -> dict:
    from ravenhold.ingest import ingest
    with Sandbox() as g:
        ingest(g, UPDATES[0][0])
        r = ask(g, "What is the Platform team working on, and is anything at risk?")
    stale = re.search(r"TKT-512[^\n]{0,160}\b(marcus|no commits)", r["answer"], re.I)
    return {"check": "projects after 'Sarah takes over TKT-512'", "ok": not stale, "score": float(not stale),
            "detail": {"TKT-512 risk gone": not stale, "names Sarah": has(r["answer"], "sarah")},
            **{k: r[k] for k in ("seconds", "cost", "cited", "cite_ok", "mcp")}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--only", choices=["impact", "memory", "brain"])
    a = ap.parse_args()
    report: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        if a.only in (None, "impact"):
            report["track1_impact"] = list(pool.map(lambda _: track1_impact(), range(a.runs)))
        if a.only in (None, "memory"):
            report["track2_memory"] = list(pool.map(lambda _: track2_memory(), range(a.runs)))
        if a.only in (None, "brain"):
            report["track3_questions"] = [x for xs in pool.map(lambda _: track3_questions(), range(a.runs)) for x in xs]
            report["track3_updates"] = track3_updates()
            report["track3_after_update"] = list(pool.map(lambda _: track3_after_update(), range(a.runs)))
    print(json.dumps(report, indent=1, default=str))

    print("\n| Track | Check | Runs passed | Mean score | Cited ids valid | Tools over MCP | Median time | Cost |\n|---|---|---|---|---|---|---|---|")
    for key, rows in report.items():
        if key == "track3_updates":
            continue
        by: dict = {}
        for r in rows:
            by.setdefault(r["check"], []).append(r)
        for name, rs in by.items():
            cited, ok = sum(r["cited"] for r in rs), sum(r["cite_ok"] for r in rs)
            print(f"| {key.split('_')[0].replace('track', 'Track ')} | {name} | {sum(r['ok'] for r in rs)}/{len(rs)} | "
                  f"{st.mean(r['score'] for r in rs):.0%} | {ok}/{cited} | {'yes' if all(r['mcp'] for r in rs) else 'no'} | "
                  f"{st.median(r['seconds'] for r in rs):.0f} s | ${sum(r['cost'] for r in rs):.3f} |")
    if "track3_updates" in report:
        u = report["track3_updates"]
        print(f"\nKnowledge updates: {sum(r['ok'] for r in u)}/{len(u)} statements applied exactly "
              "(target false before, true after, no extra changes)")
        for r in u:
            print(f"  {'✅' if r['ok'] else '❌'} {r['statement']}  (before={r['before']}, after={r['after']}, "
                  f"changes={r['changes']}/{r['expected']})")


if __name__ == "__main__":
    main()
