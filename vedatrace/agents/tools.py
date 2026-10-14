"""Investigator tools over FalkorDB. Read tools are generic graph analytics; they contain no knowledge of any
specific fraud scenario. Every result is small JSON. Cypher is parameterized (var-length bounds are clamped ints,
which Cypher cannot take as parameters)."""
import json
import re
from datetime import datetime, timezone

from config import get_graph

PREFIX_LABEL = {"ACC-": "Account", "TX-": "Transaction", "DEV-": "Device", "CUST-": "Customer",
                "EV-": "Evidence", "PAT-": "Pattern", "POL-": "Policy"}
BENIGN_BASES = ["RECURRING_PAYOUTS", "RECIPROCAL_REFUND", "HOUSEHOLD_OR_OFFICE_DEVICE", "OTHER"]
EVIDENCE_KINDS = ["SHARED_DEVICE", "RAPID_FANOUT", "STRUCTURING_AMOUNTS", "CIRCULAR_FLOW", "BENIGN_EXPLANATION"]
PATTERN_EVIDENCE = {  # which evidence kinds may back which pattern type
    "SHARED_DEVICE_CLUSTER": {"SHARED_DEVICE"},
    "RAPID_FANOUT": {"RAPID_FANOUT", "STRUCTURING_AMOUNTS"},
    "STRUCTURING": {"STRUCTURING_AMOUNTS", "RAPID_FANOUT"},
    "CIRCULAR_FLOW": {"CIRCULAR_FLOW"},
}
PATTERN_TYPES = ["SHARED_DEVICE_CLUSTER", "RAPID_FANOUT", "STRUCTURING", "CIRCULAR_FLOW"]
SUPPORT_LABELS = {"Transaction", "Account", "Device"}
QUERY_TIMEOUT_MS = 8000
ID_RE = re.compile(r"\b(?:ACC|TX|DEV|CUST)-[A-Za-z0-9]+\b")


def iso(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def label_of(node_id: str):
    for p, l in PREFIX_LABEL.items():
        if isinstance(node_id, str) and node_id.startswith(p):
            return l
    return None


def _s(name, desc, props, required):
    props = dict(props)
    props["reason"] = {"type": "string", "description": "One sentence: why you are calling this tool now / what you expect to learn."}
    return dict(name=name, description=desc,
                parameters=dict(type="object", properties=props, required=list(required) + ["reason"]))


ID = {"type": "string"}
TOOL_SCHEMAS = [
    _s("get_transaction", "Details of one transaction: sender, receiver, amount, time, channel, monitoring score.",
       {"tx_id": ID}, ["tx_id"]),
    _s("get_account_profile", "Profile of an account: owner, devices, age, in/out counts, counterparties, volumes, first/last activity.",
       {"account_id": ID}, ["account_id"]),
    _s("get_neighbors", "Raw graph neighbors of any node (Account, Transaction, Device, Customer). Optionally filter by edge type "
       "(OWNS, USED_DEVICE, SENT, TO).", {"node_id": ID, "edge_type": {"type": "string"}, "limit": {"type": "integer"}}, ["node_id"]),
    _s("find_shared_devices", "Devices used by this account and every other account (and its owner) that uses the same device.",
       {"account_id": ID}, ["account_id"]),
    _s("analyze_fanout", "Outgoing-transfer burst analysis for an account: busiest time window (distinct recipients, totals, amount range), "
       "repeated bursts, and money received just before the burst.",
       {"account_id": ID, "window_days": {"type": "number", "description": "window length in days, e.g. 2"}}, ["account_id"]),
    _s("trace_flow", "Follow money forward ('out') or backward ('in') from an account for up to max_hops account hops (max 4). "
       "Returns accounts reached per hop with amounts. Use 4 hops to see how far money travels.",
       {"account_id": ID, "direction": {"type": "string", "enum": ["out", "in"]}, "max_hops": {"type": "integer"}}, ["account_id", "direction"]),
    _s("find_cycles", "Find directed money cycles that start and end at an account (max_hops account hops, max 5). Layering loops "
       "often take 4-5 hops, so prefer max_hops=5 before concluding there is no cycle.",
       {"account_id": ID, "max_hops": {"type": "integer"}}, ["account_id"]),
    _s("shortest_path", "Shortest connection (any edge type) between two nodes, up to max_hops (max 8).",
       {"a": ID, "b": ID, "max_hops": {"type": "integer"}}, ["a", "b"]),
    _s("list_policies", "List the compliance policies (id, title, description) so you know what thresholds and behaviours matter.", {}, []),
    _s("write_evidence", "Record ONE factual finding supported by data you have seen. supporting_node_ids must be real "
       "Transaction/Account/Device ids (copy the `cite_ids` list from the tool result that showed the finding). Kinds: " + ", ".join(EVIDENCE_KINDS) + ". Use BENIGN_EXPLANATION when something looks "
       "suspicious but is explained by legitimate context.",
       {"kind": {"type": "string", "enum": EVIDENCE_KINDS}, "summary": {"type": "string"},
        "supporting_node_ids": {"type": "array", "items": ID}, "confidence": {"type": "number", "description": "0..1"},
        "benign_basis": {"type": "string", "enum": BENIGN_BASES, "description": "Only for BENIGN_EXPLANATION: why it is legitimate. "
                         "RECURRING_PAYOUTS (repeat bursts to the same recipients, e.g. payroll) and RECIPROCAL_REFUND (equal amounts back and forth) are checked against the graph."}},
       ["kind", "summary", "supporting_node_ids", "confidence"]),
    _s("write_pattern", "Record a higher-level fraud pattern backed by Evidence you already wrote (use EV-... ids). Types: "
       + ", ".join(PATTERN_TYPES) + ".",
       {"pattern_type": {"type": "string", "enum": PATTERN_TYPES}, "summary": {"type": "string"},
        "evidence_ids": {"type": "array", "items": ID}, "confidence": {"type": "number"}},
       ["pattern_type", "summary", "evidence_ids", "confidence"]),
    _s("finish", "End the investigation with a short summary of conclusions (including what you ruled out).",
       {"summary": {"type": "string"}}, ["summary"]),
]
READ_ONLY_FREE = {"list_policies", "get_transaction", "get_account_profile", "get_neighbors", "find_shared_devices", "analyze_fanout",
                  "trace_flow", "find_cycles", "shortest_path"}


class ToolError(Exception):
    """Recoverable error whose message is shown to the model."""


def validate_args(name: str, args: dict):
    schema = next((t for t in TOOL_SCHEMAS if t["name"] == name), None)
    if schema is None:
        raise ToolError(f"Unknown tool '{name}'. Available: {[t['name'] for t in TOOL_SCHEMAS]}")
    if "_malformed" in args:
        raise ToolError("Arguments were not valid JSON. Retry with a JSON object.")
    params = schema["parameters"]
    for r in params["required"]:
        if r not in args or args[r] in (None, "", []):
            raise ToolError(f"Missing required argument '{r}' for {name}.")
    types = {"string": str, "integer": (int, float), "number": (int, float), "array": list}
    for k, v in args.items():
        spec = params["properties"].get(k)
        if spec is None:
            continue  # ignore unknown extras
        if not isinstance(v, types[spec["type"]]) or isinstance(v, bool):
            raise ToolError(f"Argument '{k}' must be of type {spec['type']}.")
        if "enum" in spec and v not in spec["enum"]:
            raise ToolError(f"Argument '{k}' must be one of {spec['enum']}.")
    return {k: v for k, v in args.items() if k in params["properties"]}


class ToolBox:
    def __init__(self, graph=None, investigation_id: str | None = None):
        self.g = graph if graph is not None else get_graph()
        self.inv = investigation_id
        self.step_id = None  # set by the investigator before each call (provenance for writers)
        self.finished = None
        self.seen = set()  # node ids that appeared in tool results; evidence may only cite ids the agent has actually seen

    # ---- helpers -------------------------------------------------
    def q(self, cypher, params=None):
        return self.g.ro_query(cypher, params=params or {}, timeout=QUERY_TIMEOUT_MS).result_set

    def _require(self, node_id, label):
        if label_of(node_id) != label or not self.q(f"MATCH (n:{label} {{id:$id}}) RETURN n.id", {"id": node_id}):
            raise ToolError(f"{label} '{node_id}' does not exist in the graph. Use ids returned by other tools.")

    def _exists(self, node_id):
        lab = label_of(node_id)
        return lab is not None and bool(self.q(f"MATCH (n:{lab} {{id:$id}}) RETURN n.id", {"id": node_id}))

    # ---- read tools ------------------------------------------------
    def get_transaction(self, tx_id):
        self._require(tx_id, "Transaction")
        r = self.q("MATCH (a:Account)-[:SENT]->(t:Transaction {id:$id})-[:TO]->(b:Account) "
                   "RETURN a.id, b.id, t.amount, t.ts, t.channel, t.monitor_score, t.alerted", {"id": tx_id})[0]
        return dict(id=tx_id, sender=r[0], receiver=r[1], amount=r[2], time=iso(r[3]), channel=r[4],
                    monitor_score=r[5], alerted=r[6], currency="USD")

    def get_account_profile(self, account_id):
        self._require(account_id, "Account")
        a = self.q("MATCH (c:Customer)-[:OWNS]->(a:Account {id:$id}) RETURN c.id, c.name, c.segment, a.type, a.opened_ts",
                   {"id": account_id})[0]
        devs = [r[0] for r in self.q("MATCH (:Account {id:$id})-[:USED_DEVICE]->(d:Device) RETURN d.id", {"id": account_id})]
        out = self.q("MATCH (:Account {id:$id})-[:SENT]->(t:Transaction)-[:TO]->(b:Account) "
                     "RETURN count(t), count(DISTINCT b), sum(t.amount), min(t.ts), max(t.ts)", {"id": account_id})[0]
        inn = self.q("MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(:Account {id:$id}) "
                     "RETURN count(t), count(DISTINCT a), sum(t.amount), min(t.ts), max(t.ts)", {"id": account_id})[0]
        times = [x for x in (out[3], inn[3]) if x is not None]
        timesl = [x for x in (out[4], inn[4]) if x is not None]
        return dict(id=account_id, owner=dict(id=a[0], name=a[1], segment=a[2]), type=a[3], opened=iso(a[4])[:10],
                    devices=devs, sent=dict(tx_count=out[0], distinct_recipients=out[1], total=round(out[2] or 0, 2)),
                    received=dict(tx_count=inn[0], distinct_senders=inn[1], total=round(inn[2] or 0, 2)),
                    first_activity=iso(min(times))[:10] if times else None, last_activity=iso(max(timesl))[:10] if timesl else None)

    def get_neighbors(self, node_id, edge_type=None, limit=20):
        lab = label_of(node_id)
        if lab not in ("Account", "Transaction", "Device", "Customer"):
            raise ToolError("get_neighbors supports Account, Transaction, Device and Customer ids.")
        self._require(node_id, lab)
        limit = max(1, min(int(limit or 20), 30))
        rows = self.q(f"MATCH (n:{lab} {{id:$id}})-[r]-(m) WHERE $et IS NULL OR type(r) = $et "
                      "RETURN type(r), startNode(r).id = n.id, labels(m)[0], m.id LIMIT $lim",
                      {"id": node_id, "et": edge_type or None, "lim": limit})
        return dict(node=node_id, neighbors=[dict(edge=r[0], direction="out" if r[1] else "in", type=r[2], id=r[3]) for r in rows],
                    note=f"capped at {limit}")

    def find_shared_devices(self, account_id):
        self._require(account_id, "Account")
        rows = self.q("MATCH (me:Account {id:$id})-[:USED_DEVICE]->(d:Device)<-[:USED_DEVICE]-(o:Account) "
                      "MATCH (c:Customer)-[:OWNS]->(o) MATCH (mc:Customer)-[:OWNS]->(me) "
                      "RETURN d.id, d.kind, o.id, c.id, c.name, c.id = mc.id LIMIT 40", {"id": account_id})
        devs = {}
        for d, kind, oid, cid, cname, same in rows:
            devs.setdefault(d, dict(device=d, kind=kind, other_accounts=[]))["other_accounts"].append(
                dict(account=oid, owner=cid, owner_name=cname, same_owner=bool(same)))
        mine = [r[0] for r in self.q("MATCH (:Account {id:$id})-[:USED_DEVICE]->(d:Device) RETURN d.id", {"id": account_id})]
        interp = []
        for d in devs.values():
            others = [o for o in d["other_accounts"] if o["account"] != account_id]
            owners = {o["owner"] for o in others if not o["same_owner"]}
            if owners:
                d["cite_ids"] = [d["device"]] + [o["account"] for o in d["other_accounts"]]
                interp.append(f"{d['device']} is also used by {len(others)} other account(s) owned by {len(owners)} different customer(s): "
                              f"{[o['account'] for o in others]}.")
        return dict(account=account_id, devices_used=mine, interpretation=interp or ["No device shared with a different customer."],
                    shared=list(devs.values()))

    def analyze_fanout(self, account_id, window_days=2):
        self._require(account_id, "Account")
        w = max(0.1, min(float(window_days or 2), 30)) * 86400
        txs = self.q("MATCH (:Account {id:$id})-[:SENT]->(t:Transaction)-[:TO]->(b:Account) RETURN t.id, b.id, t.amount, t.ts ORDER BY t.ts LIMIT 2000",
                     {"id": account_id})
        if not txs:
            return dict(account=account_id, outgoing_tx=0, note="no outgoing transactions")
        # find disjoint bursts greedily: window starts at first unused tx; keep windows with >=4 distinct recipients
        bursts, i = [], 0
        while i < len(txs):
            j = i
            while j < len(txs) and txs[j][3] - txs[i][3] <= w:
                j += 1
            win = txs[i:j]
            rec = {t[1] for t in win}
            if len(rec) >= 4:
                bursts.append(win)
                i = j
            else:
                i += 1
        # single best sliding window (even if small) for context
        best, counts = [], []
        for i in range(len(txs)):
            j = i
            while j < len(txs) and txs[j][3] - txs[i][3] <= w:
                j += 1
            counts.append(len({t[1] for t in txs[i:j]}))
            if counts[-1] > len({t[1] for t in best}):
                best = txs[i:j]
        counts.sort()
        # baseline: distinct recipients per tiled window across the whole observation period (empty windows count as 0)
        lo, hi = self.q("MATCH (t:Transaction) RETURN min(t.ts), max(t.ts)")[0]
        nbins = max(1, int((hi - lo) // w) + 1)
        bins = [set() for _ in range(nbins)]
        for t in txs:
            bins[min(int((t[3] - lo) // w), nbins - 1)].add(t[1])
        per_bin = sorted(len(b) for b in bins)
        typical = per_bin[len(per_bin) // 2]
        def summ(win):
            am = [t[2] for t in win]
            return dict(start=iso(win[0][3]), end=iso(win[-1][3]), tx_count=len(win), distinct_recipients=len({t[1] for t in win}),
                        total=round(sum(am), 2), min_amount=min(am), max_amount=max(am),
                        sample_tx_ids=[t[0] for t in win[:10]], sample_recipients=sorted({t[1] for t in win})[:10])
        am = [t[2] for t in best]
        top = max(am)
        near_top = sum(1 for x in am if x >= 0.9 * top)
        assessment = dict(
            burst_vs_typical=f"{counts[-1]} distinct recipients in the busiest window vs typical {typical}",
            unusual_burst=bool(counts[-1] >= 5 and counts[-1] >= 3 * max(typical, 1)),
            amounts_tightly_clustered=bool(len(am) >= 5 and near_top >= 0.8 * len(am)),
            amount_min_max=[min(am), top])
        interp = []
        if assessment["unusual_burst"]:
            interp.append(f"UNUSUAL: {counts[-1]} distinct recipients inside {w / 86400:g} days, far above this account's norm of {typical}.")
        else:
            interp.append("No unusual recipient burst compared with this account's own baseline.")
        if assessment["unusual_burst"]:
            interp.append("If not explained by a legitimate recurring purpose, record RAPID_FANOUT evidence using cite_ids_for_burst.")
        if assessment["amounts_tightly_clustered"]:
            interp.append(f"Burst amounts are tightly clustered between {min(am)} and {top}: compare with policy thresholds (list_policies); "
                          "if just below a threshold also record STRUCTURING_AMOUNTS evidence with the same cite ids.")
        cite = [account_id] + [t[0] for t in best[:12]]
        res = dict(account=account_id, window_days=w / 86400, interpretation=interp, assessment=assessment,
                   cite_ids_for_burst=cite, outgoing_tx=len(txs),
                   distinct_recipients_overall=len({t[1] for t in txs}), busiest_window=summ(best),
                   typical_recipients_per_window=typical,
                   separate_bursts_with_4plus_recipients=len(bursts), bursts=[summ(b) for b in bursts[:4]])
        if len(bursts) >= 2:
            sets = [{t[1] for t in b} for b in bursts]
            ov = round(len(set.intersection(*sets)) / max(1, len(set.union(*sets))), 2)
            res["recipient_overlap_between_bursts"] = ov
            interp.append(f"{len(bursts)} separate bursts with {ov:.0%} recipient overlap: a recurring flow (e.g. payroll/payouts) rather than a one-off.")
        # money received shortly before the busiest window
        t0 = best[0][3]
        inn = self.q("MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(:Account {id:$id}) WHERE t.ts >= $lo AND t.ts <= $hi "
                     "RETURN t.id, a.id, t.amount, t.ts ORDER BY t.ts DESC LIMIT 8", {"id": account_id, "lo": t0 - 2 * 86400, "hi": t0 + w})
        res["received_around_burst"] = [dict(tx=r[0], from_account=r[1], amount=r[2], time=iso(r[3])) for r in inn]
        return res

    def trace_flow(self, account_id, direction="out", max_hops=3):
        self._require(account_id, "Account")
        hops = max(1, min(int(max_hops or 3), 4))
        pat = "(a:Account)-[:SENT]->(t:Transaction)-[:TO]->(b:Account)" if direction == "out" else "(b:Account)-[:SENT]->(t:Transaction)-[:TO]->(a:Account)"
        seen, frontier, levels = {account_id}, [account_id], []
        for h in range(1, hops + 1):
            rows = self.q(f"UNWIND $ids AS i MATCH {pat} WHERE a.id = i RETURN a.id, b.id, count(t), sum(t.amount), min(t.ts), max(t.ts) LIMIT 300",
                          {"ids": frontier[:60]})
            nxt, edges = [], []
            for a, b, n, tot, lo, hi in rows:
                edges.append(dict(frm=a, to=b, tx_count=n, total=round(tot, 2)) if direction == "out"
                             else dict(frm=b, to=a, tx_count=n, total=round(tot, 2)))
                if b not in seen:
                    seen.add(b)
                    nxt.append(b)
            levels.append(dict(hop=h, accounts_reached=len(nxt), edges_sample=sorted(edges, key=lambda e: -e["total"])[:10],
                               returns_to_start=any((e["to"] if direction == "out" else e["frm"]) == account_id for e in edges)))
            frontier = nxt
            if not frontier:
                break
        return dict(start=account_id, direction=direction, levels=levels)

    def find_cycles(self, account_id, max_hops=5):
        self._require(account_id, "Account")
        hops = max(2, min(int(max_hops or 5), 5))
        def routes(lo, hi, limit):
            return self.q(f"MATCH p=(a:Account {{id:$id}})-[:SENT|TO*{lo}..{hi}]->(a) "
                          "WITH [n IN nodes(p) WHERE n:Account | n.id] AS accs, [n IN nodes(p) WHERE n:Transaction | n] AS txn "
                          "WITH accs, head(collect(txn)) AS tx, count(*) AS variants "
                          "RETURN accs, [t IN tx | t.id], reduce(m = 1000000000.0, t IN tx | CASE WHEN t.amount < m THEN t.amount ELSE m END), variants "
                          f"ORDER BY size(accs) LIMIT {limit}", {"id": account_id})
        try:
            rows = routes(6, 2 * hops, 7) if hops >= 3 else []  # loops through 3+ accounts
            rows = rows + routes(4, 4, 2)                         # two-account back-and-forth, reported separately so it cannot crowd out loops
        except Exception as e:  # timeout on hubs
            raise ToolError(f"Cycle search was too expensive ({type(e).__name__}); retry with a smaller max_hops.")
        cycles = [dict(accounts=accs[:-1], hops=len(accs) - 1, shape="reciprocal_exchange" if len(accs) <= 3 else "multi_hop_loop",
                       cite_ids=accs[:-1] + txs, min_amount_on_cycle=minamt, parallel_tx_variants=var)
                  for accs, txs, minamt, var in rows]
        if cycles:
            n_loop = sum(1 for c in cycles if c["shape"] == "multi_hop_loop")
            interp = [f"{len(cycles)} distinct cycle route(s) returning to {account_id}: {n_loop} multi-hop loop(s) through 3+ accounts, "
                      f"{len(cycles) - n_loop} two-account back-and-forth (reciprocal exchange, often benign: refunds, family transfers, bill splitting)."]
        else:
            interp = [f"No cycle returning to {account_id} within {hops} hops."]
        return dict(account=account_id, max_hops=hops, interpretation=interp, distinct_routes=len(cycles), cycles=cycles,
                    note="distinct account routes: up to 7 loops through 3+ accounts (shortest first) plus up to 2 two-account exchanges")

    def shortest_path(self, a, b, max_hops=6):
        la, lb = label_of(a), label_of(b)
        if la is None or lb is None:
            raise ToolError("Ids must look like ACC-..., TX-..., DEV-... or CUST-...")
        self._require(a, la)
        self._require(b, lb)
        hops = max(1, min(int(max_hops or 6), 8))
        try:
            rows = self.q(f"MATCH (x:{la} {{id:$a}}), (y:{lb} {{id:$b}}) WITH shortestPath((x)-[*..{hops}]-(y)) AS p "
                          "RETURN [n IN nodes(p) | n.id], [r IN relationships(p) | type(r)]", {"a": a, "b": b})
        except Exception as e:
            raise ToolError(f"Path search failed ({type(e).__name__}); try a smaller max_hops.")
        if not rows:
            return dict(a=a, b=b, connected=False, note=f"no path within {hops} hops")
        return dict(a=a, b=b, connected=True, nodes=rows[0][0], edge_types=rows[0][1])

    def list_policies(self):
        rows = self.q("MATCH (p:Policy) RETURN p.id, p.title, p.description ORDER BY p.id")
        return dict(policies=[dict(id=r[0], title=r[1], description=r[2]) for r in rows])

    # ---- writers ---------------------------------------------------
    def _next_id(self, prefix, label):
        r = self.q(f"MATCH (n:{label}) RETURN max(toInteger(substring(n.id, {len(prefix)})))")[0][0]
        return f"{prefix}{(r or 0) + 1}"

    def _verify_claim(self, kind, ids):
        """Check the evidence claim against the graph so the agent cannot record something the data does not show."""
        accs = [i for i in ids if i.startswith("ACC-")]
        txs = [i for i in ids if i.startswith("TX-")]
        devs = [i for i in ids if i.startswith("DEV-")]
        if kind == "SHARED_DEVICE":
            if not devs or len(accs) < 2:
                raise ToolError("SHARED_DEVICE evidence must cite the Device and at least two Accounts that use it.")
            rows = self.q("MATCH (a:Account)-[:USED_DEVICE]->(d:Device) WHERE d.id IN $d AND a.id IN $a "
                          "MATCH (c:Customer)-[:OWNS]->(a) RETURN d.id, collect(DISTINCT a.id), count(DISTINCT c.id)", {"d": devs, "a": accs})
            if not any(len(r[1]) >= 2 and r[2] >= 2 for r in rows):
                raise ToolError("Graph check failed: the cited accounts do not include two accounts of different customers using the cited device.")
        elif kind in ("RAPID_FANOUT", "STRUCTURING_AMOUNTS"):
            if len(txs) < 3:
                raise ToolError(f"{kind} evidence must cite at least 3 outgoing Transactions of the burst (e.g. sample_tx_ids from analyze_fanout) plus the sending Account. Use `cite_ids_for_burst` from analyze_fanout of that account.")
            rows = self.q("MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(b:Account) WHERE t.id IN $t "
                          "RETURN a.id, count(t), count(DISTINCT b), min(t.amount), max(t.amount), min(t.ts), max(t.ts)", {"t": txs})
            best = max(rows, key=lambda r: r[1]) if rows else None
            if not best or best[1] < 3:
                raise ToolError("Graph check failed: the cited transactions are not 3+ transfers sent by one account. Cite the burst transactions of a single sender.")
            if kind == "RAPID_FANOUT" and (best[2] < 3 or best[6] - best[5] > 7 * 86400):
                raise ToolError("Graph check failed: cited transactions must go to 3+ distinct recipients within 7 days.")
            if kind == "STRUCTURING_AMOUNTS" and best[4] > 1.25 * best[3]:
                raise ToolError("Graph check failed: cited transaction amounts are not tightly clustered (max/min > 1.25).")
        elif kind == "CIRCULAR_FLOW":
            if len(txs) < 2:
                raise ToolError("CIRCULAR_FLOW evidence must cite every Transaction on the cycle (use `cite_ids` of one route from find_cycles).")
            rows = self.q("MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(b:Account) WHERE t.id IN $t RETURN a.id, b.id", {"t": txs})
            nxt = {}
            for a_, b_ in rows:
                nxt.setdefault(a_, set()).add(b_)
            def closes(start, node, depth):
                for n in nxt.get(node, ()):
                    if n == start or (depth < len(rows) and closes(start, n, depth + 1)):
                        return True
                return False
            if not any(closes(a_, a_, 1) for a_ in nxt):
                raise ToolError("Graph check failed: the cited transactions do not form a closed loop of money. Cite all transactions on the cycle.")

    def _verify_benign(self, basis, ids):
        accs = [i for i in ids if i.startswith("ACC-")]
        txs = [i for i in ids if i.startswith("TX-")]
        if basis not in BENIGN_BASES:
            raise ToolError(f"BENIGN_EXPLANATION needs benign_basis, one of {BENIGN_BASES}.")
        if basis == "RECURRING_PAYOUTS":
            for a in accs:
                r = self.analyze_fanout(a, 2)
                if r.get("separate_bursts_with_4plus_recipients", 0) >= 2 and r.get("recipient_overlap_between_bursts", 0) >= 0.8:
                    return
            raise ToolError("Graph check failed: none of the cited accounts shows repeated bursts to the same recipients (payroll-like). "
                            "Cite the paying account, and only claim RECURRING_PAYOUTS if analyze_fanout showed recurring bursts with high overlap.")
        if basis == "RECIPROCAL_REFUND":
            rows = self.q("MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(b:Account) WHERE t.id IN $t RETURN a.id, b.id, t.amount", {"t": txs})
            if any(x[0] == y[1] and x[1] == y[0] and abs(x[2] - y[2]) < 0.01 for x in rows for y in rows):
                return
            raise ToolError("Graph check failed: cited transactions do not contain equal amounts flowing back and forth between two accounts.")
        # unverifiable bases may not contradict the investigator's own suspicious findings
        clash = self.q("MATCH (:Investigation {id:$inv})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(e:Evidence)-[:SUPPORTED_BY]->(n) "
                       "WHERE e.kind <> 'BENIGN_EXPLANATION' AND n.id IN $ids RETURN DISTINCT e.id, n.id LIMIT 5", {"inv": self.inv, "ids": ids})
        if clash:
            raise ToolError(f"This benign claim cites nodes already used as support for suspicious evidence ({[c[0] for c in clash]}). "
                            "Unverifiable benign explanations may not contradict your own findings; cite only the nodes that are actually explained.")

    def write_evidence(self, kind, summary, supporting_node_ids, confidence=0.5, benign_basis=None):
        if kind not in EVIDENCE_KINDS:
            raise ToolError(f"kind must be one of {EVIDENCE_KINDS}")
        ids = list(dict.fromkeys(str(i) for i in supporting_node_ids))[:30]
        for i in ids:
            lab = label_of(i)
            if lab not in SUPPORT_LABELS or not self._exists(i):
                raise ToolError(f"Supporting id '{i}' is not an existing Transaction/Account/Device in the graph. "
                                "Only cite ids you actually saw in tool results.")
        unseen = [i for i in ids if i not in self.seen]
        if unseen:
            raise ToolError(f"You have not seen ids {unseen[:6]} in any tool result of this investigation. Only cite ids returned by tools.")
        self._verify_claim(kind, ids)
        if kind == "BENIGN_EXPLANATION":
            self._verify_benign(benign_basis, ids)
        dup = self.q("MATCH (:Investigation {id:$inv})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(e:Evidence {kind:$k}) "
                     "MATCH (e)-[:SUPPORTED_BY]->(n) WITH e, collect(n.id) AS ids RETURN e.id, ids", {"inv": self.inv, "k": kind})
        for eid, have in dup:
            if set(have) == set(ids):
                return dict(evidence_id=eid, note="identical evidence already recorded; not duplicated")
        eid = self._next_id("EV-", "Evidence")
        conf = max(0.0, min(float(confidence), 1.0))
        self.g.query("MATCH (s:Step {id:$step}) CREATE (s)-[:PRODUCED]->(e:Evidence {id:$id, kind:$kind, summary:$summary, "
                     "confidence:$conf, investigation:$inv, basis:$basis, created_ts:timestamp()}) "
                     "WITH e UNWIND $ids AS nid MATCH (n) WHERE n.id = nid AND (n:Transaction OR n:Account OR n:Device) "
                     "CREATE (e)-[:SUPPORTED_BY]->(n)",
                     {"step": self.step_id, "id": eid, "kind": kind, "summary": summary[:1000], "conf": conf, "inv": self.inv, "ids": ids, "basis": benign_basis or ""})
        return dict(evidence_id=eid, supported_by=ids,
                    next="When all evidence for one structure is recorded, group it with write_pattern (evidence_ids=[EV-...]).")

    @staticmethod
    def _norm_ev(i):
        i = str(i).strip().upper()
        if i.startswith("EV-") and i[3:].isdigit():
            return f"EV-{int(i[3:])}"  # tolerate EV-001 style padding
        return i

    def write_pattern(self, pattern_type, summary, evidence_ids, confidence=0.5):
        if pattern_type not in PATTERN_TYPES:
            raise ToolError(f"pattern_type must be one of {PATTERN_TYPES}")
        ids = list(dict.fromkeys(self._norm_ev(i) for i in evidence_ids))
        kinds = {r[0]: r[1] for r in self.q("MATCH (:Investigation {id:$inv})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(e:Evidence) RETURN e.id, e.kind",
                                            {"inv": self.inv})}
        bad = [i for i in ids if i not in kinds]
        if bad:
            raise ToolError(f"Evidence ids {bad} were not written in this investigation. Existing: { {k: v for k, v in kinds.items()} }")
        wrong = [f"{i} ({kinds[i]})" for i in ids if kinds[i] not in PATTERN_EVIDENCE[pattern_type]]
        if wrong:
            ok = [i for i, k in kinds.items() if k in PATTERN_EVIDENCE[pattern_type]]
            raise ToolError(f"A {pattern_type} pattern can only be backed by evidence of kind {sorted(PATTERN_EVIDENCE[pattern_type])}; "
                            f"{wrong} do not fit. Evidence that fits: {ok}. Write one pattern per kind of structure.")
        pid = self._next_id("PAT-", "Pattern")
        conf = max(0.0, min(float(confidence), 1.0))
        self.g.query("MATCH (s:Step {id:$step}) CREATE (s)-[:PRODUCED]->(p:Pattern {id:$id, pattern_type:$pt, summary:$summary, "
                     "confidence:$conf, investigation:$inv, created_ts:timestamp()}) "
                     "WITH p UNWIND $ids AS eid MATCH (e:Evidence {id:eid}) CREATE (p)-[:BACKED_BY]->(e)",
                     {"step": self.step_id, "id": pid, "pt": pattern_type, "summary": summary[:1000], "conf": conf,
                      "inv": self.inv, "ids": ids})
        return dict(pattern_id=pid, backed_by=ids)

    def finish(self, summary):
        self.finished = summary
        return dict(status="finished")

    # ---- dispatch ------------------------------------------------------
    READS = READ_ONLY_FREE
    ANALYTIC = {"find_shared_devices", "analyze_fanout", "find_cycles", "trace_flow", "shortest_path"}

    def call(self, name, args):
        args = validate_args(name, args)
        args.pop("reason", None)
        res = getattr(self, name)(**args)
        if name in self.READS:
            self.seen.update(ID_RE.findall(json.dumps(res, default=str)))
        return res


def short_summary(result, limit=400) -> str:
    s = json.dumps(result, default=str)
    return s if len(s) <= limit else s[:limit] + "…"
