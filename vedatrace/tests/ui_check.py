"""Headless browser check of the demo UI against the real API/FalkorDB (not part of the fast pytest run).

  python tests/ui_check.py [--live] [--shots DIR]
Without --live it replays the newest finished investigation (and its report, if any). With --live it clicks
"Run investigation" and waits for the real agent to finish (minutes with a local model), then generates the report.
"""
import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--live", action="store_true")
ap.add_argument("--url", default="http://localhost:8000")
ap.add_argument("--shots", default=os.path.join(os.path.dirname(__file__), "..", "reports", "ui"))
ap.add_argument("--timeout", type=int, default=1800)
a = ap.parse_args()
os.makedirs(a.shots, exist_ok=True)
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
    pg.goto(a.url)
    pg.wait_for_function("document.getElementById('b-db').textContent.includes('nodes')", timeout=15000)
    res["badges"] = {i: pg.inner_text("#" + i) for i in ("b-db", "b-llm", "b-mem")}
    res["cytoscape_loaded"] = pg.evaluate("typeof cytoscape === 'function' && typeof marked === 'object' || typeof marked === 'function'")
    if a.live:
        pg.click("#run")
        pg.wait_for_function("document.querySelectorAll('#steps li').length > 0", timeout=240000)
        res["first_step_seen"] = True
        pg.screenshot(path=os.path.join(a.shots, "live_running.png"))
        pg.wait_for_function("!document.getElementById('make-report').disabled", timeout=a.timeout * 1000)
    else:
        pg.wait_for_function("document.querySelectorAll('#past option').length > 1", timeout=10000)
        pg.select_option("#past", index=1)
        pg.wait_for_function("document.querySelectorAll('#steps li').length > 0", timeout=20000)
        pg.wait_for_timeout(4000)
    res["steps"] = pg.evaluate("document.querySelectorAll('#steps li').length")
    res["findings"] = pg.evaluate("document.querySelectorAll('#findings .finding').length")
    res["graph_nodes"] = pg.evaluate("cy.nodes().length")
    res["node_types"] = pg.evaluate("[...new Set(cy.nodes().map(n => n.data('type')))]")
    pg.screenshot(path=os.path.join(a.shots, "investigation.png"))
    if a.live or pg.evaluate("document.querySelector('#report a.cite') !== null"):
        if a.live or not pg.evaluate("document.querySelector('#report a.cite')"):
            pg.click("#make-report")
            pg.wait_for_function("document.querySelectorAll('#report a.cite').length > 0", timeout=600000)
        res["citations"] = pg.evaluate("document.querySelectorAll('#report a.cite').length")
        res["recommendation"] = pg.evaluate("document.querySelector('#report .rec') && document.querySelector('#report .rec').textContent")
        # click the first evidence/pattern citation and check the graph focuses it
        cid = pg.evaluate("[...document.querySelectorAll('#report a.cite')].map(a => a.textContent).find(t => /^(EV|PAT)-/.test(t))")
        pg.evaluate("(id) => [...document.querySelectorAll('#report a.cite')].find(a => a.textContent === id).click()", cid)
        pg.wait_for_timeout(800)
        res["clicked_citation"] = cid
        res["graph_focused"] = pg.evaluate("(id) => cy.getElementById(id).hasClass('focus')", cid)
        res["inspector_open"] = pg.evaluate("!document.getElementById('inspector').classList.contains('hidden')")
        res["download_links"] = pg.evaluate("[document.getElementById('dl-md').href, document.getElementById('dl-json').href]")
        pg.screenshot(path=os.path.join(a.shots, "report.png"))
    res["js_errors"] = errors[:5]
    b.close()
print(json.dumps(res, indent=1))
ok = res.get("steps", 0) > 0 and res.get("graph_nodes", 0) > 0 and not res["js_errors"]
sys.exit(0 if ok else 1)
