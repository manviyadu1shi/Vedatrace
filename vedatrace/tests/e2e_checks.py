"""Ground-truth based checks for an investigation + report. Used by tests and by demo-verify. Agent code never imports this."""
import json
import os
import re

from agents.reporter import CITE_RE, resolve

GT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed", "ground_truth.json")


def truth():
    return json.load(open(GT))


def evidence(g, inv):
    rows = g.ro_query("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(e:Evidence) "
                      "OPTIONAL MATCH (e)-[:SUPPORTED_BY]->(n) RETURN e.id, e.kind, collect(n.id)", params={"i": inv}).result_set
    return [dict(id=r[0], kind=r[1], supports=set(r[2])) for r in rows]


def patterns(g, inv):
    rows = g.ro_query("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(:Step)-[:PRODUCED]->(p:Pattern) "
                      "OPTIONAL MATCH (p)-[:BACKED_BY]->(e:Evidence) RETURN p.id, p.pattern_type, collect(e.id)", params={"i": inv}).result_set
    return [dict(id=r[0], type=r[1], evidence=r[2]) for r in rows]


def discovery(g, inv):
    t = truth()
    ring = {t["sender"], t["hub"], t["device_cluster_member"], *t["mules"], *t["intermediaries"]}
    ev, pats = evidence(g, inv), patterns(g, inv)
    tx_sender = {r[0]: r[1] for r in g.ro_query("MATCH (a:Account)-[:SENT]->(x:Transaction) RETURN x.id, a.id").result_set
                 if r[1] in ring}
    def acc_of(e):
        return {s for s in e["supports"] if s.startswith("ACC-")} | {tx_sender[s] for s in e["supports"] if s in tx_sender}
    found = dict(
        shared_device=any(e["kind"] == "SHARED_DEVICE" and t["shared_device"] in e["supports"] and len(acc_of(e) & {t["sender"], t["hub"], t["device_cluster_member"]}) >= 2 for e in ev),
        fanout=any(e["kind"] in ("RAPID_FANOUT", "STRUCTURING_AMOUNTS") and t["hub"] in acc_of(e) for e in ev),
        circular=any(e["kind"] == "CIRCULAR_FLOW" and len(acc_of(e) & ring) >= 3 for e in ev),
        pattern_shared=any(p["type"] == "SHARED_DEVICE_CLUSTER" for p in pats),
        pattern_fanout=any(p["type"] in ("RAPID_FANOUT", "STRUCTURING") for p in pats),
        pattern_circular=any(p["type"] == "CIRCULAR_FLOW" for p in pats),
    )
    return found


def red_herrings_clean(g, inv):
    """Benign structures must not be the support of a non-BENIGN evidence."""
    t = truth()
    benign = {t["payroll_account"], t["marketplace_account"], *t["payroll_employees"], *t["marketplace_sellers"]}
    for hh in t["benign_household_devices"] + t["benign_office_devices"]:
        benign |= {hh["device"], *hh["accounts"]}
    for pair in t["benign_rent_cycles"]:
        benign |= set(pair)
    benign |= set(t["benign_split_cycle"])
    bad = []
    for e in evidence(g, inv):
        if e["kind"] != "BENIGN_EXPLANATION" and (e["supports"] & {t["payroll_account"], t["marketplace_account"]}):
            bad.append(e["id"])
    return bad


def provenance_ok(g, inv):
    ev, pats = evidence(g, inv), patterns(g, inv)
    return all(e["supports"] for e in ev) and all(p["evidence"] for p in pats)


def report_citations_ok(g, report_id):
    md = g.ro_query("MATCH (r:Report {id:$r}) RETURN r.markdown", params={"r": report_id}).result_set[0][0]
    cited = set(CITE_RE.findall(md))
    unresolved = [c for c in cited if not resolve(g, c)]
    return not unresolved and len(cited) > 0, unresolved


def step_sequence(g, inv):
    return [r[0] for r in g.ro_query("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(s:Step) RETURN s.tool ORDER BY s.seq", params={"i": inv}).result_set]
