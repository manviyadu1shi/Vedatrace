"""Investigator: a tool-using LLM loop. The model chooses every next tool call; code only executes, validates and records.
All output goes into the graph (Investigation / Step / Evidence / Pattern). Nothing is returned to the Reporter in-process."""
import argparse
import json
import time

from agents import llm
from agents.tools import TOOL_SCHEMAS, ToolBox, ToolError, label_of, short_summary
from config import get_graph

MAX_STEPS = 36
RESULT_CHARS = 3000
KEEP_FULL_RESULTS = 6

SYSTEM_PROMPT = """You are an anti-money-laundering (AML) investigator working on a graph of customers, accounts, devices and transactions.
You are given ONE alerted transaction. Decide for yourself what to look at next, using the tools. Do not assume the transaction is guilty or innocent.

Method:
- Form hypotheses, test each with a tool, and only record what the data you have actually seen supports.
- Typical laundering indicators (none is conclusive alone): accounts of different customers sharing a device; one account redistributing
  funds to many recipients in a short burst, especially with amounts clustered just under a review threshold (structuring);
  funds travelling through several accounts and returning to the origin or to accounts tied to it (circular flow / layering);
  newly opened accounts forwarding money quickly.
- Equally important: spot legitimate explanations (payroll, marketplace payouts, households or offices sharing a device, rent and
  refund cycles, bill splitting). If something looks suspicious but is explained, record it with write_evidence kind BENIGN_EXPLANATION
  and do NOT build a fraud Pattern from it.
- Suspicious activity is often NOT on the alerted sender itself but on connected accounts: accounts that share a device with a party,
  the receiver, and accounts the receiver pays onward. Profile and analyze those too, not just the first two accounts.
- Be thorough before you finish. Before concluding, make sure you have checked: devices of every party, fan-out behaviour of
  every party and every device-linked account, where the money goes after the receiver (trace_flow), and money cycles with max_hops=5
  from the sender. Do not record a RAPID_FANOUT finding unless the burst is clearly above the account's typical_recipients_per_window.
- Follow leads outward from the alerted transaction: its sender and receiver first, then whoever they are connected to.
- Every tool call needs a short `reason`. Ids you cite must come from tool results; never invent ids.

Recording:
- write_evidence records ONE finding with supporting_node_ids (Transaction/Account/Device ids). Write evidence as you confirm it.
- write_pattern combines Evidence (use the EV-... ids returned) into a Pattern. Only write a Pattern backed by evidence.
- When you are done (usually after 15-25 tool calls), call finish with a concise summary including what you ruled out.
"""


REVIEW_NOTE = ("Review the tool result(s) you just received. If they confirm a finding, or reveal a legitimate explanation for "
               "something suspicious-looking, call write_evidence NOW with ids taken from the result (kind BENIGN_EXPLANATION for "
               "legitimate explanations). If they show nothing notable, continue investigating.")


def _compact(history, keep=KEEP_FULL_RESULTS):
    """Shrink old tool results so small local models keep their context window."""
    idx = [i for i, m in enumerate(history) if m["role"] == "tool"]
    for i in idx[:-keep]:
        c = history[i]["content"]
        if len(c) > 350:
            history[i] = dict(history[i], content=c[:350] + "…[older result truncated]")


class Investigator:
    def __init__(self, graph=None, on_event=None, max_steps=MAX_STEPS):
        self.g = graph if graph is not None else get_graph()
        self.on_event = on_event or (lambda e: None)
        self.max_steps = max_steps

    def _emit(self, **e):
        self.on_event(e)

    def _new_investigation(self, tx_id, memory_context):
        r = self.g.query("MATCH (i:Investigation) RETURN max(toInteger(substring(i.id, 4)))").result_set[0][0]
        inv = f"INV-{(r or 0) + 1}"
        self.g.query("CREATE (:Investigation {id:$id, target_tx:$tx, status:'running', started_ts:timestamp(), provider:$p, model:$m, "
                     "memory_used:$mem})", {"id": inv, "tx": tx_id, "p": llm.provider(), "m": llm.model_name(), "mem": bool(memory_context)})
        return inv

    def _record_step(self, inv, seq, name, args, reason):
        sid = f"{inv}-S{seq:02d}"
        self.g.query("MATCH (i:Investigation {id:$inv}) CREATE (i)-[:HAS_STEP]->(:Step {id:$sid, seq:$seq, tool:$tool, args:$args, "
                     "reason:$reason, ok:true, ts:timestamp()})",
                     {"inv": inv, "sid": sid, "seq": seq, "tool": name, "args": json.dumps(args, default=str)[:1500],
                      "reason": (reason or "")[:500]})
        for v in args.values():
            for node_id in (v if isinstance(v, list) else [v]):
                lab = label_of(node_id) if isinstance(node_id, str) else None
                if lab in ("Account", "Transaction", "Device", "Customer"):
                    self.g.query(f"MATCH (s:Step {{id:$sid}}), (n:{lab} {{id:$nid}}) MERGE (s)-[:EXAMINED]->(n)", {"sid": sid, "nid": node_id})
        return sid

    def _finish_step(self, sid, ok, summary):
        self.g.query("MATCH (s:Step {id:$sid}) SET s.ok=$ok, s.result_summary=$sm", {"sid": sid, "ok": ok, "sm": summary[:600]})

    def run(self, tx_id: str, memory_context: str | None = None) -> str:
        inv = self._new_investigation(tx_id, memory_context)
        tb = ToolBox(self.g, inv)
        system = SYSTEM_PROMPT + (f"\nPrior cases from memory (may or may not apply; verify everything in the current graph):\n{memory_context}\n" if memory_context else "")
        history = [dict(role="user", content=f"Alerted transaction: {tx_id}. Investigate it. Start now.")]
        self._emit(type="start", investigation=inv, tx=tx_id, provider=llm.provider(), model=llm.model_name())
        seq, idle, status = 0, 0, "incomplete"
        try:
            reflections = 0
            while seq < self.max_steps and tb.finished is None:
                resp = llm.chat(history, TOOL_SCHEMAS, system=system)
                if not resp.tool_calls:
                    idle += 1
                    if idle >= 3:
                        break
                    history.append(dict(role="assistant", content=resp.text or "(no output)"))
                    history.append(dict(role="user", content="Use a tool to continue, or call finish if you are done."))
                    continue
                idle = 0
                history.append(dict(role="assistant", content=resp.text, tool_calls=resp.tool_calls))
                for tc in resp.tool_calls:
                    seq += 1
                    reason = tc.arguments.get("reason", "") if isinstance(tc.arguments, dict) else ""
                    sid = self._record_step(inv, seq, tc.name, {k: v for k, v in tc.arguments.items() if k != "reason"}, reason)
                    tb.step_id = sid
                    try:
                        if tc.name == "finish" and reflections < 2:
                            reflections += 1
                            raise ToolError(self._reflection(inv))
                        result = tb.call(tc.name, dict(tc.arguments))
                        ok, content = True, json.dumps(result, default=str)
                    except ToolError as e:
                        ok, content = False, json.dumps(dict(error=str(e)))
                    summary = short_summary(json.loads(content), 500)
                    self._finish_step(sid, ok, summary)
                    self._emit(type="step", investigation=inv, step=sid, seq=seq, tool=tc.name, args=tc.arguments, reason=reason,
                               ok=ok, summary=summary)
                    history.append(dict(role="tool", tool_call_id=tc.id, content=content[:RESULT_CHARS]))
                if any(tc.name in ToolBox.ANALYTIC for tc in resp.tool_calls):
                    history.append(dict(role="user", content=REVIEW_NOTE))
                _compact(history)
                left = self.max_steps - seq
                if left in (6, 3) and tb.finished is None:
                    history.append(dict(role="user", content=f"{left} tool calls remain. Record the evidence and patterns the data supports, then call finish."))
            if tb.finished is None and seq >= self.max_steps:  # out of budget: force a closing summary instead of a silent stop
                history.append(dict(role="user", content="Step budget exhausted. Call finish now with your conclusions."))
                fin = [t for t in TOOL_SCHEMAS if t["name"] == "finish"]
                resp = llm.chat(history, fin, system=system)
                for tc in resp.tool_calls[:1]:
                    seq += 1
                    sid = self._record_step(inv, seq, "finish", {"summary": str(tc.arguments.get("summary", ""))[:300]}, "budget exhausted")
                    tb.step_id = sid
                    tb.finish(str(tc.arguments.get("summary", "")) or "Step budget exhausted.")
                    self._finish_step(sid, True, "finished (forced)")
                    self._emit(type="step", investigation=inv, step=sid, seq=seq, tool="finish", args=tc.arguments, reason="budget exhausted",
                               ok=True, summary="finished (forced)")
            status = "finished" if tb.finished is not None else "incomplete"
        except Exception as e:  # DB down, LLM unreachable, ...
            self._set_status(inv, "failed", f"{type(e).__name__}: {e}")
            self._emit(type="error", investigation=inv, message=f"{type(e).__name__}: {e}")
            raise
        self.g.query("MATCH (i:Investigation {id:$id}) SET i.status=$st, i.summary=$sm, i.finished_ts=timestamp(), i.steps=$n",
                     {"id": inv, "st": status, "sm": (tb.finished or "Step cap reached without finish.")[:2000], "n": seq})
        self._emit(type="finished", investigation=inv, status=status, summary=tb.finished, steps=seq)
        return inv

    def _reflection(self, inv):
        """Generic self-review prompt built from graph state: what has been recorded, and which leads were seen but not explored."""
        q = lambda c: self.g.ro_query(c, params={"inv": inv}).result_set
        ev = q("MATCH (:Investigation {id:$inv})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(e:Evidence) RETURN e.id, e.kind ORDER BY e.id")
        pat = q("MATCH (:Investigation {id:$inv})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(p:Pattern) RETURN p.id, p.pattern_type ORDER BY p.id")
        steps = q("MATCH (:Investigation {id:$inv})-[:HAS_STEP]->(s:Step) RETURN s.tool, s.args, s.result_summary ORDER BY s.seq")
        profiled, fanned, cycled, leads = set(), set(), set(), set()
        for tool, args, res in steps:
            try:
                a = json.loads(args)
            except Exception:
                a = {}
            acc = a.get("account_id")
            if tool == "get_account_profile": profiled.add(acc)
            if tool == "analyze_fanout": fanned.add(acc)
            if tool == "find_cycles": cycled.add(acc)
        shared = self.g.ro_query("MATCH (s:Step {tool:'find_shared_devices'})<-[:HAS_STEP]-(:Investigation {id:$inv}) "
                                 "MATCH (s)-[:EXAMINED]->(a:Account)-[:USED_DEVICE]->(:Device)<-[:USED_DEVICE]-(o:Account) RETURN DISTINCT o.id",
                                 params={"inv": inv}).result_set
        leads = {r[0] for r in shared} - fanned
        loose = q("MATCH (:Investigation {id:$inv})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(e:Evidence) WHERE NOT (:Pattern)-[:BACKED_BY]->(e) "
                  "AND e.kind <> 'BENIGN_EXPLANATION' RETURN e.id, e.kind ORDER BY e.id")
        msg = ("Self-review before finishing. "
               f"Evidence not yet grouped into any Pattern: {[f'{i}:{k}' for i, k in loose] or 'none'} (each structure needs a write_pattern; one pattern per kind). "
               f"Evidence so far: {[f'{i}:{k}' for i, k in ev] or 'none'}. Patterns so far: {[f'{i}:{k}' for i, k in pat] or 'none'}. "
               f"Accounts whose fan-out you have not analyzed but that are device-linked to accounts you examined: {sorted(leads) or 'none'}. "
               f"Accounts you analyzed for fan-out: {sorted(x for x in fanned if x)}. "
               "Re-read your earlier tool results: did any tool report an unusual burst, tightly clustered amounts, a cycle, or a device "
               "shared between different owners that you have not yet recorded as Evidence? Is every Evidence that belongs to a "
               "suspicious structure grouped into a Pattern? Did you check why each suspicious-looking account might be legitimate? "
               "If everything is recorded, call finish again; otherwise continue investigating.")
        return msg

    def _set_status(self, inv, status, msg):
        try:
            self.g.query("MATCH (i:Investigation {id:$id}) SET i.status=$st, i.summary=$sm, i.finished_ts=timestamp()",
                         {"id": inv, "st": status, "sm": msg[:500]})
        except Exception:
            pass


def console(e):
    if e["type"] == "start":
        print(f"[{e['investigation']}] {e['provider']}/{e['model']} investigating {e['tx']}")
    elif e["type"] == "step":
        flag = "" if e["ok"] else " (ERROR)"
        print(f"  {e['seq']:>2}. {e['tool']}{flag} — {e['reason'][:110]}\n      -> {e['summary'][:160]}")
    elif e["type"] == "finished":
        print(f"[{e['investigation']}] {e['status']} after {e['steps']} steps: {(e['summary'] or '')[:300]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", default="TX-FLAGSHIP")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    a = ap.parse_args()
    t = time.time()
    inv = Investigator(on_event=console, max_steps=a.max_steps).run(a.tx)
    print(f"INVESTIGATION_ID={inv} ({time.time() - t:.0f}s)")
