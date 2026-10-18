"""Deterministic tests of the graph logic the agents rely on. No LLM calls, no cost.

Each test runs on a throwaway copy of the template graph (FalkorDB GRAPH.COPY), so real data is never touched.
    cd backend && uv run pytest -q
"""

import threading
import uuid

import pytest

from ravenhold import scenarios, seed
from ravenhold.agents import split_suggestions, verify_citations
from ravenhold.db import client, copy_graph, graph
from ravenhold.tools import Trace, check_service_ids, next_number, run_tool

TEMPLATE = "ravenhold"


@pytest.fixture()
def g():
    if TEMPLATE not in client().list_graphs():
        seed.main()
    name = f"test-{uuid.uuid4().hex[:8]}"
    copy_graph(TEMPLATE, name)
    graph(name).query("MERGE (:Session {id: 'SES-test', started_at: 'test'})")
    yield name
    graph(name).delete()


@pytest.mark.parametrize("scenario,root,noise", [
    ("pool-exhaustion", "pgbouncer", "email-sender"),
    ("broker-loss", "kafka", "push-service"),
    ("cache-evictions", "session-cache", "warehouse"),
])
def test_alert_storm_collapses_to_one_root_and_flags_noise(g, scenario, root, noise):
    scenarios.inject(g, scenario)
    r = run_tool(g, "SES-test", "correlate_alerts", {})["result"]
    assert [c["service"] for c in r["root_cause_candidates"]] == [root]
    assert [n["service"] for n in r["likely_unrelated_noise"]] == [noise]
    assert r["unexplained_services"] == []


def test_expert_is_who_fixed_it_not_who_is_on_call(g):
    r = run_tool(g, "x", "find_experts", {"service_id": "payments-db"})["result"]
    assert r["experts"][0]["id"] == "sarah"
    assert r["owner"]["on_call_id"] == "marcus"
    assert r["owner"]["on_call_commits"] == 0 and r["owner"]["on_call_incidents_resolved"] == 0
    assert any(m["person"] == "sarah" and m["former_team"] == "Payments" for m in r["team_moves"])


def test_blast_radius_reaches_customers_and_revenue(g):
    r = run_tool(g, "x", "blast_radius", {"service_id": "payments-db"})["result"]
    assert {"pgbouncer", "payments-api", "payouts-service"} <= {d["id"] for d in r["dependent_services"]}
    assert "checkout" in {p["product"] for p in r["products"]}
    assert r["arr_at_risk_usd"] > 1_000_000


def test_open_follow_up_from_past_incident_is_found(g):
    r = run_tool(g, "x", "open_follow_ups", {"service_id": "payments-db"})["result"]
    assert any(t["id"] == "TKT-512" and t["from_incident"] == "INC-142" for t in r["open_follow_ups"])


def test_decision_traces_back_to_chat_and_postmortem(g):
    r = run_tool(g, "x", "trace_decisions", {"service_id": "ledger-service"})["result"]
    adr = next(d for d in r["decisions"] if d["id"] == "ADR-12")
    assert {"MSG-5", "PM-142"} <= {s["id"] for s in adr["sources"]}


def test_correction_proposes_runbook_version_that_needs_approval(g):
    from ravenhold.tools import decide_runbook
    out = run_tool(g, "SES-test", "record_lesson", {
        "text": "At peak use RECONNECT, not PAUSE", "service_ids": ["pgbouncer"], "kind": "correction",
        "refines_runbook": "RB-7", "updated_steps": "1) compare config 2) RECONNECT 3) raise limits"})["result"]
    assert out["new_runbook_version"] == "RB-7-v2" and out["status"].startswith("proposed")
    rb = run_tool(g, "x", "get_runbook", {"runbook_id": "RB-7"})["result"]
    assert rb["id"] == "RB-7" and rb["pending_proposals"][0]["id"] == "RB-7-v2"  # not used until approved
    assert decide_runbook(g, "RB-7-v2", approve=True)["status"] == "active"
    rb = run_tool(g, "x", "get_runbook", {"runbook_id": "RB-7"})["result"]
    assert rb["id"] == "RB-7-v2" and "RECONNECT" in rb["steps"]
    again = run_tool(g, "SES-test", "record_lesson", {
        "text": "Also drain long transactions", "service_ids": ["pgbouncer"], "kind": "correction",
        "refines_runbook": "RB-7", "updated_steps": "v3"})["result"]
    assert again["new_runbook_version"] == "RB-7-v3" and again["supersedes"] == "RB-7-v2"
    assert decide_runbook(g, "RB-7-v3", approve=False)["status"] == "rejected"
    assert run_tool(g, "x", "get_runbook", {"runbook_id": "RB-7"})["result"]["id"] == "RB-7-v2"


def test_model_written_cypher_has_a_time_limit(g):
    r = run_tool(g, "x", "graph_query", {"cypher": "UNWIND range(1, 80000000) AS x WITH x WHERE x % 7 = 0 RETURN count(x)"})
    assert "error" in r["result"] and "timed out" in r["result"]["error"].lower()


def test_lesson_is_recalled_in_a_later_session(g):
    run_tool(g, "SES-test", "record_lesson", {"text": "Check live pool config first", "service_ids": ["pgbouncer"],
                                              "kind": "correction"})
    r = run_tool(g, "SES-other", "recall_memory", {"service_ids": ["payments-api"]})["result"]
    assert any("live pool config" in l["text"] for l in r["lessons"])  # found via the pgbouncer neighbour


def test_duplicate_lesson_is_reinforced_not_duplicated(g):
    args = {"text": "Same lesson", "service_ids": ["kafka"], "kind": "observation"}
    first = run_tool(g, "SES-test", "record_lesson", args)["result"]
    second = run_tool(g, "SES-test", "record_lesson", args)["result"]
    assert second["reinforced"] and second["lesson_id"] == first["lesson_id"]


def test_incident_numbers_are_unique_under_concurrency(g):
    got = []
    threads = [threading.Thread(target=lambda: got.append(next_number(Trace(g), "incident", 300))) for _ in range(25)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(got)) == 25


def test_wrong_ids_get_a_correction_not_an_empty_result(g):
    assert "Product" in check_service_ids(g, {"service_id": "checkout"})
    assert "payments-db" in check_service_ids(g, {"service_id": "payment-db"})
    assert check_service_ids(g, {"service_id": "payments-db"}) is None


def test_graph_query_tool_is_read_only(g):
    r = run_tool(g, "x", "graph_query", {"cypher": "MATCH (n) DETACH DELETE n"})["result"]
    assert "error" in r
    assert graph(g).ro_query("MATCH (s:Service) RETURN count(s)").result_set[0][0] == 27


def test_citations_are_checked_against_the_graph(g):
    r = verify_citations(g, "Page [sarah] about [INC-142]; see [TKT-999].")
    assert r == {"verified": ["INC-142", "sarah"], "unknown": ["TKT-999"]}


def test_next_step_suggestions_are_parsed():
    text, opts = split_suggestions("Answer\n\nNEXT: Mark it resolved | Teach a correction")
    assert text == "Answer" and opts == ["Mark it resolved", "Teach a correction"]
    assert split_suggestions("no next line here")[1] == []


def test_paging_waits_for_human_approval(g):
    inc = run_tool(g, "SES-test", "declare_incident", {"title": "t", "service_id": "pgbouncer"})["result"]["incident_id"]
    r = run_tool(g, "SES-test", "page_person", {"person_id": "sarah", "incident_id": inc, "message": "help"})["result"]
    assert r["status"] == "awaiting human approval"
    from ravenhold.tools import decide_page, page_statuses
    assert page_statuses(g, [r["page_id"]]) == {r["page_id"]: "awaiting_approval"}
    assert decide_page(g, r["page_id"], approve=True)["status"] == "sent"
    assert decide_page(g, r["page_id"], approve=False)["status"] == "sent"  # a decision can't be flipped later


def test_database_enforces_unique_ids(g):
    with pytest.raises(Exception, match="unique constraint"):
        graph(g).query("CREATE (:Service {id: 'pgbouncer'})")
    statuses = {r[4] for r in graph(g).ro_query("CALL db.constraints()").result_set}
    assert statuses == {"OPERATIONAL"}


def test_vector_index_finds_incident_described_in_other_words(g):
    r = run_tool(g, "x", "similar_incidents",
                 {"service_id": "payments-api", "description": "clients queueing at the pooler, connections waiting"})["result"]
    top = r["incidents"][0]
    assert top["id"] == "INC-142" and "vector" in top["matched_by"]


def test_native_dates_power_team_history_and_recency(g):
    r = run_tool(g, "x", "find_experts", {"service_id": "payments-db"})["result"]
    sarah = next(m for m in r["team_moves"] if m["person"] == "sarah")
    assert sarah["to"] == "2026-06-01" and sarah["days_since_move"] > 0
    assert all(e["current_team"] for e in r["experts"])  # current team = MEMBER_OF.to IS NULL


def test_algorithms_find_failure_domains_and_reach(g):
    r = run_tool(g, "x", "critical_services", {})["result"]
    assert r["failure_domains_label_propagation"] and r["widest_reach_harmonic"]
    assert r["most_depended_on_pagerank"][0]["id"] == "kafka"


def _source(g, sid="SRC-test"):
    graph(g).query("CREATE (:Source:Message {id: $id, kind: 'chat', title: 't', text: 't'})", {"id": sid})
    return sid


def test_team_change_keeps_history_and_records_provenance(g):
    from ravenhold.ingest import apply_facts
    out = apply_facts(g, _source(g), [{"kind": "team_change", "person_id": "sarah", "team_id": "data",
                                       "effective_date": "2026-10-01"}])["result"]
    assert out["changes"] and not out["skipped"]
    hist = graph(g).ro_query("MATCH (:Person {id:'sarah'})-[m:MEMBER_OF]->(t:Team) "
                             "RETURN t.id, toString(m.to) ORDER BY m.from").result_set
    assert hist[-1] == ["data", None] and hist[-2][1] == "2026-10-01"  # old platform membership closed, not deleted
    assert graph(g).ro_query("MATCH (:Change)-[:FROM_SOURCE]->(:Source {id:'SRC-test'}) RETURN count(*)").result_set[0][0] == 1


def test_ingested_facts_change_what_the_agents_see(g):
    from ravenhold.ingest import apply_facts
    sid = _source(g)
    apply_facts(g, sid, [{"kind": "on_call_change", "person_id": "sarah", "team_id": "payments"},
                         {"kind": "dependency_added", "service_id": "payments-api", "depends_on_id": "observability"},
                         {"kind": "decision", "title": "Move session cache to managed Redis", "affects": ["session-cache"],
                          "made_by": "fiona"},
                         {"kind": "team_change", "person_id": "nobody", "team_id": "data"}])
    r = run_tool(g, "x", "find_experts", {"service_id": "payments-db"})["result"]
    assert r["owner"]["on_call_id"] == "sarah"
    br = run_tool(g, "x", "blast_radius", {"service_id": "observability"})["result"]
    assert "payments-api" in {d["id"] for d in br["dependent_services"]}
    dec = run_tool(g, "x", "trace_decisions", {"service_id": "session-cache"})["result"]["decisions"]
    assert any(d["title"].startswith("Move session cache") and any(s["id"] == sid for s in d["sources"]) for d in dec)


def test_projects_show_who_works_on_what_and_risks_from_the_graph(g):
    r = run_tool(g, "SES-test", "project_overview", {})["result"]["projects"]
    pool = next(p for p in r if p["id"] == "PRJ-POOL")
    assert pool["team"] == "platform" and pool["lead"] == "daniel"
    blind = [x for x in pool["risks"] if x.startswith("TKT-512")]
    assert blind and "[marcus]" in blind[0] and "[sarah]" in blind[0] and "not on this project" in blind[0]
    assert any("past its due date" in x for x in pool["risks"])
    fraud = next(p for p in r if p["id"] == "PRJ-FRAUD-REDIS")
    assert any("ADR-19" in x for x in fraud["risks"])
    assert not next(p for p in r if p["id"] == "PRJ-SECRETS")["risks"]  # done, nothing open
    assert [p["id"] for p in run_tool(g, "SES-test", "project_overview", {"person_id": "marcus"})["result"]["projects"]] == ["PRJ-POOL"]


def test_pasted_updates_keep_projects_and_tickets_current_with_their_source(g):
    from ravenhold.ingest import apply_facts
    graph(g).query("CREATE (:Source:Message {id: 'SRC-t', kind: 'chat', title: 't', text: 't'})")
    out = apply_facts(g, "SRC-t", [
        {"kind": "ticket_update", "ticket_id": "TKT-512", "assignee_id": "sarah"},
        {"kind": "project_member", "person_id": "sarah", "project_id": "PRJ-POOL"},
        {"kind": "project_status", "project_id": "PRJ-POOL", "due_date": "2026-12-01"},
    ])["result"]
    assert len(out["changes"]) == 3 and not out["skipped"]
    pool = run_tool(g, "SES-test", "project_overview", {"project_id": "PRJ-POOL"})["result"]["projects"][0]
    assert not [x for x in pool["risks"] if x.startswith("TKT-512")]  # Sarah has the history, so the risk is gone
    assert any(m["id"] == "sarah" for m in pool["members"]) and pool["due"] == "2026-12-01"
    n = graph(g).ro_query("MATCH (:Change)-[:FROM_SOURCE]->(:Source {id: 'SRC-t'}) RETURN count(*)").result_set[0][0]
    assert n == 3


def test_recalled_lessons_say_whether_they_come_from_an_earlier_conversation(g):
    graph(g).query("MERGE (:Session {id: 'SES-later', started_at: 'test'})")
    run_tool(g, "SES-test", "record_lesson", {"text": "Reconnect, don't pause, at peak", "service_ids": ["pgbouncer"]})
    later = run_tool(g, "SES-later", "recall_memory", {"service_ids": ["pgbouncer"]})["result"]["lessons"]
    same = run_tool(g, "SES-test", "recall_memory", {"service_ids": ["pgbouncer"]})["result"]["lessons"]
    pick = lambda ls: next(l for l in ls if l["text"].startswith("Reconnect"))  # noqa: E731
    assert pick(later)["earlier_session"] is True and pick(same)["earlier_session"] is False


def test_a_malformed_team_end_date_does_not_break_the_expert_lookup(g):
    graph(g).query("MATCH (p:Person {id: 'sarah'}), (t:Team {id: 'data'}) CREATE (p)-[:MEMBER_OF {from: '2024', to: ''}]->(t)")
    r = run_tool(g, "SES-test", "find_experts", {"service_id": "pgbouncer"})["result"]
    assert "error" not in r and r["experts"][0]["id"] == "sarah"


def test_a_fix_waits_for_approval_then_clears_only_what_the_root_cause_explains(g):
    from ravenhold.tools import decide_fix
    scenarios.inject(g, "pool-exhaustion")
    firing = lambda: graph(g).ro_query("MATCH (a:Alert {status: 'firing'}) RETURN count(a)").result_set[0][0]  # noqa: E731
    inc = run_tool(g, "SES-test", "declare_incident", {"title": "pool", "service_id": "payments-api", "sev": 1})["result"]["incident_id"]
    p = run_tool(g, "SES-test", "propose_fix", {"incident_id": inc, "service_id": "pgbouncer", "runbook_id": "RB-7"})["result"]
    assert p["status"] == "awaiting human approval" and p["alarms_it_should_clear"] == 12 and firing() == 13
    d = decide_fix(g, p["fix_id"], True)
    assert d["status"] == "applied" and d["cleared"] == 12 and d["recovered"]
    assert [a["service"] for a in d["still_firing_unrelated"]] == ["email-sender"]  # the noise alarm is untouched
    rec = run_tool(g, "SES-test", "check_recovery", {"incident_id": inc})["result"]
    assert rec["incident_status"] == "mitigated" and not rec["still_firing_related"]
    assert decide_fix(g, p["fix_id"], False)["status"] == "applied"  # a decision can't be flipped afterwards
