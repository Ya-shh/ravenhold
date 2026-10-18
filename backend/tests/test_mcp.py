"""The agents' tools come over MCP: tools/list gives them the catalogue, tools/call runs each step.
Spawns the real MCP server subprocess (stdio). No LLM calls, no cost."""

import uuid

import pytest

from ravenhold import mcp_bridge
from ravenhold.agents import COMMANDER_TOOLS, WORKER_TOOLS
from ravenhold.db import copy_graph, graph
from ravenhold.tools import TOOLS


@pytest.fixture()
def g():
    name = f"test-{uuid.uuid4().hex[:8]}"
    copy_graph("ravenhold", name)
    graph(name).query("MERGE (:Session {id: 'SES-test', started_at: 'test'})")
    yield name
    graph(name).delete()


def test_every_agent_tool_is_listed_over_mcp_without_context_fields():
    box = mcp_bridge.toolbox()
    assert box is not None, "MCP server did not start"
    assert set(box.tools) == set(TOOLS)
    for names in [COMMANDER_TOOLS, *WORKER_TOOLS.values()]:
        specs = box.specs(names)
        assert [s["name"] for s in specs] == names
        for s in specs:  # the model never sees (or picks) the workspace or session
            assert not {"tenant", "session_id"} & set(s["input_schema"]["properties"])


def test_tool_calls_over_mcp_return_the_full_trace_and_use_the_right_graph(g):
    out = mcp_bridge.call(g, "SES-test", "blast_radius", {"service_id": "pgbouncer"})
    assert out["via"] == "mcp" and out["cypher"] and out["subgraph"]["nodes"]
    assert out["result"]["arr_at_risk_usd"] > 0
    les = mcp_bridge.call(g, "SES-test", "record_lesson", {"text": "mcp write test", "service_ids": ["pgbouncer"]})
    assert les["via"] == "mcp"
    assert graph(g).ro_query("MATCH (l:Lesson {text: 'mcp write test'}) RETURN count(l)").result_set[0][0] == 1
    assert graph("ravenhold").ro_query("MATCH (l:Lesson {text: 'mcp write test'}) RETURN count(l)").result_set[0][0] == 0


def test_bad_arguments_over_mcp_come_back_as_an_error_the_model_can_fix(g):
    assert "error" in mcp_bridge.call(g, "SES-test", "blast_radius", {})["result"]
    assert "did you mean" in mcp_bridge.call(g, "SES-test", "blast_radius", {"service_id": "payment-db"})["result"]["error"]


def test_optional_arguments_over_mcp_behave_like_a_direct_call(g):
    # The SDK fills in defaults (service_id='') before the tool runs; that must not turn into an "unknown service".
    out = mcp_bridge.call(g, "SES-test", "project_overview", {"team_id": "platform"})
    assert "error" not in out["result"], out["result"]
    assert {p["id"] for p in out["result"]["projects"]} == {"PRJ-POOL", "PRJ-FRAUD-REDIS"}
    explicit = mcp_bridge.call(g, "SES-test", "project_overview", {"team_id": "platform", "service_id": ""})
    assert "error" not in explicit["result"]
