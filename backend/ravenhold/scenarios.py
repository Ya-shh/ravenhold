"""Alert storms you can inject into the graph to try the agents without any setup.

Each scenario fires alerts on the services a real failure would affect: the failing service plus the services that
depend on it. Nothing in the alerts names the root cause; the agents find it by walking DEPENDS_ON in the graph.
"""

from __future__ import annotations

from .db import graph
from .tools import now, short_id

# (service_id, severity, title, metric)
SCENARIOS: dict[str, dict] = {
    "pool-exhaustion": {
        "name": "Checkout errors at peak traffic",
        "summary": "Errors and timeouts across checkout, payouts and mobile.",
        "impact": "Customers can't finish paying, merchants aren't getting paid out, and the mobile app is failing.",
        "alerts": [
            ("pgbouncer", "critical", "Client wait queue above 2,000", "cl_waiting=2812"),
            ("pgbouncer", "warning", "Pool utilisation at 100% for 5 min", "pool_util=1.0"),
            ("payments-api", "critical", "5xx error rate 31%", "http_5xx_ratio=0.31"),
            ("payments-api", "warning", "p99 latency 8.2s", "latency_p99=8.2s"),
            ("ledger-service", "warning", "Write latency 4.1s", "write_latency_p95=4.1s"),
            ("payouts-service", "critical", "Batch job failing, retrying", "job_failures=148"),
            ("mobile-bff", "warning", "Upstream timeouts to payments-api", "upstream_timeouts=902"),
            ("promo-engine", "warning", "Discount calls timing out", "timeouts=317"),
            ("merchant-dashboard", "warning", "Payout page errors", "http_5xx_ratio=0.12"),
            ("reporting-api", "info", "Ledger reads slow", "read_latency_p95=2.3s"),
            ("referral-service", "info", "Reward credit delayed", "queue_age=14m"),
            ("payments-api", "warning", "Checkout conversion down 42%", "conversion_delta=-0.42"),
            ("email-sender", "info", "Bounce rate slightly up", "bounce_rate=0.04"),  # unrelated noise
        ],
    },
    "broker-loss": {
        "name": "Payouts delayed, reports stale",
        "summary": "Lag and delays across ledger, payouts, analytics and notifications.",
        "impact": "Merchants are waiting on their money, and reports and notifications are running late.",
        "alerts": [
            ("kafka", "critical", "Under-replicated partitions: 64", "urp=64"),
            ("ledger-service", "critical", "Consumer lag 19 min", "consumer_lag=19m"),
            ("payouts-service", "warning", "Payout batch waiting on ledger", "batch_delay=22m"),
            ("analytics-pipeline", "warning", "Ingest lag 25 min", "ingest_lag=25m"),
            ("notifications-service", "warning", "Email and push delayed", "queue_age=11m"),
            ("ml-feature-store", "info", "Feature freshness degraded", "staleness=18m"),
            ("kyc-service", "info", "Verification events delayed", "event_lag=9m"),
            ("reporting-api", "info", "Reports serving stale data", "staleness=31m"),
            ("push-service", "info", "APNs latency slightly up", "apns_p95=1.4s"),  # unrelated noise
        ],
    },
    "cache-evictions": {
        "name": "Logins failing, fraud checks slow",
        "summary": "Login failures, fraud-check timeouts and database load.",
        "impact": "People can't log in, and payment fraud checks are slowing down.",
        "alerts": [
            ("session-cache", "critical", "Evictions 48k/s, hit rate 31%", "hit_rate=0.31"),
            ("auth-service", "critical", "Login failures 22%", "login_fail_ratio=0.22"),
            ("fraud-scorer", "warning", "Scoring timeouts at p99", "timeouts=1204"),
            ("api-gateway", "warning", "401 rate elevated", "http_401_ratio=0.09"),
            ("payments-api", "warning", "Fraud fallback to allow-list", "fallback_ratio=0.18"),
            ("mobile-bff", "warning", "Session refresh failing", "refresh_fail=0.14"),
            ("merchant-dashboard", "info", "Users logged out unexpectedly", "logout_spike=6x"),
            ("warehouse", "info", "Nightly load 9 min slower", "load_delay=9m"),  # unrelated noise
        ],
    },
}


def inject(tenant: str, scenario: str) -> dict:
    """Fire a scenario's alerts into the graph and mark the alerting services degraded."""
    sc = SCENARIOS[scenario]
    g = graph(tenant)
    clear(tenant)
    g.query("MATCH (a:Alert) WHERE a.status <> 'firing' DETACH DELETE a")  # keep the map tidy: old test alerts go
    rows = [{"id": short_id("ALR"), "service": s, "severity": sev, "title": title, "metric": metric}
            for s, sev, title, metric in sc["alerts"]]
    g.query(
        """UNWIND $rows AS r
           MATCH (s:Service {id: r.service})
           CREATE (a:Alert {id: r.id, title: r.title, severity: r.severity, metric: r.metric,
                            status: 'firing', fired_at: $now, scenario: $scenario})
           CREATE (a)-[:ON]->(s)
           SET s.status = 'degraded'""",
        {"rows": rows, "now": now(), "scenario": scenario},
    )
    return {"scenario": scenario, "name": sc["name"], "alerts": len(rows),
            "services": sorted({r["service"] for r in rows})}


def clear(tenant: str) -> None:
    graph(tenant).query(
        """MATCH (a:Alert {status: 'firing'})
           SET a.status = 'resolved', a.resolved_at = $now
           WITH a MATCH (a)-[:ON]->(s:Service) SET s.status = 'healthy'""",
        {"now": now()},
    )


def firing(tenant: str) -> list[dict]:
    from .db import rows
    return rows(graph(tenant).ro_query(
        """MATCH (a:Alert {status: 'firing'})-[:ON]->(s:Service)
           RETURN a.id AS id, a.title AS title, a.severity AS severity, a.metric AS metric,
                  a.fired_at AS fired_at, s.id AS service, s.name AS service_name, a.scenario AS scenario
           ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, s.id"""
    ))
