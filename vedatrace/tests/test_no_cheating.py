"""Static + structural guards: the agents must not contain the answer, share state, or touch provider SDKs directly."""
import ast
import glob
import json
import os
import re

import pytest

from config import get_db
from seed import build_graph as bg

ROOT = os.path.dirname(os.path.dirname(__file__))
AGENT_FILES = [f for d in ("agents", "api", "memory") for f in glob.glob(os.path.join(ROOT, d, "*.py"))]
GT = os.path.join(ROOT, "seed", "ground_truth.json")


def src(path):
    return open(path).read()


def test_seed_creates_no_agent_nodes():
    g = get_db().select_graph("vedatrace_test_nc")
    try:
        g.delete()
    except Exception:
        pass
    bg.build("dev", g, write_truth=False)
    for label in ("Investigation", "Step", "Evidence", "Pattern", "Report", "Finding"):
        assert g.ro_query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0] == 0
    g.delete()


def test_agent_code_does_not_reference_ground_truth_or_ring_ids():
    forbidden = [r"ground_truth", r"\bis_fraud\b", r"\bring_id\b", r"\bmule", r"seed\.build_graph"]
    if os.path.exists(GT):
        truth = json.load(open(GT))
        ids = set(re.findall(r"(?:ACC|DEV|CUST)-\d+", json.dumps(truth)))
        forbidden += [re.escape(i) for i in ids]
    for f in AGENT_FILES:
        text = src(f)
        for pat in forbidden:
            assert not re.search(pat, text), f"{os.path.basename(f)} matches forbidden pattern {pat}"


def imports_of(path):
    tree = ast.parse(src(path))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out.add(n.module or "")
    return out


def test_reporter_is_decoupled_from_investigator():
    imps = imports_of(os.path.join(ROOT, "agents", "reporter.py"))
    assert not any("investigator" in i for i in imps), imps
    assert not any(i.startswith("agents.tools") for i in imps), "reporter must not use the investigator's tools"


def test_only_llm_module_imports_provider_sdks():
    for f in AGENT_FILES + [os.path.join(ROOT, "run_demo.py")]:
        if not os.path.exists(f) or os.path.basename(f) in ("llm.py",):
            continue
        bad = {i for i in imports_of(f) if i.split(".")[0] in ("anthropic", "openai", "ollama")}
        assert not bad, f"{f} imports provider SDK {bad}"


def test_forbidden_env_name_not_used():
    name = "ANTHROPIC" + "_API_KEY"
    for f in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True):
        if "/tests/" in f:
            continue
        assert name not in src(f), f


def test_cypher_is_parameterized():
    """f-strings that build Cypher may only interpolate fixed labels/patterns and clamped-int path bounds, never tool arguments."""
    allowed = {"label", "lab", "la", "lb", "hops", "2 * hops", "pat", "len(prefix)", "lo", "hi", "limit"}
    text = src(os.path.join(ROOT, "agents", "tools.py"))
    for m in re.finditer(r'f"([^"\n]*(?:MATCH|CREATE|MERGE|RETURN)[^"\n]*)"', text):
        body = m.group(1).replace("{{", "").replace("}}", "")
        for expr in re.findall(r"\{([^}]*)\}", body):
            assert expr.strip() in allowed, f"suspicious interpolation into Cypher: {expr}"
