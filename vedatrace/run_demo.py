"""One-command end-to-end demo: reset seed -> Investigator (own process) -> Reporter (own process) -> verification summary.

  python run_demo.py [--size demo|dev] [--tx TX-FLAGSHIP] [--keep]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def sh(args, env=None, stream=False):
    e = dict(os.environ, **(env or {}))
    p = subprocess.run([sys.executable, "-u", *args], cwd=HERE, env=e, text=True, capture_output=not stream)
    return p


def run_once(size="demo", tx="TX-FLAGSHIP", reset=True, graph=None, quiet=False):
    env = {"VEDATRACE_GRAPH": graph} if graph else {}
    t0 = time.time()
    if reset:
        p = sh(["-m", "seed.reset", "--size", size], env)
        if p.returncode:
            raise RuntimeError("reset failed: " + (p.stderr or p.stdout)[-500:])
    p = sh(["-m", "agents.investigator", "--tx", tx], env)
    if not quiet:
        print(p.stdout)
    m = re.search(r"INVESTIGATION_ID=(INV-\d+)", p.stdout or "")
    if p.returncode or not m:
        raise RuntimeError("investigator failed: " + ((p.stderr or "") + (p.stdout or ""))[-800:])
    inv = m.group(1)
    p = sh(["-m", "agents.reporter", "--investigation", inv], env)  # separate process; only the investigation id crosses over
    if not quiet:
        print(p.stdout)
    m = re.search(r"REPORT_ID=(RPT-\d+)", p.stdout or "")
    if p.returncode or not m:
        raise RuntimeError("reporter failed: " + ((p.stderr or "") + (p.stdout or ""))[-800:])
    return dict(investigation=inv, report=m.group(1), seconds=round(time.time() - t0))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="demo", choices=["dev", "demo"])
    ap.add_argument("--tx", default="TX-FLAGSHIP")
    ap.add_argument("--keep", action="store_true", help="do not reseed first")
    a = ap.parse_args()
    res = run_once(a.size, a.tx, reset=not a.keep)
    print(json.dumps(res))
