"""Spend tracking and a hard budget guard, so the API key can't run dry before judging.

Every model call adds its cost to a (:Spend {model}) node in a separate `ops` graph in FalkorDB, so the
total survives restarts and redeploys (a hosted container's disk does not). Cost is computed from the
token counts the API returns. The provider's console is the source of truth; this is a close local
estimate the app can act on immediately.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .db import graph, rows

BUDGET_USD = float(os.getenv("RAVENHOLD_BUDGET_USD", "4.00"))  # leave headroom under the $5 of credits
OPS_GRAPH = "ops"
LEGACY_LEDGER = Path(__file__).resolve().parent.parent / "usage.jsonl"

# $ per 1M tokens: (input, output, cache_write, cache_read)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "openai/gpt-oss-120b": (0.15, 0.60, 0.15, 0.15),
    "openai/gpt-oss-20b": (0.075, 0.30, 0.075, 0.075),
}
HAIKU_LONG = (0.50, 2.50, 0.625, 0.05)  # Haiku 5.5 prompts over 100K tokens


class BudgetExceeded(RuntimeError):
    pass


def cost(model: str, inp: int, out: int, cache_write: int = 0, cache_read: int = 0) -> float:
    p = PRICES.get(model, PRICES["claude-sonnet-5-5"])  # unknown model: assume Sonnet pricing, the safe side
    if model == "claude-haiku-5-5" and inp + cache_write + cache_read > 100_000:
        p = HAIKU_LONG
    return (inp * p[0] + out * p[1] + cache_write * p[2] + cache_read * p[3]) / 1_000_000


def _add(model: str, usd: float, tokens_in: int, tokens_out: int, calls: int = 1, role: str = "earlier") -> None:
    # MERGE + SET in one query is atomic in FalkorDB, so parallel workers can't lose updates.
    graph(OPS_GRAPH).query(
        """MERGE (s:Spend {model: $model, role: $role})
           SET s.usd = coalesce(s.usd, 0) + $usd, s.calls = coalesce(s.calls, 0) + $calls,
               s.tokens_in = coalesce(s.tokens_in, 0) + $tin, s.tokens_out = coalesce(s.tokens_out, 0) + $tout,
               s.updated_at = $now""",
        {"model": model, "role": role, "usd": usd, "calls": calls, "tin": tokens_in, "tout": tokens_out,
         "now": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    )


def record(model: str, agent: str, inp: int, out: int, cache_write: int = 0, cache_read: int = 0) -> float:
    usd = cost(model, inp, out, cache_write, cache_read)
    _add(model, usd, inp + cache_write + cache_read, out, role=agent)
    return usd


def log_call(session_id: str, agent: str, cost: dict) -> dict:
    """One row per model call, so users can see every call and its cost (the Usage tab)."""
    row = {"id": f"CALL-{datetime.now(timezone.utc).strftime('%H%M%S%f')}", "session": session_id, "agent": agent,
           "model": cost["model"], "tokens_in": cost["tokens_in"], "tokens_out": cost["tokens_out"],
           "usd": round(cost["usd"], 6), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    graph(OPS_GRAPH).query("CREATE (:Call {id: $id, session: $session, agent: $agent, model: $model, "
                           "tokens_in: $tokens_in, tokens_out: $tokens_out, usd: $usd, at: $at})", row)
    return row


def recent_calls(limit: int = 100) -> list[dict]:
    return rows(graph(OPS_GRAPH).query(
        "MATCH (c:Call) RETURN c.id AS id, c.session AS session, c.agent AS agent, c.model AS model, "
        "c.tokens_in AS tokens_in, c.tokens_out AS tokens_out, c.usd AS usd, c.at AS at "
        "ORDER BY c.at DESC, c.id DESC LIMIT $n", {"n": int(limit)}))


def _import_legacy_ledger() -> None:
    """One-time import of the earlier file-based ledger so no spend is forgotten."""
    if not LEGACY_LEDGER.exists():
        return
    totals: dict[str, list] = {}
    for raw in LEGACY_LEDGER.read_text().splitlines():
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = totals.setdefault(r["model"], [0.0, 0, 0, 0])
        t[0] += r["usd"]
        t[1] += r["in"] + r.get("cache_write", 0) + r.get("cache_read", 0)
        t[2] += r["out"]
        t[3] += 1
    for model, (usd, tin, tout, calls) in totals.items():
        _add(model, usd, tin, tout, calls)
    LEGACY_LEDGER.rename(LEGACY_LEDGER.with_suffix(".jsonl.imported"))


def summary() -> dict:
    _import_legacy_ledger()
    spend = rows(graph(OPS_GRAPH).query(
        "MATCH (s:Spend) RETURN s.model AS model, coalesce(s.role, 'earlier') AS role, s.usd AS usd, s.calls AS calls, "
        "s.tokens_in AS tokens_in, s.tokens_out AS tokens_out"))
    by_model: dict[str, dict] = {}
    by_role: dict[str, dict] = {}
    for r in spend:
        for key, bucket in ((r["model"], by_model), (r["role"], by_role)):
            b = bucket.setdefault(key, {"usd": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0})
            for k in b:
                b[k] += r[k] or 0
    total = sum(r["usd"] for r in spend)
    rnd = lambda d: {k: {**v, "usd": round(v["usd"], 4)} for k, v in d.items()}  # noqa: E731
    from .llm import active_models
    return {"spent_usd": round(total, 4), "budget_usd": BUDGET_USD, "remaining_usd": round(BUDGET_USD - total, 4),
            "calls": sum(r["calls"] for r in spend), "by_model": rnd(by_model), "by_role": rnd(by_role),
            "models": active_models(), "prices_per_mtok": {m: {"input": p[0], "output": p[1]} for m, p in PRICES.items()}}


def check_budget() -> None:
    s = summary()
    if s["spent_usd"] >= BUDGET_USD:
        raise BudgetExceeded(
            f"Spend guard: ${s['spent_usd']:.2f} used of the ${BUDGET_USD:.2f} budget. Raise RAVENHOLD_BUDGET_USD "
            "only if you have credit left.")


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2))
