"""Chat providers with tool calling, normalised to one interface.

``step(messages, tools)`` sends the conversation and returns ``StepResult(text, tool_calls,
input_tokens, output_tokens, elapsed, raw_assistant, truncated)``; ``truncated`` means the model hit
``max_tokens`` (OpenAI finish_reason "length" / Anthropic stop_reason "max_tokens"). The loop (agent.py) appends tool results with
``tool_result_message(...)``. Messages are kept in OpenAI format internally; the Anthropic
provider converts both ways.

* ``OpenAICompatProvider`` — Groq / Cerebras / any OpenAI-compatible endpoint (``tools=``,
  ``tool_choice="required"``, optional ``reasoning_effort``).
* ``AnthropicProvider`` — Claude ``messages.create`` with ``tools=`` (input_schema), ``tool_choice={"type": "any"}``
  and ``tool_use`` / ``tool_result`` blocks.

Tool calls are forced on every step: the loop ends only through ``finish``, so prose is never wanted. A provider's
separate reasoning field (gpt-oss on Groq) is measured (``reasoning_chars``) but never stored.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Protocol

from .schema import ToolCall

MAX_TOKENS = 1500  # output tokens per step: a 3,000-character draft plus tool-call JSON (and gpt-oss reasoning) fits


class ProviderError(RuntimeError):
    pass


class QuotaError(ProviderError):
    """Daily token quota exhausted."""


@dataclass
class StepResult:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    raw_assistant: dict = field(default_factory=dict)  # OpenAI-format assistant message to append
    truncated: bool = False  # hit max_tokens: tool-call JSON may be cut (it then fails validation in the loop)
    reasoning_chars: int | None = None  # length of the provider's separate reasoning field, when it returns one


class Provider(Protocol):
    name: str
    model: str

    def step(self, messages: list[dict], tools: list[dict], max_tokens: int = MAX_TOKENS) -> StepResult: ...


def _wrap(exc: Exception) -> ProviderError:
    status = getattr(exc, "status_code", None)
    msg = str(exc)
    text = f"HTTP {status}: {msg}" if status else f"{type(exc).__name__}: {msg}"
    if status == 429 and any(k in msg.lower() for k in ("per day", "tpd", "daily")):
        return QuotaError(text)
    return ProviderError(text)


def tool_result_message(call_id: str, name: str, result) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": json.dumps(result, ensure_ascii=False, default=str)}


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, model: str, reasoning_effort: str = "", client=None) -> None:
        if client is None:
            from openai import OpenAI

            if not api_key:
                raise ProviderError("LLM_API_KEY is not set (see .env.example)")
            client = OpenAI(api_key=api_key, base_url=base_url, max_retries=2)
        self._client, self.model, self.reasoning_effort = client, model, reasoning_effort

    def step(self, messages: list[dict], tools: list[dict], max_tokens: int = MAX_TOKENS) -> StepResult:
        kwargs = dict(model=self.model, messages=messages, tools=tools, tool_choice="required", max_tokens=max_tokens, temperature=0)
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        t0 = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise _wrap(exc) from exc
        elapsed = time.perf_counter() - t0
        choice = resp.choices[0]
        m = choice.message
        calls = []
        raw_calls = []
        for tc in m.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
            raw_calls.append({"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}})
        raw = {"role": "assistant", "content": m.content or ""}
        if raw_calls:
            raw["tool_calls"] = raw_calls
        u = resp.usage
        reasoning = getattr(m, "reasoning", None)  # gpt-oss on Groq returns its reasoning here; we log only its length
        return StepResult(m.content or "", calls, int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0), elapsed, raw,
                          truncated=getattr(choice, "finish_reason", None) == "length",
                          reasoning_chars=len(reasoning) if isinstance(reasoning, str) else None)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, client=None) -> None:
        if client is None:
            import anthropic

            if not api_key:
                raise ProviderError("ANTHROPIC_API_KEY is not set (see .env.example)")
            client = anthropic.Anthropic(api_key=api_key)
        self._client, self.model = client, model

    @staticmethod
    def _tools(tools: list[dict]) -> list[dict]:
        return [{"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools]

    @staticmethod
    def _convert(messages: list[dict]) -> tuple[str, list[dict]]:
        system = ""
        out: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system += m["content"]
            elif m["role"] == "user":
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):  # e.g. the nudge right after tool results
                    out[-1]["content"].append({"type": "text", "text": m["content"]})
                else:
                    out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": json.loads(tc["function"]["arguments"] or "{}")})
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return system, out

    def step(self, messages: list[dict], tools: list[dict], max_tokens: int = MAX_TOKENS) -> StepResult:
        system, msgs = self._convert(messages)
        t0 = time.perf_counter()
        try:
            resp = self._client.messages.create(model=self.model, max_tokens=max_tokens, system=system, messages=msgs, tools=self._tools(tools),
                                                tool_choice={"type": "any"})
        except Exception as exc:
            raise _wrap(exc) from exc
        elapsed = time.perf_counter() - t0
        text, calls, raw_calls, thinking = "", [], [], None
        for b in resp.content:
            if b.type == "text":
                text += b.text
            elif b.type == "thinking":  # only if extended thinking is ever enabled; length only
                thinking = (thinking or 0) + len(getattr(b, "thinking", "") or "")
            elif b.type == "tool_use":
                calls.append(ToolCall(id=b.id, name=b.name, args=dict(b.input)))
                raw_calls.append({"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}})
        raw = {"role": "assistant", "content": text}
        if raw_calls:
            raw["tool_calls"] = raw_calls
        return StepResult(text, calls, resp.usage.input_tokens, resp.usage.output_tokens, elapsed, raw,
                          truncated=getattr(resp, "stop_reason", None) == "max_tokens", reasoning_chars=thinking)


def get_provider(cfg) -> Provider:
    if cfg.llm_provider == "anthropic":
        return AnthropicProvider(cfg.anthropic_api_key, cfg.anthropic_model)
    if cfg.llm_provider == "openai_compat":
        return OpenAICompatProvider(cfg.llm_api_key, cfg.llm_base_url, cfg.llm_model, cfg.llm_reasoning_effort)
    raise ProviderError(f"Unknown LLM_PROVIDER {cfg.llm_provider!r}")
