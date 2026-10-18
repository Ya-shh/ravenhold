"""Pluggable LLM backend. The agents only see `LLM.call()` and normalized replies.

LLM_PROVIDER=openai     any OpenAI-compatible endpoint: Groq, OpenRouter, Together, Ollama, vLLM...
                        needs LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
LLM_PROVIDER=anthropic  Claude via the Anthropic SDK (needs ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field

from dotenv import load_dotenv

from . import usage

load_dotenv()


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    error: str | None = None  # set when the model produced unparseable arguments


@dataclass
class Reply:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    refused: bool = False
    raw: object = None
    cost: dict | None = None  # {model, tokens_in, tokens_out, usd} for this one model call


@dataclass
class ToolResult:
    id: str
    name: str
    content: str
    is_error: bool = False


class OpenAICompatible:
    def __init__(self):
        from openai import OpenAI

        self.client = OpenAI(
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ.get("LLM_API_KEY", "none"),
            max_retries=2,  # 429s are handled by _create_with_backoff
            timeout=120,
        )
        self.model = os.environ["LLM_MODEL"]
        self.worker_model = os.getenv("LLM_WORKER_MODEL", self.model)
        self.temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "4096"))
        # For reasoning models that accept it (e.g. gpt-oss): low | medium | high
        self.effort = os.getenv("LLM_REASONING_EFFORT")
        self.worker_effort = os.getenv("LLM_WORKER_REASONING_EFFORT", self.effort or "")
        self.name = f"{self.model} @ {os.environ['LLM_BASE_URL']}"

    def start(self, system: str, history: list[dict]) -> list:
        return [{"role": "system", "content": system}, *history]

    def call(self, messages: list, tools: list[dict], worker: bool) -> Reply:
        specs = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                   "parameters": t["input_schema"]}} for t in tools]
        effort = self.worker_effort if worker else self.effort
        resp = self._create_with_backoff(
            "worker" if worker else "commander",
            model=self.worker_model if worker else self.model,
            messages=messages,
            tools=specs or None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **({"reasoning_effort": effort} if effort else {}),
        )
        model = self.worker_model if worker else self.model
        cost = None
        if resp.usage:
            tin, tout = resp.usage.prompt_tokens, resp.usage.completion_tokens
            usd = usage.record(model, "worker" if worker else "commander", tin, tout)
            cost = {"model": model, "tokens_in": tin, "tokens_out": tout, "usd": usd}
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args)
                err = None if isinstance(args, dict) else "arguments must be a JSON object"
            except json.JSONDecodeError as e:
                args, err = {}, f"invalid JSON arguments: {e}"
            calls.append(ToolCall(tc.id or f"call_{uuid.uuid4().hex[:8]}", tc.function.name, args if isinstance(args, dict) else {}, err))
        return Reply(text=msg.content or "", calls=calls, refused=bool(getattr(msg, "refusal", None)), raw=msg, cost=cost)

    def _create_with_backoff(self, role: str, **kwargs):
        """Tokens-per-minute limits (free tiers) say 'try again in 16.9s'; wait that long instead of failing."""
        import re
        import time

        from openai import RateLimitError

        usage.check_budget()
        for attempt in range(8):
            try:
                return self.client.chat.completions.create(**kwargs)
            except RateLimitError as e:
                if attempt == 7:
                    raise
                m = re.search(r"try again in ([\d.]+)s", str(e))
                time.sleep(min(float(m.group(1)) + 0.5 if m else 5 * (attempt + 1), 60))

    def add_assistant(self, messages: list, reply: Reply) -> None:
        m: dict = {"role": "assistant", "content": reply.text or None}
        if reply.calls:
            m["tool_calls"] = [{"id": c.id, "type": "function",
                                "function": {"name": c.name, "arguments": json.dumps(c.input)}} for c in reply.calls]
        messages.append(m)

    def add_tool_results(self, messages: list, results: list[ToolResult]) -> None:
        for r in results:
            messages.append({"role": "tool", "tool_call_id": r.id,
                             "content": ("ERROR: " if r.is_error else "") + r.content})


class Anthropic:
    # Models that accept the server-side refusal fallback (`fallbacks: "default"`). Haiku 5.5 has none.
    FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

    def __init__(self):
        import anthropic

        self.client = anthropic.Anthropic(max_retries=4)  # backs off on 429 / 529
        self.model = os.getenv("RAVENHOLD_MODEL", "claude-sonnet-5-5")
        self.worker_model = os.getenv("RAVENHOLD_WORKER_MODEL", "claude-haiku-5-5")
        self.effort = os.getenv("RAVENHOLD_COMMANDER_EFFORT", "low")  # eval: same accuracy as medium, faster and cheaper
        self.worker_effort = os.getenv("RAVENHOLD_WORKER_EFFORT", "low")
        self.system = ""
        self.name = self.model

    def start(self, system: str, history: list[dict]) -> list:
        self.system = system  # one adapter instance per tool loop, see agents.make_llm()
        return list(history)

    def call(self, messages: list, tools: list[dict], worker: bool) -> Reply:
        model = self.worker_model if worker else self.model
        fallback = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if model in self.FALLBACK_MODELS else {}
        usage.check_budget()
        resp = self.client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=self.system,
            tools=tools,
            messages=messages,
            output_config={"effort": self.worker_effort if worker else self.effort},
            cache_control={"type": "ephemeral"},
            **fallback,
        )
        u = resp.usage
        tin = u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
        usd = usage.record(resp.model or model, "worker" if worker else "commander", u.input_tokens, u.output_tokens,
                           u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0)
        cost = {"model": resp.model or model, "tokens_in": tin, "tokens_out": u.output_tokens, "usd": usd}
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Reply(text=text, calls=calls, refused=resp.stop_reason == "refusal", raw=resp, cost=cost)

    def add_assistant(self, messages: list, reply: Reply) -> None:
        messages.append({"role": "assistant", "content": reply.raw.content})  # keep thinking blocks unchanged

    def add_tool_results(self, messages: list, results: list[ToolResult]) -> None:
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.id, "content": r.content, **({"is_error": True} if r.is_error else {})}
            for r in results
        ]})


def active_models() -> dict:
    """Which model each agent role uses right now (shown to users for transparency)."""
    if provider() == "openai":
        main = os.getenv("LLM_MODEL", "")
        return {"provider": "OpenAI-compatible", "commander": main, "workers": os.getenv("LLM_WORKER_MODEL", main)}
    main = os.getenv("RAVENHOLD_MODEL", "claude-sonnet-5-5")
    return {"provider": "Anthropic", "commander": main, "workers": os.getenv("RAVENHOLD_WORKER_MODEL", "claude-haiku-5-5")}


def provider() -> str:
    return os.getenv("LLM_PROVIDER") or ("openai" if os.getenv("LLM_BASE_URL") else "anthropic")


def make_llm():
    return OpenAICompatible() if provider() == "openai" else Anthropic()
