"""Tool-level tests with no LLM: validators must reject hallucinated provenance and accept real structure."""
import json

import pytest

from agents.tools import ToolBox, ToolError
from config import get_db
from seed import build_graph as bg

NAME = "vedatrace_test_tools"


@pytest.fixture(scope="module")
def env():
    g = get_db().select_graph(NAME)
    try:
        g.delete()
    except Exception:
        pass
    b = bg.Builder("dev")
    b.build()
    bg.load(b, g)
    g.query("CREATE (:Investigation {id:'INV-9', status:'running'})-[:HAS_STEP]->(:Step {id:'INV-9-S01', seq:1})")
    yield g, b.truth
    g.delete()


def box(g):
    tb = ToolBox(g, "INV-9")
    tb.step_id = "INV-9-S01"
    return tb


def test_unknown_ids_rejected(env):
    g, t = env
    tb = box(g)
    with pytest.raises(ToolError):
        tb.write_evidence("SHARED_DEVICE", "x", ["ACC-9999", "DEV-9999"], 0.5)


def test_unseen_ids_rejected_even_if_real(env):
    g, t = env
    tb = box(g)
    with pytest.raises(ToolError):
        tb.write_evidence("SHARED_DEVICE", "x", [t["sender"], t["hub"], t["shared_device"]], 0.5)


def test_valid_evidence_and_pattern_have_provenance(env):
    g, t = env
    tb = box(g)
    tb.call("find_shared_devices", dict(account_id=t["sender"], reason="r"))
    r = tb.write_evidence("SHARED_DEVICE", "shared device", [t["sender"], t["hub"], t["shared_device"]], 0.8)
    n = g.ro_query("MATCH (e:Evidence {id:$i})-[:SUPPORTED_BY]->(n) RETURN count(n)", params={"i": r["evidence_id"]}).result_set[0][0]
    assert n == 3
    p = tb.write_pattern("SHARED_DEVICE_CLUSTER", "cluster", [r["evidence_id"]], 0.8)
    assert g.ro_query("MATCH (:Pattern {id:$p})-[:BACKED_BY]->(:Evidence) RETURN count(*)", params={"p": p["pattern_id"]}).result_set[0][0] == 1
    with pytest.raises(ToolError):
        tb.write_pattern("RAPID_FANOUT", "bogus", ["EV-777"], 0.5)


def test_claims_are_verified_against_graph(env):
    g, t = env
    tb = box(g)
    r = tb.call("analyze_fanout", dict(account_id=t["hub"], reason="r"))
    with pytest.raises(ToolError):  # a normal single transfer is not a fan-out
        tb.write_evidence("RAPID_FANOUT", "x", ["TX-FLAGSHIP", t["sender"], t["hub"]], 0.5)
    ok = tb.write_evidence("RAPID_FANOUT", "burst", r["busiest_window"]["sample_tx_ids"] + [t["hub"]], 0.9)
    assert ok["evidence_id"].startswith("EV-")


def test_payroll_flagged_as_recurring_not_one_off(env):
    g, t = env
    tb = box(g)
    r = tb.call("analyze_fanout", dict(account_id=t["payroll_account"], reason="r"))
    assert r["separate_bursts_with_4plus_recipients"] >= 2 and r["recipient_overlap_between_bursts"] >= 0.9


def test_cycle_tool_finds_ring_with_bounded_depth(env):
    g, t = env
    r = box(g).call("find_cycles", dict(account_id=t["sender"], max_hops=99, reason="r"))
    assert r["max_hops"] == 5 and any(c["hops"] == 5 for c in r["cycles"])


def test_benign_explanations_are_checked_against_graph(env):
    g, t = env
    tb = box(g)
    r = tb.call("analyze_fanout", dict(account_id=t["payroll_account"], reason="r"))
    h = tb.call("analyze_fanout", dict(account_id=t["hub"], reason="r"))
    with pytest.raises(ToolError):  # no basis given
        tb.write_evidence("BENIGN_EXPLANATION", "legit", r["cite_ids_for_burst"], 0.8)
    with pytest.raises(ToolError):  # a one-off burst is not recurring payouts
        tb.write_evidence("BENIGN_EXPLANATION", "legit", h["cite_ids_for_burst"], 0.8, "RECURRING_PAYOUTS")
    ok = tb.write_evidence("BENIGN_EXPLANATION", "payroll", [t["payroll_account"]] + r["busiest_window"]["sample_tx_ids"][:3], 0.9, "RECURRING_PAYOUTS")
    assert ok["evidence_id"].startswith("EV-")


def test_cycle_search_not_crowded_out_by_benign_two_cycles(env):
    g, t = env
    r = box(g).call("find_cycles", dict(account_id=t["sender"], max_hops=5, reason="r"))
    hops = sorted({c["hops"] for c in r["cycles"]})
    assert 2 in hops and 5 in hops  # household back-and-forth AND the long ring are both reported
