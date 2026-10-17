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
    extra: dict | None = None  # provider passthrough that must be echoed back (Gemini 3 thought_signature)


@dataclass
class LLMResponse:
    text: str
    tool_calls: list = field(default_factory=list)


def provider() -> str:
    return os.environ.get("VEDATRACE_LLM_PROVIDER", "anthropic").lower()


def model_name() -> str:
    if provider() == "anthropic":
        return ANTHROPIC_MODEL
    if provider() == "openai":
        return os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
    return os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")


def chat(messages: list, tools: list | None = None, system: str | None = None, max_tokens: int = 2048) -> LLMResponse:
    if provider() == "anthropic":
        return _chat_anthropic(messages, tools, system, max_tokens)
    if provider() == "ollama":
        return _chat_ollama(messages, tools, system, max_tokens)
    if provider() == "openai":
        return _chat_openai(messages, tools, system, max_tokens)
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
    client = ollama.Client(host=host, timeout=float(os.environ.get("OLLAMA_TIMEOUT", "240")))
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
                           "num_ctx": int(os.environ.get("OLLAMA_NUM_CTX", "8192"))})
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
    think = os.environ.get("OLLAMA_THINK")  # "1"/"0" for models that support a thinking mode (qwen3); unset = model default
    if think in ("0", "1"):
        kwargs["think"] = think == "1"
    last = None
    for attempt in range(3):  # a stalled local server call must not hang the run forever
        try:
            r = client.chat(**kwargs)
            break
        except Exception as e:
            last = e
    else:
        raise last
    msg = r.message
    calls = []
    for i, tc in enumerate(msg.tool_calls or []):
        args = tc.function.arguments
        args = dict(args) if isinstance(args, dict) else {"_malformed": str(args)}
        calls.append(ToolCall(f"call_{i}", tc.function.name, args))
    return LLMResponse(msg.content or "", calls)


# ---- OpenAI (chat completions + tools; retries with backoff because of low rate limits) ----
def _chat_openai(messages, tools, system, max_tokens):
    import time

    from openai import APIConnectionError, BadRequestError, InternalServerError, OpenAI, RateLimitError

    # OPENAI_BASE_URL points this provider at any OpenAI-compatible endpoint (Groq, Gemini, OpenRouter, ...)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL") or None, max_retries=0, timeout=120)
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        elif m["role"] == "assistant":
            d = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                d["tool_calls"] = [{"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                                    **({"extra_content": tc.extra} if tc.extra else {})} for tc in m["tool_calls"]]
            out.append(d)
        else:
            out.append({"role": "user", "content": m["content"]})
    kwargs = dict(model=model_name(), messages=out, max_completion_tokens=max_tokens)
    if not os.environ.get("OPENAI_BASE_URL"):  # reasoning_effort is OpenAI-only; compatible endpoints may reject it
        kwargs["reasoning_effort"] = os.environ.get("OPENAI_REASONING_EFFORT", "none")  # "none" = cheap and allows function tools
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
    for attempt in range(6):
        try:
            r = client.chat.completions.create(**kwargs)
            break
        except (RateLimitError, InternalServerError, APIConnectionError):  # 429, transient 5xx ("high demand" 503 on free tiers), network blips
            if attempt == 5:
                raise
            time.sleep(min(60, 5 * 2 ** attempt))
        except BadRequestError as e:
            msg = str(e)
            if "max_completion_tokens" in msg and "max_completion_tokens" in kwargs:  # some OpenAI-compatible servers (Mistral) only know max_tokens
                kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
            elif "tool_use_failed" in msg and attempt < 3:  # strict servers (Groq) reject a hallucinated/malformed tool call with 400; a resample usually fixes it
                pass
            else:
                raise
    msg = r.choices[0].message
    calls = []
    for tc in msg.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except Exception:
            args = {"_malformed": tc.function.arguments}
        extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
        calls.append(ToolCall(tc.id, tc.function.name, args if isinstance(args, dict) else {"_malformed": str(args)}, extra))
    return LLMResponse(msg.content or "", calls)


# ---- embeddings (used only by the memory layer) -----------------------------------------
def embed(texts: list) -> list:
    """Local embeddings via Ollama (nomic-embed-text). Anthropic has no embedding endpoint, so memory uses Ollama for this in both modes."""
    base = os.environ.get("OPENAI_BASE_URL") or None
    emodel = os.environ.get("OPENAI_EMBED_MODEL") or (None if base else "text-embedding-3-small")  # custom endpoint without OPENAI_EMBED_MODEL -> Ollama
    if provider() == "openai" and emodel:
        from openai import OpenAI

        r = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=base, timeout=60).embeddings.create(model=emodel, input=list(texts))
        return [list(d.embedding)[:1024] for d in r.data]
    import ollama

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    r = ollama.Client(host=host, timeout=120).embed(model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"), input=list(texts), keep_alive=0)
    return [list(v) for v in r.embeddings]
