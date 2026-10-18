"""Concurrency benchmark for agent memory and coordination. No LLM calls, no cost.

Simulates many conversations at once, spread over several private visitor graphs (GRAPH.COPY sandboxes). Each
conversation does what an incident run does, through the same code the agents use:

  session + turns  ->  declare_incident  ->  two Task handoffs  ->  both workers read in parallel
  (blast_radius, find_experts, root_cause_candidates | similar_incidents, recall_memory, get_runbook)
  ->  workers write results + Episodes  ->  record_lesson (one lesson every conversation repeats, one of its own)

Then it checks the graph: no duplicate ids, every incident number unique, every task done with an Episode, the
repeated lesson stored once and reinforced, and no sandbox can see another's memory. The sandboxes are deleted.

    cd backend && uv run python scripts/bench_concurrency.py                 # 5 sandboxes x 20 conversations, via MCP
    uv run python scripts/bench_concurrency.py --sandboxes 10 --sessions 20 --via direct
"""

import argparse
import platform
import statistics
import sys
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ravenhold import mcp_bridge  # noqa: E402
from ravenhold.agents import add_turn, complete_task, create_task, ensure_session  # noqa: E402
from ravenhold.db import copy_graph, graph  # noqa: E402
from ravenhold.tools import run_tool  # noqa: E402

SHARED_LESSON = "Under load, RECONNECT the pgbouncer pool instead of PAUSE (bench)."
INVESTIGATOR = [("blast_radius", {"service_id": "pgbouncer"}), ("find_experts", {"service_id": "pgbouncer"}),
                ("root_cause_candidates", {"service_id": "payments-api"})]
HISTORIAN = [("similar_incidents", {"service_id": "payments-api", "description": "pool exhausted"}),
             ("recall_memory", {"service_ids": ["pgbouncer"]}), ("get_runbook", {"runbook_id": "RB-7"})]

lat: dict[str, list[float]] = defaultdict(list)
lat_lock = Lock()


def timed(op: str, fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    with lat_lock:
        lat[op].append((time.perf_counter() - t0) * 1000)
    return out


def tool(via: str, sb: str, sid: str, name: str, args: dict) -> dict:
    out = mcp_bridge.call(sb, sid, name, args) if via == "mcp" else run_tool(sb, sid, name, args)
    if isinstance(out.get("result"), dict) and out["result"].get("error"):
        raise RuntimeError(f"{name}: {out['result']['error']}")
    return out


def worker(via: str, sb: str, sid: str, agent: str, task_id: str, calls) -> str:
    touched = set()
    for name, args in calls:
        out = timed(f"read: {name}", tool, via, sb, sid, name, args)
        touched |= {n["id"] for n in (out.get("subgraph") or {}).get("nodes", [])}
    return timed("write: task result + Episode", complete_task, sb, sid, task_id, agent, "done", f"{agent} done", sorted(touched))


def conversation(via: str, sb: str, i: int) -> None:
    sid = timed("write: session", ensure_session, sb, None)
    timed("write: turn", add_turn, sb, sid, "user", f"bench conversation {i}")
    timed("write: declare_incident", tool, via, sb, sid, "declare_incident",
          {"title": f"bench {sb} #{i}", "service_id": "payments-api", "sev": 2})
    tasks = [(a, timed("write: Task handoff", create_task, sb, sid, a, f"bench task {i}")) for a in ("investigator", "historian")]
    with ThreadPoolExecutor(2) as pool:  # the two workers run in parallel, as in a real run
        list(pool.map(lambda t: worker(via, sb, sid, t[0], t[1], INVESTIGATOR if t[0] == "investigator" else HISTORIAN), tasks))
    timed("write: record_lesson (shared)", tool, via, sb, sid, "record_lesson", {"text": SHARED_LESSON, "service_ids": ["pgbouncer"]})
    timed("write: record_lesson (own)", tool, via, sb, sid, "record_lesson",
          {"text": f"bench lesson {sb} {i}", "service_ids": ["pgbouncer"]})
    timed("write: turn", add_turn, sb, sid, "assistant", f"bench answer {i}")


def check(sb: str, n: int, others: list[str]) -> dict:
    g = graph(sb)
    one = lambda q, p=None: g.ro_query(q, p or {}).result_set[0][0]  # noqa: E731
    return {
        "duplicate ids": one("MATCH (n) WHERE n.id IS NOT NULL WITH labels(n)[0] AS l, n.id AS id, count(*) AS c "
                             "WHERE c > 1 RETURN count(*)"),
        "incidents (unique)": one("MATCH (i:Incident) WHERE i.title STARTS WITH $p RETURN count(DISTINCT i.id)", {"p": f"bench {sb} "}),
        "tasks done with Episode": one("MATCH (e:Episode)-[:FOR_TASK]->(t:Task {status: 'done'}) "
                                       "WHERE t.instructions STARTS WITH 'bench' RETURN count(DISTINCT t)"),
        "shared lesson nodes": one("MATCH (l:Lesson {text: $t}) RETURN count(l)", {"t": SHARED_LESSON}),
        "shared lesson reinforced": one("MATCH (l:Lesson {text: $t}) RETURN coalesce(max(l.reinforced), 0)", {"t": SHARED_LESSON}),
        "own lessons": one("MATCH (l:Lesson) WHERE l.text STARTS WITH $p RETURN count(l)", {"p": f"bench lesson {sb} "}),
        "turns": one("MATCH (t:Turn) WHERE t.text STARTS WITH 'bench' RETURN count(t)"),
        "other sandboxes' lessons visible": sum(one("MATCH (l:Lesson) WHERE l.text STARTS WITH $p RETURN count(l)",
                                                    {"p": f"bench lesson {o} "}) for o in others),
    }


def pct(xs: list[float], p: float) -> float:
    return statistics.quantiles(xs, n=100)[p - 1] if len(xs) > 1 else xs[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandboxes", type=int, default=5)
    ap.add_argument("--sessions", type=int, default=20, help="conversations per sandbox")
    ap.add_argument("--via", choices=["mcp", "direct"], default="mcp")
    a = ap.parse_args()

    if a.via == "mcp" and not mcp_bridge.toolbox():
        print("MCP server did not start"); return 1
    sandboxes = [f"bench__{uuid.uuid4().hex[:8]}" for _ in range(a.sandboxes)]
    t0 = time.perf_counter()
    for sb in sandboxes:
        copy_graph("ravenhold", sb)
    copy_ms = (time.perf_counter() - t0) * 1000 / len(sandboxes)
    jobs = [(sb, i) for i in range(a.sessions) for sb in sandboxes]
    failures = []
    try:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(len(jobs)) as pool:  # every conversation at once
            futs = [pool.submit(conversation, a.via, sb, i) for sb, i in jobs]
            for f in futs:
                if f.exception():
                    failures.append(repr(f.exception()))
        wall = time.perf_counter() - t0
        results = {sb: check(sb, a.sessions, [o for o in sandboxes if o != sb]) for sb in sandboxes}
    finally:
        for sb in sandboxes:
            graph(sb).delete()

    n, ops = len(jobs), sum(len(v) for v in lat.values())
    reads = sum(len(v) for k, v in lat.items() if k.startswith("read"))
    print(f"\n{n} conversations at once ({a.sandboxes} sandboxes x {a.sessions}), tools via {a.via}, "
          f"FalkorDB {'local Docker' if 'localhost' in str(graph(sandboxes[0])._graph if hasattr(graph(sandboxes[0]), '_graph') else 'localhost') else ''} "
          f"on {platform.machine()} {platform.system()}")
    print(f"wall time {wall:.2f} s · {ops} graph operations ({reads} reads, {ops - reads} writes) · "
          f"{ops / wall:.0f} ops/s · GRAPH.COPY {copy_ms:.0f} ms per sandbox · failures: {len(failures)}")
    for f in failures[:5]:
        print("  FAIL", f)
    print("\n| Operation | Count | p50 ms | p95 ms | max ms |\n|---|---|---|---|---|")
    for k in sorted(lat):
        v = lat[k]
        print(f"| {k} | {len(v)} | {statistics.median(v):.0f} | {pct(v, 95):.0f} | {max(v):.0f} |")

    expect = {"duplicate ids": 0, "incidents (unique)": a.sessions, "tasks done with Episode": 2 * a.sessions,
              "shared lesson nodes": 1, "shared lesson reinforced": a.sessions - 1, "own lessons": a.sessions,
              "turns": 2 * a.sessions, "other sandboxes' lessons visible": 0}
    print("\n| Check (per sandbox) | Expected | Got |\n|---|---|---|")
    ok = not failures
    for k, e in expect.items():
        got = sorted({r[k] for r in results.values()})
        ok &= got == [e]
        print(f"| {k} | {e} | {', '.join(map(str, got))} {'✅' if got == [e] else '❌'} |")
    print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
