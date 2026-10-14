import json
import os

import pytest

from config import get_db
from seed import build_graph as bg

TEST_GRAPH = "vedatrace_test_seed"
AGENT_LABELS = ["Investigation", "Step", "Evidence", "Pattern", "Report", "Finding"]


def _dump(g):
    nodes = g.ro_query("MATCH (n) RETURN labels(n)[0], properties(n) ORDER BY labels(n)[0], n.id").result_set
    edges = g.ro_query("MATCH (a)-[r]->(b) RETURN a.id, type(r), b.id ORDER BY a.id, type(r), b.id").result_set
    return [[l, sorted(p.items())] for l, p in nodes], edges


@pytest.fixture(scope="module")
def graphs():
    db = get_db()
    out = []
    for i in range(2):
        g = db.select_graph(f"{TEST_GRAPH}_{i}")
        try:
            g.delete()
        except Exception:
            pass
        bg.build("dev", g, write_truth=False)
        out.append(g)
    yield out
    for g in out:
        g.delete()


def count(g, label):
    return g.ro_query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]


def test_counts_and_flagship(graphs):
    g = graphs[0]
    assert count(g, "Policy") == len(bg.POLICIES) >= 5
    assert count(g, "Account") > 30 and count(g, "Transaction") > 100 and count(g, "Device") > 5
    r = g.ro_query("MATCH (a:Account)-[:SENT]->(t:Transaction {id:'TX-FLAGSHIP'})-[:TO]->(b:Account) RETURN a.id, b.id, t.amount").result_set
    assert len(r) == 1 and r[0][0] != r[0][1]


def test_no_agent_written_labels(graphs):
    for label in AGENT_LABELS:
        assert count(graphs[0], label) == 0, label


def test_no_revealing_properties(graphs):
    keys = {k for (k,) in graphs[0].ro_query("MATCH (n) UNWIND keys(n) AS k RETURN DISTINCT k").result_set}
    assert not {k for k in keys if "fraud" in k.lower() or "ring" in k.lower() or "mule" in k.lower()}


def test_deterministic(graphs):
    assert _dump(graphs[0]) == _dump(graphs[1])


def test_demo_size_targets():
    b = bg.Builder("demo")
    b.build()
    assert 1400 <= len(b.accounts) <= 1600
    assert 7000 <= len(b.txs) <= 9000
    assert 300 <= len(b.devices) <= 500
