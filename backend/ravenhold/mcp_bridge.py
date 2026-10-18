"""The agents' tools come from MCP.

At startup the API launches `python -m ravenhold.mcp_server --agents` as a subprocess and connects to it over stdio.
The tool list each agent sees is the server's `tools/list` (names, descriptions, input schemas), filtered to that
agent's role, and every tool the model picks is executed with `tools/call`. The bridge only fills in `tenant` and
`session_id`, so each visitor's private graph is used; the model never sees or chooses those.

One MCP connection is shared by all agents. Calls from parallel workers are concurrent: the client multiplexes
requests over the connection and the server runs each tool in its own thread.

RAVENHOLD_TOOLS_VIA=direct skips MCP and calls the same functions in-process. If the MCP server can't start or dies,
tools fall back to in-process calls, and every step records which way it went (`via`), so the trace stays honest.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .tools import run_tool, tool_specs

log = logging.getLogger("ravenhold.mcp")
CONTEXT = ("tenant", "session_id")
ENABLED = os.getenv("RAVENHOLD_TOOLS_VIA", "mcp") != "direct"
RETRY_AFTER_S = 60
CALL_TIMEOUT_S = 60


class MCPToolbox:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.tools: dict = {}
        self.client = None
        threading.Thread(target=self._run, name="mcp-client", daemon=True).start()
        if not self.ready.wait(45):
            raise RuntimeError("MCP server did not start within 45 s")
        if self.error:
            raise RuntimeError(f"MCP server failed to start: {self.error}")

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self) -> None:
        from mcp import Client, StdioServerParameters
        params = StdioServerParameters(command=sys.executable, args=["-m", "ravenhold.mcp_server", "--agents"],
                                       env=dict(os.environ), cwd=str(Path(__file__).resolve().parents[1]))
        try:
            async with Client(params) as client:
                self.client = client
                self.tools = {t.name: t for t in (await client.list_tools()).tools}
                self.ready.set()
                await asyncio.Event().wait()  # keep the connection open for the life of the process
        except BaseException as e:  # startup failure, or the server process died later
            self.error = e
            self.client = None
            self.ready.set()

    @property
    def alive(self) -> bool:
        return self.client is not None and self.error is None

    def specs(self, names: list[str]) -> list[dict]:
        """Tool specs for the model, straight from the server's tools/list (context fields removed)."""
        out = []
        for n in names:
            t = self.tools.get(n)
            if not t:
                continue
            schema = json.loads(json.dumps(t.input_schema))
            schema["properties"] = {k: v for k, v in schema.get("properties", {}).items() if k not in CONTEXT}
            schema["required"] = [r for r in schema.get("required", []) if r not in CONTEXT]
            out.append({"name": n, "description": t.description or "", "input_schema": schema})
        return out

    def call(self, tenant: str, session_id: str, name: str, args: dict) -> dict:
        fut = asyncio.run_coroutine_threadsafe(
            self.client.call_tool(name, {"tenant": tenant, "session_id": session_id, **args},
                                  read_timeout_seconds=CALL_TIMEOUT_S), self.loop)
        res = fut.result(CALL_TIMEOUT_S + 5)
        text = "".join(getattr(c, "text", "") for c in res.content)
        if res.is_error:  # e.g. the model sent arguments that don't match the schema
            return {"result": {"error": text[:800]}, "cypher": [], "subgraph": {"nodes": [], "edges": []}}
        return json.loads(text)


_box: MCPToolbox | None = None
_failed_at = 0.0
_lock = threading.Lock()


def toolbox() -> MCPToolbox | None:
    """The shared MCP connection, started on first use. None when disabled or unavailable (retried after a minute)."""
    global _box, _failed_at
    if not ENABLED:
        return None
    with _lock:
        if _box and _box.alive:
            return _box
        if time.time() - _failed_at < RETRY_AFTER_S:
            return None
        try:
            _box = MCPToolbox()
            log.info("agents connected to the MCP server: %d tools", len(_box.tools))
            return _box
        except Exception as e:
            _box, _failed_at = None, time.time()
            log.warning("MCP unavailable, using in-process tools: %s", e)
            return None


def specs_for(names: list[str]) -> tuple[list[dict], str]:
    box = toolbox()
    return (box.specs(names), "mcp") if box else (tool_specs(names), "direct")


def call(tenant: str, session_id: str, name: str, args: dict) -> dict:
    box = toolbox()
    if box:
        try:
            return {**box.call(tenant, session_id, name, args), "via": "mcp"}
        except Exception as e:  # connection lost mid-call: answer in-process rather than fail the step
            log.warning("MCP call %s failed (%s); running it in-process", name, e)
    return {**run_tool(tenant, session_id, name, args), "via": "direct"}


def status() -> dict:
    box = toolbox()
    return {"transport": "mcp-stdio" if box else "direct", "tools": len(box.tools) if box else 0, "enabled": ENABLED}
