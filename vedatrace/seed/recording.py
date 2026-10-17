"""Export / load a RECORDED real investigation (+ report) so the demo can be replayed without an LLM key.

This is NOT seeding: `seed.reset` still creates raw facts only. A recording is the output of an earlier real
Investigator + Reporter run, exported from the graph and re-attached to the (deterministic) seed nodes by id.
Loaded nodes are flagged recorded=true so the UI/API can say so honestly.

  python -m seed.recording export INV-1 --out demo_runs/flagship.json
  python -m seed.recording load demo_runs/flagship.json        # after: python -m seed.reset --size demo
"""
import argparse
import json
import os
import re
from collections import defaultdict

from config import GRAPH_NAME, get_db

AGENT_LABELS = ["Investigation", "Step", "Evidence", "Pattern", "Report", "PriorCase"]
SEED_LABELS = ["Customer", "Account", "Device", "Transaction", "Policy"]
ALLOWED_LABELS = set(AGENT_LABELS + SEED_LABELS)
REL_RE = re.compile(r"^[A-Z][A-Z_]{1,40}$")


def _graph(graph_name=None):
    return get_db().select_graph(graph_name or GRAPH_NAME)


def export_run(inv: str, graph_name=None) -> dict:
    g = _graph(graph_name)
    ro = lambda c, p=None: g.ro_query(c, params=p or {}).result_set
    if not ro("MATCH (i:Investigation {id:$i}) RETURN i.id", {"i": inv}):
        raise SystemExit(f"Investigation {inv} not found")
    status = ro("MATCH (i:Investigation {id:$i}) RETURN i.status", {"i": inv})[0][0]
    if status != "finished":
        raise SystemExit(f"{inv} is '{status}', only finished investigations can be recorded")
    # everything reachable from the investigation through agent-written nodes: steps, evidence, patterns, reports
    nodes = ro("MATCH (i:Investigation {id:$i}) "
               "OPTIONAL MATCH (i)-[:HAS_STEP]->(s:Step) OPTIONAL MATCH (s)-[:PRODUCED]->(x) "
               "OPTIONAL MATCH (r:Report)-[:ABOUT]->(i) "
               "WITH collect(DISTINCT i) + collect(DISTINCT s) + collect(DISTINCT x) + collect(DISTINCT r) AS ns "
               "UNWIND ns AS n WITH DISTINCT n WHERE n IS NOT NULL RETURN labels(n)[0], properties(n)", {"i": inv})
    nodes = [dict(label=l, props=p) for l, p in nodes if l in AGENT_LABELS]
    ids = [n["props"]["id"] for n in nodes]
    edges = ro("MATCH (a)-[r]->(b) WHERE a.id IN $ids AND (b.id IN $ids OR labels(b)[0] IN $seed) "
               "RETURN labels(a)[0], a.id, type(r), properties(r), labels(b)[0], b.id", {"ids": ids, "seed": SEED_LABELS})
    # a Pattern/Evidence may also point at other agent nodes of this run (BACKED_BY): covered by b.id IN ids
    edges = [dict(a_label=r[0], a=r[1], type=r[2], props=r[3], b_label=r[4], b=r[5]) for r in edges]
    reports = [n["props"]["id"] for n in nodes if n["label"] == "Report"]
    return dict(format=1, investigation=inv, reports=reports, nodes=nodes, edges=edges)


def load_run(path: str, graph_name=None) -> dict:
    data = json.load(open(path, encoding="utf-8"))
    g = _graph(graph_name)
    ro = lambda c, p=None: g.ro_query(c, params=p or {}).result_set
    if not ro("MATCH (a:Account) RETURN count(a)")[0][0]:
        raise SystemExit("The graph has no seed data. Run: python -m seed.reset --size demo")
    existing = ro("MATCH (n) WHERE labels(n)[0] IN $l RETURN count(n)", {"l": AGENT_LABELS})[0][0]
    if existing:
        raise SystemExit(f"The graph already holds {existing} agent-written nodes. Reset first: python -m seed.reset --size demo")

    by_label = defaultdict(list)
    for n in data["nodes"]:
        if n["label"] not in ALLOWED_LABELS or n["label"] in SEED_LABELS:
            raise SystemExit(f"Refusing to load node label {n['label']!r}")
        props = dict(n["props"])
        if n["label"] in ("Investigation", "Report"):
            props["recorded"] = True
        by_label[n["label"]].append(props)
    for label, rows in by_label.items():  # label comes from the allowlist above, values are parameters
        g.query(f"UNWIND $rows AS p CREATE (n:{label}) SET n = p", params={"rows": rows})

    groups = defaultdict(list)
    for e in data["edges"]:
        if e["a_label"] not in ALLOWED_LABELS or e["b_label"] not in ALLOWED_LABELS or not REL_RE.match(e["type"]):
            raise SystemExit(f"Refusing to load edge {e['a_label']}-{e['type']}->{e['b_label']}")
        groups[(e["a_label"], e["type"], e["b_label"])].append(dict(a=e["a"], b=e["b"], p=e["props"] or {}))
    made = 0
    for (la, t, lb), rows in groups.items():
        res = g.query(f"UNWIND $rows AS r MATCH (a:{la} {{id: r.a}}), (b:{lb} {{id: r.b}}) CREATE (a)-[x:{t}]->(b) SET x = r.p",
                      params={"rows": rows})
        made += res.relationships_created
    wanted = len(data["edges"])
    if made != wanted:
        raise SystemExit(f"Only {made}/{wanted} edges attached: the seed does not match the recording (wrong --size or seed changed)")
    return dict(nodes=len(data["nodes"]), edges=made, investigation=data["investigation"], reports=data["reports"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("investigation"); e.add_argument("--out", required=True)
    l = sub.add_parser("load"); l.add_argument("path")
    a = ap.parse_args()
    if a.cmd == "export":
        d = export_run(a.investigation)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(d, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"exported {len(d['nodes'])} nodes, {len(d['edges'])} edges, reports={d['reports']} -> {a.out}")
    else:
        print("loaded", load_run(a.path))
