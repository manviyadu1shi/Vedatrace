"""The ONLY place provider SDKs are imported.  chat(messages, tools) -> LLMResponse.

Messages use a neutral format:
  {"role": "user"|"assistant"|"tool", "content": str, "tool_calls": [ToolCall...] (assistant), "tool_call_id": str (tool)}
Tools use a neutral format: {"name", "description", "parameters": <JSON schema>}.
"""
import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_MODEL = "claude-sonnet-5-5"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list = field(default_factory=list)


def provider() -> str:
    return os.environ.get("VEDATRACE_LLM_PROVIDER", "anthropic").lower()


def model_name() -> str:
    return ANTHROPIC_MODEL if provider() == "anthropic" else os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")


def chat(messages: list, tools: list | None = None, system: str | None = None, max_tokens: int = 2048) -> LLMResponse:
    if provider() == "anthropic":
        return _chat_anthropic(messages, tools, system, max_tokens)
    if provider() == "ollama":
        return _chat_ollama(messages, tools, system, max_tokens)
    raise ValueError(f"Unknown VEDATRACE_LLM_PROVIDER={provider()!r}")


# ---- Anthropic -----------------------------------------------------------
def _chat_anthropic(messages, tools, system, max_tokens):
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["VEDATRACE_API_KEY"])
    out, pending_results = [], []

    def flush():
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        if m["role"] == "tool":
            pending_results.append({"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]})
            continue
        if m["role"] == "user" and pending_results:  # user text right after tool results shares the same user turn
            out.append({"role": "user", "content": list(pending_results) + [{"type": "text", "text": m["content"]}]})
            pending_results.clear()
            continue
        flush()
        if m["role"] == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "."}]})
        else:
            out.append({"role": "user", "content": m["content"]})
    flush()
    kwargs = dict(model=ANTHROPIC_MODEL, max_tokens=max_tokens, messages=out)
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    r = client.messages.create(**kwargs)
    text = "".join(b.text for b in r.content if b.type == "text")
    calls = [ToolCall(b.id, b.name, dict(b.input)) for b in r.content if b.type == "tool_use"]
    return LLMResponse(text, calls)


# ---- Ollama (native client: lets us set num_ctx, which the /v1 endpoint ignores) ----
def _chat_ollama(messages, tools, system, max_tokens):
    import ollama

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    client = ollama.Client(host=host, timeout=900)
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "tool", "content": m["content"]})
        elif m["role"] == "assistant":
            d = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                d["tool_calls"] = [{"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m["tool_calls"]]
            out.append(d)
        else:
            out.append({"role": "user", "content": m["content"]})
    kwargs = dict(model=model_name(), messages=out, stream=False,
                  options={"temperature": 0.2, "num_predict": max_tokens,
                           "num_ctx": int(os.environ.get("OLLAMA_NUM_CTX", "12288"))})
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
    think = os.environ.get("OLLAMA_THINK")  # "1"/"0" for models that support a thinking mode (qwen3); unset = model default
    if think in ("0", "1"):
        kwargs["think"] = think == "1"
    r = client.chat(**kwargs)
    msg = r.message
    calls = []
    for i, tc in enumerate(msg.tool_calls or []):
        args = tc.function.arguments
        args = dict(args) if isinstance(args, dict) else {"_malformed": str(args)}
        calls.append(ToolCall(f"call_{i}", tc.function.name, args))
    return LLMResponse(msg.content or "", calls)


# ---- embeddings (used only by the memory layer) -----------------------------------------
def embed(texts: list) -> list:
    """Local embeddings via Ollama (nomic-embed-text). Anthropic has no embedding endpoint, so memory uses Ollama for this in both modes."""
    import ollama

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    r = ollama.Client(host=host, timeout=120).embed(model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"), input=list(texts))
    return [list(v) for v in r.embeddings]
