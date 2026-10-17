"""Cross-investigation memory.

Two layers, both stored in FalkorDB (graph `vedatrace_memory`, separate from the case graph):
  * Case store (always on when memory is on): one `Case` node per past investigation, with an embedding for semantic recall.
    Recalled cases are copied into the case graph as `PriorCase` nodes (RECALLED edge) so the Reporter can cite them like any other node.
  * Graphiti (optional, `graphiti-core[falkordb]`): each case is also ingested as an episode so Graphiti builds a temporal
    knowledge graph; `search` adds extra facts. If Graphiti cannot run (no LLM, flaky extraction, ...), the case store still works.

VEDATRACE_MEMORY = auto (default) | graphiti | simple | off
CLI: python -m memory.graphiti_mem status | seed-history [--no-graphiti] | remember --investigation INV-n | reset-investigations
"""
import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone

from agents import llm
from config import FALKOR_HOST, FALKOR_PORT, get_db, get_graph

MEM_GRAPH = os.environ.get("VEDATRACE_MEMORY_GRAPH", "vedatrace_memory")
GRAPHITI_GRAPH = os.environ.get("VEDATRACE_GRAPHITI_GRAPH", "vedatrace_graphiti")
GROUP_ID = GRAPHITI_GRAPH  # Graphiti maps group_id to a FalkorDB graph name, so this keeps it out of the case graph
GRAPHITI_TIMEOUT = int(os.environ.get("VEDATRACE_GRAPHITI_TIMEOUT", "900"))

HISTORY = [  # synthetic, clearly labelled demo history so that recall is visible on a fresh install
    dict(id="CASE-H1", title="Hub account fan-out ring (escalated)", outcome="ESCALATED",
         policies=["POL-STRUCTURING", "POL-RAPID-FANOUT", "POL-LAYERING", "POL-SHARED-DEVICE"],
         text="DEMO HISTORY. Alerted transfer of mid size passed rules. Sender shared one mobile device with two accounts of other "
              "customers. A device-linked hub account redistributed the funds to eight recipients within a day, every amount just below the "
              "review threshold, and the money came back through two intermediary accounts to an account on the same device: a circular flow. "
              "Patterns: shared device cluster, rapid fan-out, structuring, circular flow. Outcome: ESCALATED, SAR filed."),
    dict(id="CASE-H2", title="Household device and bill-splitting (cleared)", outcome="CLEAR",
         policies=[],
         text="DEMO HISTORY. Two accounts of different customers shared a tablet device and exchanged small equal transfers back and forth "
              "(household bill splitting, rent deposit refund). No fan-out, no structuring, amounts varied. Shared device alone was weak; "
              "benign explanation confirmed. Outcome: CLEAR."),
    dict(id="CASE-H3", title="Payroll account with large recurring fan-out (cleared)", outcome="CLEAR",
         policies=[],
         text="DEMO HISTORY. A business account paid dozens of employees in the same bursts every month with the same recipients and "
              "varied amounts. Looked like rapid fan-out but recurring payouts to an identical recipient set are a legitimate payroll "
              "explanation. Outcome: CLEAR."),
    dict(id="CASE-H4", title="New-account velocity cluster (monitor)", outcome="MONITOR",
         policies=["POL-NEW-ACCOUNT-VELOCITY"],
         text="DEMO HISTORY. Several accounts opened within weeks received funds and forwarded almost all of it within hours. No shared device, "
              "no cycle. Pattern: new account velocity only. Outcome: MONITOR for 60 days."),
]


# ---- helpers --------------------------------------------------------------------------
def preference() -> str:
    return os.environ.get("VEDATRACE_MEMORY", "auto").lower()


_ensured = False


def mem_graph():
    global _ensured
    g = get_db().select_graph(MEM_GRAPH)
    if not _ensured:  # read-only queries fail on a graph key that does not exist yet
        g.query("MERGE (:Meta {id:'memory'})")
        _ensured = True
    return g


def _cos(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


TOKEN = re.compile(r"[a-z]{4,}")


def _kw_score(q, text):
    a, b = set(TOKEN.findall(q.lower())), set(TOKEN.findall(text.lower()))
    return len(a & b) / (len(a | b) or 1)


# ---- simple case store ------------------------------------------------------------------
def store_case(case_id, title, text, outcome, policies, origin, investigation=None, episode_uuid=None):
    try:
        emb = llm.embed([text])[0]
    except Exception:
        emb = None
    mem_graph().query("MERGE (c:Case {id:$id}) SET c.title=$title, c.text=$text, c.outcome=$outcome, c.policies=$pol, c.origin=$origin, "
                      "c.investigation=$inv, c.embedding=$emb, c.episode_uuid=$ep, c.ts=timestamp()",
                      {"id": case_id, "title": title, "text": text, "outcome": outcome, "pol": policies, "origin": origin,
                       "inv": investigation or "", "emb": emb, "ep": episode_uuid or ""})


def list_cases():
    rows = mem_graph().ro_query("MATCH (c:Case) RETURN c.id, c.title, c.text, c.outcome, c.policies, c.origin, c.embedding, c.investigation").result_set
    return [dict(id=r[0], title=r[1], text=r[2], outcome=r[3], policies=r[4], origin=r[5], embedding=r[6], investigation=r[7]) for r in rows]


def recall_cases(query, k=3):
    cases = list_cases()
    if not cases:
        return []
    try:
        qe = llm.embed([query])[0]
        scored = [(_cos(qe, c["embedding"]) if c["embedding"] else _kw_score(query, c["text"]), c) for c in cases]
        method = "embedding"
    except Exception:
        scored = [(_kw_score(query, c["text"]), c) for c in cases]
        method = "keyword"
    scored.sort(key=lambda x: -x[0])
    top = scored[:k]
    if k > 1 and not any(c["origin"] == "investigation" for _, c in top):
        # Cases the system wrote itself must stay recallable: the best of them takes the last slot rather than being outranked by seeded history.
        own = [x for x in scored if x[1]["origin"] == "investigation"]
        if own:
            scored = top[:-1] + [own[0]]
        else:
            scored = top
    else:
        scored = top
    return [dict(id=c["id"], title=c["title"], text=c["text"], outcome=c["outcome"], origin=c["origin"], score=round(s, 3), method=method)
            for s, c in scored if s > 0.15]


# ---- Graphiti layer (optional) -----------------------------------------------------------
_graphiti_state = dict(client=None, error=None, tried=False)


def _patch_falkordb_fulltext():
    """graphiti-core 0.30.2 creates FalkorDB fulltext indexes with the pre-6.0 procedure signature, which the current
    falkordb/falkordb image (graph module 6.x) rejects. Use the Cypher DDL form instead (same indexes, same fields)."""
    from graphiti_core import graph_queries
    from graphiti_core.driver import falkordb_driver
    original = graph_queries.get_fulltext_indices

    def patched(provider):
        if provider == graph_queries.GraphProvider.FALKORDB:
            return ["CREATE FULLTEXT INDEX FOR (n:Episodic) ON (n.content, n.source, n.source_description, n.group_id)",
                    "CREATE FULLTEXT INDEX FOR (n:Entity) ON (n.name, n.summary, n.group_id)",
                    "CREATE FULLTEXT INDEX FOR (n:Community) ON (n.name, n.group_id)",
                    "CREATE FULLTEXT INDEX FOR ()-[e:RELATES_TO]-() ON (e.name, e.fact, e.group_id)"]
        return original(provider)

    graph_queries.get_fulltext_indices = patched
    falkordb_driver.get_fulltext_indices = patched
    try:
        from graphiti_core.driver.falkordb.operations import graph_ops
        graph_ops.get_fulltext_indices = patched
    except Exception:
        pass


def _build_graphiti():
    _patch_falkordb_fulltext()
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    class PassthroughReranker(CrossEncoderClient):  # keep first-stage (vector + BM25) ordering; no extra LLM calls
        async def rank(self, query, passages):
            return [(p, 1.0 / (i + 1)) for i, p in enumerate(passages)]

    if llm.provider() == "anthropic":
        from graphiti_core.llm_client.anthropic_client import AnthropicClient
        llm_client = AnthropicClient(config=LLMConfig(api_key=os.environ["VEDATRACE_API_KEY"], model=llm.ANTHROPIC_MODEL))
    elif llm.provider() == "openai":
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
        base = os.environ.get("OPENAI_BASE_URL") or None  # custom OpenAI-compatible endpoint (e.g. free Gemini) -> use its chat model
        gmodel = os.environ.get("OPENAI_GRAPHITI_MODEL") or (os.environ.get("OPENAI_MODEL") if base else None) or "gpt-4.1-mini"
        llm_client = OpenAIGenericClient(config=LLMConfig(api_key=os.environ["OPENAI_API_KEY"], model=gmodel, small_model=gmodel,
                                                         base_url=base, temperature=0.0, max_tokens=4096))
    else:
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
        llm_client = OpenAIGenericClient(config=LLMConfig(api_key="ollama", model=llm.model_name(), small_model=llm.model_name(),
                                                         base_url=f"{host}/v1", temperature=0.0, max_tokens=4096))
    if llm.provider() == "openai":
        base = os.environ.get("OPENAI_BASE_URL") or None
        emodel = os.environ.get("OPENAI_EMBED_MODEL") or ("gemini-embedding-001" if base else "text-embedding-3-small")
        embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(api_key=os.environ["OPENAI_API_KEY"], base_url=base, embedding_model=emodel,
                                                              embedding_dim=1024))  # longer vectors are truncated to 1024
    else:
        embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(api_key="ollama", base_url=f"{host}/v1",
                                                              embedding_model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                                                              embedding_dim=768))
    driver = FalkorDriver(host=FALKOR_HOST, port=FALKOR_PORT, database=GRAPHITI_GRAPH)
    return Graphiti(graph_driver=driver, llm_client=llm_client, embedder=embedder, cross_encoder=PassthroughReranker())


async def _graphiti_ready():
    """A fresh client per call: the async FalkorDB connection is bound to the event loop of asyncio.run()."""
    c = _build_graphiti()
    if not _graphiti_state["client"]:  # indexes persist in the database; create them once per process
        await c.build_indices_and_constraints()
        _graphiti_state["client"] = True
    return c


def graphiti_enabled() -> bool:
    return preference() in ("auto", "graphiti") and not _graphiti_state["error"]


def _run(coro, timeout):
    return asyncio.run(asyncio.wait_for(coro, timeout))


EXTRACTION_HINT = ("These are short AML case summaries. Extract every concrete entity (account types, devices, customer roles, behaviours such as "
                   "fan-out, structuring, circular flow, shared device, payroll) and the relationships between them, including the case outcome.")


async def _close_all(c):
    """Close the Graphiti client and the async HTTP clients it created, inside the running loop (avoids 'Event loop is closed' at exit)."""
    await c.close()
    for part in (getattr(c, "llm_client", None), getattr(c, "embedder", None)):
        cl = getattr(part, "client", None)
        if cl is not None and hasattr(cl, "close"):
            try:
                await cl.close()
            except Exception:
                pass


def graphiti_add(case_id, text, reference_time=None):
    """Ingest one case as an episode. Returns the episode uuid or None (never raises)."""
    if not graphiti_enabled():
        return None

    async def go():
        c = await _graphiti_ready()
        try:
            r = await c.add_episode(name=case_id, episode_body=text, source_description="VedaTrace case summary",
                                    reference_time=reference_time or datetime.now(timezone.utc), group_id=GROUP_ID,
                                    custom_extraction_instructions=EXTRACTION_HINT)
            return r.episode.uuid
        finally:
            await _close_all(c)

    try:
        return _run(go(), GRAPHITI_TIMEOUT)
    except Exception as e:
        _graphiti_state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"[memory] Graphiti ingestion failed, continuing with the case store only: {_graphiti_state['error']}", file=sys.stderr)
        return None


def graphiti_search(query, k=3):
    if not graphiti_enabled() or not _graphiti_has_data():
        return []

    async def go():
        c = await _graphiti_ready()
        try:
            return [e.fact for e in await c.search(query, group_ids=[GROUP_ID], num_results=k)]
        finally:
            await _close_all(c)

    try:
        _graphiti_state["tried"] = True
        return _run(go(), 120)
    except Exception as e:
        _graphiti_state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return []


def _graphiti_has_data() -> bool:
    try:
        r = get_db().select_graph(GRAPHITI_GRAPH).ro_query("MATCH (e:Episodic) RETURN count(e)").result_set
        return bool(r and r[0][0])
    except Exception:
        return False


# ---- public API -----------------------------------------------------------------------------
def status() -> dict:
    pref = preference()
    if pref == "off":
        return dict(enabled=False, backend=None, detail="disabled (VEDATRACE_MEMORY=off)")
    try:
        n = mem_graph().ro_query("MATCH (c:Case) RETURN count(c)").result_set[0][0]
    except Exception as e:
        return dict(enabled=False, backend=None, detail=f"FalkorDB unavailable: {type(e).__name__}")
    if n == 0:
        return dict(enabled=False, backend=None, detail="no cases stored yet; run: python -m memory.graphiti_mem seed-history")
    has_g = pref != "simple" and _graphiti_has_data() and not _graphiti_state["error"]
    return dict(enabled=True, backend="graphiti+cases" if has_g else "cases", cases=n,
                detail=None if has_g or pref == "simple" else "Graphiti has no ingested episodes; using the case store")


def alert_context(tx_id: str) -> str:
    """Neutral description of the alert built with the same read tools the Investigator has (no verdicts)."""
    from agents.tools import ToolBox, ToolError
    tb = ToolBox(get_graph(), None)
    try:
        tx = tb.get_transaction(tx_id)
        parts = [f"Alerted transfer of {tx['amount']} via {tx['channel']}, monitoring score {tx['monitor_score']}."]
        for role in ("sender", "receiver"):
            acc = tx[role]
            parts += [f"{role}: " + " ".join(tb.find_shared_devices(acc)["interpretation"]),
                      f"{role}: " + " ".join(tb.analyze_fanout(acc, 2).get("interpretation", []))]
        return " ".join(parts)
    except ToolError as e:
        return f"Alerted transaction {tx_id}. ({e})"


def recall(tx_id: str, k: int = 3) -> dict:
    """Returns dict(cases=[...], facts=[...], context=str). Never raises: memory must not break the core demo."""
    out = dict(cases=[], facts=[], context="")
    if preference() == "off":
        return out
    try:
        if mem_graph().ro_query("MATCH (c:Case) RETURN count(c)").result_set[0][0] == 0:
            seed_history(use_graphiti=False)  # fast, no LLM; Graphiti ingestion is a separate (slow) step
        ctx = alert_context(tx_id)
        out["context"] = ctx
        out["cases"] = recall_cases(ctx, k)
        if preference() != "simple":
            out["facts"] = graphiti_search(ctx, k)
    except Exception as e:
        print(f"[memory] recall skipped: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def attach_recall_to_investigation(g, inv_id, rec):
    """Copy recalled cases into the case graph as PriorCase nodes so the Reporter can read and cite them (graph-only coupling)."""
    for c in rec["cases"]:
        g.query("MATCH (i:Investigation {id:$i}) MERGE (p:PriorCase {id:$cid}) SET p.title=$t, p.summary=$s, p.outcome=$o, p.origin=$og, p.score=$sc "
                "MERGE (i)-[:RECALLED]->(p)",
                {"i": inv_id, "cid": c["id"], "t": c["title"], "s": c["text"], "o": c["outcome"], "og": c["origin"], "sc": c["score"]})
    g.query("MATCH (i:Investigation {id:$i}) SET i.recalled=$r, i.memory_facts=$f",
            {"i": inv_id, "r": json.dumps([f"{c['id']} ({c['outcome']}): {c['title']}" for c in rec["cases"]]), "f": json.dumps(rec["facts"])})


def prompt_block(rec) -> str:
    if not rec["cases"] and not rec["facts"]:
        return ""
    lines = [f"- {c['id']} [{c['outcome']}] {c['text']}" for c in rec["cases"]] + [f"- graph fact: {f}" for f in rec["facts"]]
    return "\n".join(lines)


def summarize_investigation(g, inv_id) -> dict:
    q = lambda c: g.ro_query(c, params={"i": inv_id}).result_set
    pats = q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(p:Pattern) RETURN p.pattern_type, p.summary")
    pols = [r[0] for r in q("MATCH (p:Pattern {investigation:$i})-[:VIOLATES]->(o:Policy) RETURN DISTINCT o.id")]
    rep = q("MATCH (r:Report)-[:ABOUT]->(:Investigation {id:$i}) RETURN r.recommendation ORDER BY r.created_ts DESC LIMIT 1")
    ben = [r[0] for r in q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(e:Evidence {kind:'BENIGN_EXPLANATION'}) RETURN e.summary")]
    ents = [r[0] for r in q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(:Evidence)-[:SUPPORTED_BY]->(n:Account) RETURN DISTINCT n.id LIMIT 12")]
    tx = q("MATCH (i:Investigation {id:$i}) RETURN i.target_tx")[0][0]
    outcome = rep[0][0] if rep else "UNKNOWN"
    text = (f"Case for alerted transaction {tx} (investigation {inv_id}). Patterns found: "
            + ("; ".join(f"{t}: {s}" for t, s in pats) or "none") + ". Policies matched: " + (", ".join(pols) or "none")
            + ". Ruled out: " + ("; ".join(ben) or "nothing recorded") + f". Key accounts: {', '.join(ents)}. Outcome: {outcome}.")
    return dict(id=f"CASE-{inv_id}", title=f"{inv_id}: {', '.join(t for t, _ in pats) or 'no patterns'}", text=text, outcome=outcome, policies=pols)


def remember(inv_id: str, g=None) -> dict:
    if preference() == "off":
        return dict(stored=False, reason="memory off")
    g = g or get_graph()
    c = summarize_investigation(g, inv_id)
    ep = graphiti_add(c["id"], c["text"]) if preference() != "simple" else None
    store_case(c["id"], c["title"], c["text"], c["outcome"], c["policies"], "investigation", inv_id, ep)
    return dict(stored=True, case=c["id"], graphiti=bool(ep))


def reset_investigation_memory():
    """Called on demo reset: forget cases that came from investigations in the (now deleted) case graph; keep the labelled demo history."""
    try:
        g = mem_graph()
        eps = [r[0] for r in g.ro_query("MATCH (c:Case {origin:'investigation'}) WHERE c.episode_uuid <> '' RETURN c.episode_uuid").result_set]
        g.query("MATCH (c:Case {origin:'investigation'}) DELETE c")
        if eps and graphiti_enabled():
            async def go():
                c = await _graphiti_ready()
                for u in eps:
                    await c.remove_episode(u)
            try:
                _run(go(), 300)
            except Exception:
                pass
    except Exception:
        pass


def seed_history(use_graphiti=True):
    for h in HISTORY:
        have = mem_graph().ro_query("MATCH (c:Case {id:$i}) RETURN c.episode_uuid", params={"i": h["id"]}).result_set
        ep = have[0][0] if have and have[0][0] else None
        if use_graphiti and not ep and preference() != "simple":
            t = time.time()
            ep = graphiti_add(h["id"], h["text"], datetime(2026, 5, 1, tzinfo=timezone.utc))
            print(f"[memory] {h['id']} graphiti episode: {'ok' if ep else 'skipped/failed'} ({time.time() - t:.0f}s)")
        store_case(h["id"], h["title"], h["text"], h["outcome"], h["policies"], "demo_history", None, ep)
    if not use_graphiti:
        return
    print(json.dumps(status()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "seed-history", "remember", "reset-investigations", "recall"])
    ap.add_argument("--investigation")
    ap.add_argument("--tx", default="TX-FLAGSHIP")
    ap.add_argument("--no-graphiti", action="store_true")
    a = ap.parse_args()
    if a.cmd == "status":
        print(json.dumps(status()))
    elif a.cmd == "seed-history":
        seed_history(not a.no_graphiti)
    elif a.cmd == "remember":
        print(json.dumps(remember(a.investigation)))
    elif a.cmd == "recall":
        r = recall(a.tx)
        print(json.dumps({k: r[k] for k in ("cases", "facts")}, indent=1)[:3000])
    else:
        reset_investigation_memory()
