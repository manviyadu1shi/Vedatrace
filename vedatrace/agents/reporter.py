"""Reporter: reads an investigation from FalkorDB (only input: investigation id) and writes an auditable report.

No import of agents.investigator, no shared in-memory state. Structured sections are rendered deterministically from the graph
so ids are exact; the LLM maps Patterns to Policies and writes the narrative. Every [CITATION] is verified against the graph.
"""
import argparse
import json
import os
import re
import sys

from agents import llm
from config import get_graph

CITE_RE = re.compile(r"\[([A-Z]{2,5}-[A-Za-z0-9][A-Za-z0-9\-]*)\]")
RECOMMENDATIONS = ["ESCALATE", "MONITOR", "CLEAR"]
HUMAN_NOTE = ("**A human analyst makes the final decision.** This report is decision support generated from graph evidence; "
              "it does not constitute a determination of wrongdoing.")

LINK_TOOL = dict(
    name="link_policy",
    description="Record that a Pattern violates / is relevant to a Policy. Call once per (pattern, policy) match. "
                "Only link policies whose description genuinely fits the pattern.",
    parameters=dict(type="object", properties=dict(
        pattern_id={"type": "string"}, policy_id={"type": "string"},
        rationale={"type": "string", "description": "Why this policy applies, citing the evidence."},
        confidence={"type": "number", "description": "0..1"}),
        required=["pattern_id", "policy_id", "rationale", "confidence"]))

REPORT_TOOL = dict(
    name="submit_report",
    description="Submit the narrative parts of the report. Cite graph ids in square brackets, e.g. [EV-3], [PAT-1], [POL-LAYERING], "
                "[ACC-1234], [TX-00012]. Use ONLY ids given in the context. Every factual sentence must carry a citation.",
    parameters=dict(type="object", properties=dict(
        executive_summary={"type": "string", "description": "3-5 sentences."},
        analysis={"type": "string", "description": "How the patterns connect to each other and to the alerted transaction."},
        recommendation={"type": "string", "enum": RECOMMENDATIONS},
        confidence={"type": "number", "description": "0..1 confidence in the recommendation"},
        recommendation_rationale={"type": "string"}),
        required=["executive_summary", "analysis", "recommendation", "confidence", "recommendation_rationale"]))


class ReportError(Exception):
    pass


# ---- graph reads --------------------------------------------------------------
def load_case(g, inv_id):
    q = lambda c, p=None: g.ro_query(c, params=p or {}).result_set
    r = q("MATCH (i:Investigation {id:$id}) RETURN i.id, i.target_tx, i.status, i.summary, i.provider, i.model, i.started_ts, i.memory_used",
          {"id": inv_id})
    if not r:
        raise ReportError(f"Investigation '{inv_id}' not found in the graph.")
    i = r[0]
    case = dict(id=i[0], target_tx=i[1], status=i[2], summary=i[3], provider=i[4], model=i[5], started_ts=i[6], memory_used=i[7])
    case["steps"] = [dict(id=s[0], seq=s[1], tool=s[2], args=s[3], reason=s[4], ok=s[5], result=s[6]) for s in q(
        "MATCH (:Investigation {id:$id})-[:HAS_STEP]->(s:Step) RETURN s.id, s.seq, s.tool, s.args, s.reason, s.ok, s.result_summary ORDER BY s.seq", {"id": inv_id})]
    ev = q("MATCH (:Investigation {id:$id})-[:HAS_STEP]->(s:Step)-[:PRODUCED]->(e:Evidence) "
           "OPTIONAL MATCH (e)-[:SUPPORTED_BY]->(n) RETURN e.id, e.kind, e.summary, e.confidence, s.id, collect(n.id) ORDER BY toInteger(substring(e.id,3))",
           {"id": inv_id})
    case["evidence"] = [dict(id=e[0], kind=e[1], summary=e[2], confidence=e[3], step=e[4], supports=e[5]) for e in ev]
    pt = q("MATCH (:Investigation {id:$id})-[:HAS_STEP]->(s:Step)-[:PRODUCED]->(p:Pattern) "
           "OPTIONAL MATCH (p)-[:BACKED_BY]->(e:Evidence) RETURN p.id, p.pattern_type, p.summary, p.confidence, s.id, collect(e.id) ORDER BY toInteger(substring(p.id,4))",
           {"id": inv_id})
    case["patterns"] = [dict(id=p[0], type=p[1], summary=p[2], confidence=p[3], step=p[4], evidence=p[5]) for p in pt]
    case["policies"] = [dict(id=p[0], title=p[1], description=p[2], severity=p[3]) for p in q(
        "MATCH (p:Policy) RETURN p.id, p.title, p.description, p.severity ORDER BY p.id")]
    case["prior_cases"] = [dict(id=r[0], title=r[1], summary=r[2], outcome=r[3], origin=r[4]) for r in q(
        "MATCH (:Investigation {id:$id})-[:RECALLED]->(p:PriorCase) RETURN p.id, p.title, p.summary, p.outcome, p.origin ORDER BY p.score DESC", {"id": inv_id})]
    tx = q("MATCH (a:Account)-[:SENT]->(t:Transaction {id:$t})-[:TO]->(b:Account) RETURN a.id, b.id, t.amount, t.ts", {"t": case["target_tx"]})
    case["target"] = dict(sender=tx[0][0], receiver=tx[0][1], amount=tx[0][2], ts=tx[0][3]) if tx else None
    return case


def resolve(g, node_id):
    """True if the cited id is a real node."""
    m = [("EV-", "Evidence"), ("PAT-", "Pattern"), ("POL-", "Policy"), ("ACC-", "Account"), ("TX-", "Transaction"),
         ("DEV-", "Device"), ("CUST-", "Customer"), ("RPT-", "Report"), ("CASE-", "PriorCase")]
    for pre, lab in m:
        if node_id.startswith(pre):
            return bool(g.ro_query(f"MATCH (n:{lab} {{id:$id}}) RETURN n.id", params={"id": node_id}).result_set)
    if node_id.startswith("INV-"):
        lab = "Step" if re.search(r"-S\d+$", node_id) else "Investigation"
        return bool(g.ro_query(f"MATCH (n:{lab} {{id:$id}}) RETURN n.id", params={"id": node_id}).result_set)
    return False


def check_citations(g, *texts):
    cited = {c for t in texts for c in CITE_RE.findall(t or "")}
    return sorted(c for c in cited if not resolve(g, c)), sorted(cited)


# ---- LLM stages -----------------------------------------------------------------
def context_text(case, mappings=None):
    lines = [f"Investigation {case['id']} of alerted transaction {case['target_tx']} "
             f"(sender {case['target']['sender']}, receiver {case['target']['receiver']}, amount {case['target']['amount']})."
             if case["target"] else f"Investigation {case['id']}"]
    lines.append(f"Investigator's own summary: {case['summary']}")
    lines.append("\nEVIDENCE:")
    for e in case["evidence"]:
        lines.append(f"[{e['id']}] kind={e['kind']} conf={e['confidence']} supports={e['supports']}: {e['summary']}")
    lines.append("\nPATTERNS:")
    for p in case["patterns"]:
        lines.append(f"[{p['id']}] type={p['type']} conf={p['confidence']} backed_by={p['evidence']}: {p['summary']}")
    if case.get("prior_cases"):
        lines.append("\nPRIOR CASES RECALLED FROM MEMORY (historical context, not evidence about this transaction):")
        for c in case["prior_cases"]:
            lines.append(f"[{c['id']}] outcome={c['outcome']} {c['title']}: {c['summary'][:400]}")
    if mappings:
        lines.append("\nPOLICY MATCHES:")
        for m in mappings:
            lines.append(f"[{m['pattern']}] -> [{m['policy']}] ({m['rationale']})")
    return "\n".join(lines)


def map_policies(g, case):
    """LLM maps Patterns to Policies via tool calls; persisted as VIOLATES edges with rationale."""
    if not case["patterns"]:
        return []
    pols = "\n".join(f"[{p['id']}] {p['title']} (severity {p['severity']}): {p['description']}" for p in case["policies"])
    pids = {p["id"] for p in case["policies"]}
    patids = {p["id"] for p in case["patterns"]}
    prompt = (context_text(case) + "\n\nPOLICIES:\n" + pols +
              "\n\nFor each Pattern, call link_policy once for every Policy it genuinely violates (a pattern may match several policies, "
              "or none). Base the rationale only on the evidence above.")
    history, out = [dict(role="user", content=prompt)], []
    for _ in range(3):
        resp = llm.chat(history, [LINK_TOOL], system="You are a compliance analyst mapping detected patterns to policies.", max_tokens=1500)
        if not resp.tool_calls:
            if out:
                break
            history += [dict(role="assistant", content=resp.text or "ok"), dict(role="user", content="Call link_policy for each matching (pattern, policy) pair.")]
            continue
        history.append(dict(role="assistant", content=resp.text, tool_calls=resp.tool_calls))
        for tc in resp.tool_calls:
            a = tc.arguments
            pat, pol = str(a.get("pattern_id", "")), str(a.get("policy_id", ""))
            if pat in patids and pol in pids:
                conf = max(0.0, min(float(a.get("confidence", 0.5) or 0.5), 1.0))
                rat = str(a.get("rationale", ""))[:600]
                if not any(m["pattern"] == pat and m["policy"] == pol for m in out):
                    g.query("MATCH (p:Pattern {id:$p}), (o:Policy {id:$o}) MERGE (p)-[v:VIOLATES]->(o) SET v.rationale=$r, v.confidence=$c",
                            {"p": pat, "o": pol, "r": rat, "c": conf})
                    out.append(dict(pattern=pat, policy=pol, rationale=rat, confidence=conf))
                res = "linked"
            else:
                res = f"error: unknown pattern or policy id. Patterns: {sorted(patids)}. Policies: {sorted(pids)}"
            history.append(dict(role="tool", tool_call_id=tc.id, content=res))
        if len(out) >= 1 and all(any(m["pattern"] == p for m in out) for p in patids):
            break
    return out


def write_narrative(g, case, mappings):
    prompt = (context_text(case, mappings) +
              "\n\nPolicies available: " + ", ".join(f"[{p['id']}]" for p in case["policies"]) +
              "\n\nWrite the narrative for the compliance report by calling submit_report. Cite ids in square brackets after each claim. "
              "Mention what was ruled out as benign only if BENIGN_EXPLANATION evidence exists. Do not claim more than the evidence supports."
              + (" If prior cases are listed, add one sentence in the analysis comparing this case to the most similar prior case, citing it as [CASE-...]; "
                 "prior cases are context, never evidence." if case.get("prior_cases") else ""))
    system = "You write precise, auditable AML investigation reports grounded ONLY in the provided graph records."
    allowed = None
    for attempt in range(2):
        msgs = [dict(role="user", content=prompt)]
        if attempt:
            msgs[0]["content"] += f"\n\nYour previous attempt cited ids that do not exist: {allowed}. Use only ids listed above."
        resp = llm.chat(msgs, [REPORT_TOOL], system=system, max_tokens=2500)
        calls = [c for c in resp.tool_calls if c.name == "submit_report"]
        if not calls:
            allowed = "(no submit_report call was made)"
            continue
        a = calls[0].arguments
        if a.get("recommendation") not in RECOMMENDATIONS or not a.get("executive_summary"):
            allowed = "(missing/invalid recommendation or summary)"
            continue
        bad, _ = check_citations(g, a.get("executive_summary"), a.get("analysis"), a.get("recommendation_rationale"))
        if not bad:
            return a
        allowed = bad
    raise ReportError(f"Reporter could not produce a report with resolvable citations (last problem: {allowed}).")


# ---- rendering ---------------------------------------------------------------------
def render_markdown(case, mappings, narrative):
    L = []
    t = case["target"]
    L.append(f"# AML Investigation Report: {case['target_tx']}")
    L.append(f"*Investigation {case['id']} · status {case['status']} · produced by {case['provider']}/{case['model']}*  ")
    if t:
        L.append(f"*Alerted transaction {case['target_tx']}: {t['sender']} → {t['receiver']}, amount {t['amount']}*")
    L.append("")
    L.append(f"## Recommendation: **{narrative['recommendation']}** (confidence {float(narrative['confidence']):.2f})")
    L.append(narrative["recommendation_rationale"])
    L.append("")
    L.append(HUMAN_NOTE)
    L.append("")
    L.append("## Executive summary")
    L.append(narrative["executive_summary"])
    L.append("")
    L.append("## Findings")
    if not case["patterns"]:
        L.append("No fraud patterns were recorded by the Investigator.")
    ev_by_id = {e["id"]: e for e in case["evidence"]}
    pol_by_id = {p["id"]: p for p in case["policies"]}
    for p in case["patterns"]:
        L.append(f"### [{p['id']}] {p['type']} (confidence {p['confidence']})")
        L.append(p["summary"])
        for eid in p["evidence"]:
            e = ev_by_id.get(eid)
            if e:
                L.append(f"- [{e['id']}] **{e['kind']}**: {e['summary']} — supported by {', '.join(f'[{s}]' for s in e['supports'])}")
        for m in [m for m in mappings if m["pattern"] == p["id"]]:
            pol = pol_by_id[m["policy"]]
            L.append(f"- Violates [{pol['id']}] *{pol['title']}* (severity {pol['severity']}, confidence {m['confidence']:.2f}): {m['rationale']}")
        L.append("")
    L.append("## Analysis")
    L.append(narrative["analysis"])
    L.append("")
    L.append("## What was ruled out")
    benign = [e for e in case["evidence"] if e["kind"] == "BENIGN_EXPLANATION"]
    if benign:
        for e in benign:
            L.append(f"- [{e['id']}] {e['summary']} — supported by {', '.join(f'[{s}]' for s in e['supports'])}")
    else:
        L.append("No benign explanations were recorded by the Investigator.")
    L.append("")
    in_pat = {eid for p in case["patterns"] for eid in p["evidence"]}
    loose = [e for e in case["evidence"] if e["id"] not in in_pat and e["kind"] != "BENIGN_EXPLANATION"]
    if loose:
        L.append("## Evidence not attached to a pattern")
        for e in loose:
            L.append(f"- [{e['id']}] **{e['kind']}**: {e['summary']} — supported by {', '.join(f'[{s}]' for s in e['supports'])}")
        L.append("")
    if case.get("prior_cases"):
        L.append("## Prior cases recalled from memory")
        for c in case["prior_cases"]:
            tag = " *(synthetic demo history)*" if c["origin"] == "demo_history" else ""
            L.append(f"- [{c['id']}] **{c['outcome']}** — {c['title']}{tag}")
        L.append("")
    L.append("## Reasoning path (Investigator steps)")
    for s in case["steps"]:
        mark = "" if s["ok"] else " *(rejected/error)*"
        L.append(f"{s['seq']}. [{s['id']}] `{s['tool']}`{mark} — {s['reason'] or '(no reason given)'}")
    L.append("")
    L.append("---")
    L.append(HUMAN_NOTE)
    return "\n".join(L)


def persist(g, case, markdown, payload, cited):
    r = g.ro_query("MATCH (r:Report) RETURN max(toInteger(substring(r.id, 4)))").result_set[0][0]
    rid = f"RPT-{(r or 0) + 1}"
    g.query("MATCH (i:Investigation {id:$inv}) CREATE (r:Report {id:$id, investigation:$inv, markdown:$md, json:$js, recommendation:$rec, "
            "confidence:$conf, created_ts:timestamp()}) CREATE (r)-[:ABOUT]->(i)",
            {"inv": case["id"], "id": rid, "md": markdown, "js": json.dumps(payload), "rec": payload["recommendation"],
             "conf": float(payload["confidence"])})
    g.query("MATCH (r:Report {id:$id}) UNWIND $ids AS x MATCH (n) WHERE n.id = x AND (n:Pattern OR n:Evidence OR n:Policy) "
            "MERGE (r)-[:CITES]->(n)", {"id": rid, "ids": cited})
    g.query("MATCH (r:Report {id:$id}) UNWIND $ids AS x MATCH (n:PriorCase {id:x}) MERGE (r)-[:CITES]->(n)", {"id": rid, "ids": cited})
    return rid


def build_report(investigation_id: str, graph=None) -> dict:
    g = graph if graph is not None else get_graph()
    case = load_case(g, investigation_id)
    if case["status"] == "running":
        raise ReportError(f"Investigation {investigation_id} is still running.")
    if case["status"] in ("abandoned", "failed"):
        raise ReportError(f"Investigation {investigation_id} did not finish ({case['status']}); run it again.")
    mappings = map_policies(g, case)
    narrative = write_narrative(g, case, mappings)
    md = render_markdown(case, mappings, narrative)
    bad, cited = check_citations(g, md)
    if bad:
        raise ReportError(f"Unresolvable citations in final report: {bad}")
    # CITES edges for every pattern/evidence/policy referenced anywhere in the report
    payload = dict(investigation=case["id"], target_tx=case["target_tx"], recommendation=narrative["recommendation"],
                   confidence=float(narrative["confidence"]), executive_summary=narrative["executive_summary"],
                   analysis=narrative["analysis"], patterns=case["patterns"], evidence=case["evidence"],
                   policy_matches=mappings, steps=[{k: s[k] for k in ("id", "seq", "tool", "reason")} for s in case["steps"]],
                   citations=cited, human_decision_note=HUMAN_NOTE)
    rid = persist(g, case, md, payload, cited)
    payload["report_id"] = rid
    return dict(report_id=rid, markdown=md, json=payload)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--investigation", required=True)
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()
    try:
        res = build_report(a.investigation)
    except ReportError as e:
        print(f"REPORT_FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, f"{res['report_id']}.md"), "w") as f:
        f.write(res["markdown"])
    with open(os.path.join(a.out, f"{res['report_id']}.json"), "w") as f:
        json.dump(res["json"], f, indent=2)
    print(res["markdown"])
    try:  # memory is optional and runs after the report is safely persisted
        from memory import graphiti_mem
        m = graphiti_mem.remember(a.investigation)
        print(f"\n[memory] {m}")
    except Exception as e:
        print(f"\n[memory] not stored: {type(e).__name__}: {e}")
    print(f"\nREPORT_ID={res['report_id']}")
