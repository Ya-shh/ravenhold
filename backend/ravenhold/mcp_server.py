"""MCP server exposing the Ravenhold graph tools.

Two modes, same tools:

    uv run python -m ravenhold.mcp_server            # for any MCP client (Claude Desktop, Claude Code, Cursor...)
    claude mcp add ravenhold -- uv --directory <repo>/backend run python -m ravenhold.mcp_server

    uv run python -m ravenhold.mcp_server --agents   # for Ravenhold's own agents (see mcp_bridge.py)

Client mode binds one workspace (RAVENHOLD_TENANT) and returns result + Cypher. Agent mode is what the Commander,
Investigator and Historian use: every tool also takes `tenant` and `session_id` (filled in by the bridge, never by the
model, so each visitor's private graph is used) and returns the full trace (result, Cypher, highlighted subgraph).
"""

import functools
import inspect
import json
import os
import sys

from mcp.server.mcpserver import MCPServer

from .tools import NEEDS_SESSION, TOOLS, run_tool

TENANT = os.getenv("RAVENHOLD_TENANT", "ravenhold")
AGENT_MODE = "--agents" in sys.argv
CONTEXT = ("tenant", "session_id")  # injected by the bridge, hidden from the model
mcp = MCPServer("ravenhold")


def _wrap(name, fn):
    """Bind the tenant, keep the rest of the signature so MCPServer infers the input schema."""
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters.values() if p.name != "tenant"]
    if name in NEEDS_SESSION:
        params = [p.replace(default="mcp") if p.name == "session_id" else p for p in params]
        # session_id must come after required params once it has a default
        params = [p for p in params if p.default is inspect.Parameter.empty] + \
                 [p for p in params if p.default is not inspect.Parameter.empty]

    @functools.wraps(fn)
    def tool(**kwargs):
        if name in NEEDS_SESSION:
            kwargs.setdefault("session_id", "mcp")
            _ensure_mcp_session()
        out = fn(TENANT, **kwargs)
        return {"result": out["result"], "cypher": out["cypher"]}

    tool.__signature__ = sig.replace(parameters=params, return_annotation=dict)
    tool.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k != "tenant"}
    tool.__annotations__["return"] = dict
    return tool


def _wrap_for_agents(name, fn):
    """tenant + session_id first, then the tool's own parameters. Goes through run_tool, so id checks and error
    handling are exactly the same as before; the reply is the whole trace as JSON."""
    sig = inspect.signature(fn)
    ctx = [inspect.Parameter(n, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str) for n in CONTEXT]
    own = [p for p in sig.parameters.values() if p.name not in CONTEXT]
    defaults = {p.name: p.default for p in own if p.default is not inspect.Parameter.empty}

    def tool(tenant: str, session_id: str, **kwargs) -> str:
        # The SDK fills in every default before calling us; pass only what the model actually set, so a call over MCP
        # behaves exactly like a direct one (e.g. service_id='' must not reach the id check).
        sent = {k: v for k, v in kwargs.items() if not (k in defaults and v == defaults[k])}
        return json.dumps(run_tool(tenant, session_id, name, sent), default=str)

    tool.__name__ = name
    tool.__signature__ = sig.replace(parameters=ctx + own, return_annotation=str)
    tool.__annotations__ = {**{k: v for k, v in fn.__annotations__.items() if k not in CONTEXT},
                            "tenant": str, "session_id": str, "return": str}
    return tool


def _ensure_mcp_session():
    from .db import graph
    graph(TENANT).query("MERGE (s:Session {id: 'mcp'}) ON CREATE SET s.started_at = 'mcp'")


for _name, (_fn, _desc, _schema) in TOOLS.items():
    if AGENT_MODE:
        mcp.add_tool(_wrap_for_agents(_name, _fn), name=_name, description=_desc, structured_output=False)
        # Advertise the hand-written schema (field descriptions, enums) plus the context fields. Validation still
        # uses the function signature, which has the same parameter names.
        advertised = json.loads(json.dumps(_schema))
        advertised["properties"] = {"tenant": {"type": "string"}, "session_id": {"type": "string"}, **advertised["properties"]}
        advertised["required"] = [*CONTEXT, *advertised.get("required", [])]
        mcp._tool_manager.get_tool(_name).parameters = advertised
    else:
        mcp.add_tool(_wrap(_name, _fn), name=_name, description=_desc)


if __name__ == "__main__":
    mcp.run()
