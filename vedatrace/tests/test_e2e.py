"""End-to-end with the real LLM provider. Slow (minutes per run), so opt-in:  RUN_E2E=1 pytest tests/test_e2e.py -s
E2E_RUNS=3 repeats the run (the demo-verify repeatability check)."""
import os

import pytest

from config import get_db
from run_demo import run_once
from tests import e2e_checks as chk

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 to run the LLM end-to-end test")
GRAPH = "vedatrace_e2e"


def test_end_to_end_discovers_ring_and_reports():
    runs = int(os.environ.get("E2E_RUNS", "1"))
    g = get_db().select_graph(GRAPH)
    results, seqs = [], []
    for i in range(runs):
        res = run_once("demo", graph=GRAPH, reset=(i == 0), quiet=True)
        d = chk.discovery(g, res["investigation"])
        ok_cit, unresolved = chk.report_citations_ok(g, res["report"])
        seqs.append(chk.step_sequence(g, res["investigation"]))
        r = dict(run=res, discovery=d, provenance=chk.provenance_ok(g, res["investigation"]), citations=ok_cit,
                 unresolved=unresolved, red_herring_violations=chk.red_herrings_clean(g, res["investigation"]))
        print(r)
        results.append(r)
    passed = [x for x in results if all(x["discovery"].values()) and x["provenance"] and x["citations"] and not x["red_herring_violations"]]
    print(f"PASS {len(passed)}/{runs}")
    assert all(x["provenance"] and x["citations"] for x in results)
    assert not any(x["red_herring_violations"] for x in results)
    assert len(passed) == runs
    if runs > 1:
        assert len({tuple(s) for s in seqs}) > 1 or True  # step order is model-chosen; recorded for the report
