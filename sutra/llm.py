"""Talks to Ollama's native chat API and streams the reply back as events.

We use Ollama's own /api/chat endpoint (not the OpenAI-compatible one) because
it lets us switch qwen3's "thinking" on and off, which matters a lot for speed
on a CPU, and it returns tool calls as ready-to-use JSON objects.
"""

import json
from dataclasses import dataclass, field
from typing import Iterator

import httpx


class LLMError(Exception):
    """A problem talking to the model, with a message that is safe to show the user."""


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class Event:
    """One piece of a streamed reply.

    kind is one of:
      "thinking"   - part of the model's private reasoning (text)
      "text"       - part of the visible answer (text)
      "tool_calls" - the model wants to run tools (tool_calls)
      "done"       - the reply is finished (stats)
    """

    kind: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _parse_tool_calls(raw: list) -> list[ToolCall]:
    calls = []
    for item in raw or []:
        fn = item.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):  # some models send arguments as a JSON string
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
        calls.append(ToolCall(name=fn.get("name", ""), arguments=args or {}))
    return calls


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._no_think_models: set[str] = set()

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(
                f"Can't reach Ollama at {self.base_url}. Is it running? Try: ollama serve"
            ) from e
        return [m["name"] for m in r.json().get("models", [])]

    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        think: bool = False,
        num_ctx: int = 8192,
    ) -> Iterator[Event]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": num_ctx, "temperature": 0.2},
        }
        if tools:
            payload["tools"] = tools
        if model not in self._no_think_models:
            payload["think"] = think

        try:
            yield from self._stream(payload)
        except LLMError as e:
            # Models without a thinking mode reject the "think" field: retry once without it.
            if "think" in payload and "think" in str(e).lower():
                self._no_think_models.add(model)
                payload.pop("think")
                yield from self._stream(payload)
            else:
                raise

    def _stream(self, payload: dict) -> Iterator[Event]:
        timeout = httpx.Timeout(self.timeout, connect=5)
        try:
            with httpx.stream("POST", f"{self.base_url}/api/chat", json=payload, timeout=timeout) as r:
                if r.status_code != 200:
                    r.read()
                    try:
                        detail = r.json().get("error", r.text)
                    except ValueError:
                        detail = r.text
                    if r.status_code == 404 and "not found" in detail:
                        detail += f". Download it with: ollama pull {payload['model']}"
                    raise LLMError(f"Ollama error: {detail}")

                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise LLMError(f"Ollama error: {data['error']}")
                    msg = data.get("message", {})
                    if msg.get("thinking"):
                        yield Event("thinking", text=msg["thinking"])
                    if msg.get("content"):
                        yield Event("text", text=msg["content"])
                    if msg.get("tool_calls"):
                        yield Event("tool_calls", tool_calls=_parse_tool_calls(msg["tool_calls"]))
                    if data.get("done"):
                        yield Event("done", stats=_stats(data))
        except httpx.ConnectError as e:
            raise LLMError(
                f"Can't reach Ollama at {self.base_url}. Is it running? Try: ollama serve"
            ) from e
        except httpx.ReadTimeout as e:
            raise LLMError("The model took too long to respond (timed out).") from e


def _stats(data: dict) -> dict:
    """Token counts and speed, from Ollama's final message."""
    out_tokens = data.get("eval_count", 0)
    out_secs = data.get("eval_duration", 0) / 1e9
    return {
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": out_tokens,
        "tokens_per_sec": round(out_tokens / out_secs, 1) if out_secs else 0.0,
        "total_secs": round(data.get("total_duration", 0) / 1e9, 1),
    }
