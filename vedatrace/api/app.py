"""FastAPI backend for the demo UI. The API never holds agent state: the Investigator and Reporter run as separate processes
and everything the UI shows (steps, evidence, patterns, report) is read back from FalkorDB."""
import asyncio
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents import llm
from agents.tools import label_of
from config import GRAPH_NAME, get_graph

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "ui")
app = FastAPI(title="VedaTrace")

JOBS: dict = {}  # investigation id -> {"proc", "log", "done", "rc"}
JOB_LOCK = threading.Lock()
READONLY = os.environ.get("VEDATRACE_READONLY") == "1"  # public replay deployment: no LLM calls, no resets


RESP_CACHE: dict = {}  # key -> (body, media_type, expires_at or None)
RECORDED: set = set()  # ids of recorded investigations: immutable, so their API responses can be cached (a remote FalkorDB costs ~1 s per query)
LIST_TTL = 15  # seconds: the investigation list changes when an admin starts a run
ADMIN_TOKEN = os.environ.get("VEDATRACE_ADMIN_TOKEN") or None  # lets the owner run live investigations on a read-only deployment


def is_admin(request: Request) -> bool:
    return bool(ADMIN_TOKEN) and hmac.compare_digest(request.headers.get("x-admin-token", ""), ADMIN_TOKEN)


def _cache_ttl(path: str):
    """None = do not cache, 0 = cache forever, >0 = cache for that many seconds."""
    if path == "/api/health" or path.startswith("/api/admin") or path.endswith("/stream") or path.endswith("/download"):
        return None
    if path == "/api/investigations":
        return LIST_TTL
    m = re.match(r"^/api/(?:investigations|node)/(INV-\d+)", path)
    if m and m.group(1) not in RECORDED:  # a run that may still be in progress
        return None
    return 0


@app.middleware("http")
async def readonly_cache(request: Request, call_next):
    path = request.url.path
    ttl = _cache_ttl(path) if (READONLY and request.method == "GET" and path.startswith("/api/")) else None
    if ttl is None:
        return await call_next(request)
    key = path + "?" + request.url.query
    hit = RESP_CACHE.get(key)
    if hit and (hit[2] is None or hit[2] > time.time()):
        return Response(content=hit[0], media_type=hit[1])
    resp = await call_next(request)
    if resp.status_code != 200:
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    media = resp.media_type or "application/json"
    RESP_CACHE[key] = (body, media, time.time() + ttl if ttl else None)
    return Response(content=body, media_type=media)


def _prewarm():
    """Fill the cache in the background after startup so the first visitor does not wait for the database round trips."""
    try:
        from fastapi.testclient import TestClient
        c = TestClient(app)
        runs = c.get("/api/investigations").json()
        runs = runs if isinstance(runs, list) else []
        for r in runs:
            if r.get("recorded"):
                RECORDED.add(r["id"])
        for r in runs:
            inv = r["id"]
            if inv not in RECORDED:
                continue
            c.get(f"/api/investigations/{inv}/trace"); c.get(f"/api/investigations/{inv}/memory")
            g = c.get(f"/api/investigations/{inv}/graph").json()
            for rid in r.get("reports", []):
                c.get(f"/api/reports/{rid}")
            for n in g.get("nodes", []):
                c.get(f"/api/node/{n['id']}")
    except Exception as e:  # never block or crash the app because of warming
        print("prewarm skipped:", type(e).__name__, e)


@app.on_event("startup")
def _start_prewarm():
    if READONLY:
        threading.Thread(target=_prewarm, daemon=True).start()


def deny_if_readonly(request: Request, allow_admin: bool = False):
    if READONLY and not (allow_admin and is_admin(request)):
        raise HTTPException(403, "This is a read-only replay deployment: it shows a recorded investigation. Run the project locally with an LLM key to investigate live."
                                 + (" The owner can unlock live runs with the admin token." if ADMIN_TOKEN else ""))


def g():
    return get_graph()


def q(cypher, params=None):
    return g().ro_query(cypher, params=params or {}).result_set


def db_error_response(e):
    return JSONResponse(status_code=503, content=dict(error="FalkorDB is not reachable", detail=f"{type(e).__name__}: {e}",
                                                       hint="Start it with: docker start vedatrace-falkordb"))


# ---- health -----------------------------------------------------------------------
def llm_status():
    prov = llm.provider()
    if prov == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        try:
            with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=2) as r:
                names = [m["name"] for m in json.load(r).get("models", [])]
            ok = any(llm.model_name() in n for n in names)
            return dict(provider=prov, model=llm.model_name(), ok=ok, detail=None if ok else f"model {llm.model_name()} not pulled")
        except Exception as e:
            return dict(provider=prov, model=llm.model_name(), ok=False, detail=f"Ollama unreachable at {host}: {type(e).__name__}")
    key_var = "OPENAI_API_KEY" if prov == "openai" else "VEDATRACE_API_KEY"
    ok = bool(os.environ.get(key_var))
    return dict(provider=prov, model=llm.model_name(), ok=ok, detail=None if ok else f"{key_var} is not set")


def memory_status():
    try:
        from memory import graphiti_mem
        return graphiti_mem.status()
    except Exception as e:
        return dict(enabled=False, backend=None, detail=f"memory unavailable: {type(e).__name__}")


@app.get("/api/admin/check")
def admin_check(request: Request):
    return dict(ok=is_admin(request))


@app.get("/api/health")
def health():
    out = dict(graph=GRAPH_NAME, llm=llm_status(), memory=memory_status(), readonly=READONLY, admin_enabled=bool(ADMIN_TOKEN))
    try:
        n = q("MATCH (n) RETURN count(n)")[0][0]
        out["falkordb"] = dict(ok=True, nodes=n)
    except Exception as e:
        out["falkordb"] = dict(ok=False, detail=f"{type(e).__name__}: {e}")
    return out


# ---- investigations -------------------------------------------------------------------
class StartReq(BaseModel):
    tx_id: str = "TX-FLAGSHIP"


def _reader(inv_holder, proc, log):
    for line in proc.stdout:
        log.append(line.rstrip())
        m = re.match(r"\[(INV-\d+)\]", line)
        if m and not inv_holder["id"]:
            inv_holder["id"] = m.group(1)
    proc.wait()
    inv_holder["done"] = True
    inv_holder["rc"] = proc.returncode


@app.post("/api/investigations")
def start_investigation(req: StartReq, request: Request):
    deny_if_readonly(request, allow_admin=True)
    if not re.fullmatch(r"TX-[A-Za-z0-9]+", req.tx_id):
        raise HTTPException(400, "Transaction ids look like TX-00123 or TX-FLAGSHIP")
    try:
        if not q("MATCH (t:Transaction {id:$id}) RETURN t.id", {"id": req.tx_id}):
            raise HTTPException(404, f"Transaction {req.tx_id} not found. Reset the demo data?")
    except HTTPException:
        raise
    except Exception as e:
        return db_error_response(e)
    with JOB_LOCK:
        if any(not j["holder"]["done"] for j in JOBS.values()):
            raise HTTPException(409, "An investigation is already running.")
        cmd = [sys.executable, "-u", "-m", "agents.investigator", "--tx", req.tx_id]
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        holder, log = dict(id=None, done=False, rc=None), []
        threading.Thread(target=_reader, args=(holder, proc, log), daemon=True).start()
        key = f"job-{len(JOBS) + 1}"
        JOBS[key] = dict(holder=holder, log=log, proc=proc)
    deadline = time.time() + 20
    while time.time() < deadline and not holder["id"] and not holder["done"]:
        time.sleep(0.2)
    if not holder["id"]:
        tail = "\n".join(log[-8:])
        return JSONResponse(status_code=502, content=dict(error="Investigation failed to start", detail=tail[-800:]))
    return dict(id=holder["id"], tx_id=req.tx_id)


@app.get("/api/investigations")
def list_investigations():
    try:
        from agents.investigator import sweep_stale
        sweep_stale(get_graph())
        rows = q("MATCH (i:Investigation) OPTIONAL MATCH (r:Report)-[:ABOUT]->(i) "
                 "RETURN i.id, i.target_tx, i.status, i.provider, i.model, i.steps, collect(r.id), i.recorded ORDER BY toInteger(substring(i.id,4)) DESC LIMIT 30")
    except Exception as e:
        return db_error_response(e)
    return [dict(id=r[0], tx_id=r[1], status=r[2], provider=r[3], model=r[4], steps=r[5], reports=r[6], recorded=bool(r[7])) for r in rows]


def snapshot(inv, since_seq, since_ev, since_pat):
    steps = q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(s:Step) WHERE s.seq > $n RETURN s.id, s.seq, s.tool, s.args, s.reason, s.ok, s.result_summary ORDER BY s.seq",
              {"i": inv, "n": since_seq})
    ev = q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(s:Step)-[:PRODUCED]->(e:Evidence) WHERE toInteger(substring(e.id,3)) > $n "
           "RETURN e.id, e.kind, e.summary, e.confidence, s.id ORDER BY toInteger(substring(e.id,3))", {"i": inv, "n": since_ev})
    pat = q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(s:Step)-[:PRODUCED]->(p:Pattern) WHERE toInteger(substring(p.id,4)) > $n "
            "RETURN p.id, p.pattern_type, p.summary, p.confidence, s.id ORDER BY toInteger(substring(p.id,4))", {"i": inv, "n": since_pat})
    st = q("MATCH (i:Investigation {id:$i}) RETURN i.status, i.summary, i.steps", {"i": inv})
    return steps, ev, pat, (st[0] if st else None)


@app.get("/api/investigations/{inv}/trace")
def trace(inv: str):
    """The full step/evidence/pattern trail as JSON (same shape as the SSE events); the UI uses it for a paced replay of a finished run."""
    try:
        steps, ev, pat, st = snapshot(inv, 0, 0, 0)
    except Exception as e:
        return db_error_response(e)
    if st is None:
        raise HTTPException(404, "Unknown investigation")
    return dict(status=st[0], summary=st[1],
                steps=[dict(id=s[0], seq=s[1], tool=s[2], args=json.loads(s[3] or "{}"), reason=s[4], ok=s[5], summary=s[6]) for s in steps],
                evidence=[dict(id=e[0], kind=e[1], summary=e[2], confidence=e[3], step=e[4]) for e in ev],
                patterns=[dict(id=p[0], type=p[1], summary=p[2], confidence=p[3], step=p[4]) for p in pat])


@app.get("/api/investigations/{inv}/stream")
async def stream(inv: str):
    async def gen():
        seq = ev_n = pat_n = 0
        idle_after_done = 0
        yield "retry: 3000\n\n"
        while True:
            try:
                steps, ev, pat, st = await run_in_threadpool(snapshot, inv, seq, ev_n, pat_n)
            except Exception as e:
                yield f"event: error\ndata: {json.dumps(dict(message=f'FalkorDB error: {type(e).__name__}: {e}'))}\n\n"
                return
            if st is None:
                yield f"event: error\ndata: {json.dumps(dict(message=f'Unknown investigation {inv}'))}\n\n"
                return
            for s in steps:
                seq = s[1]
                yield "event: step\ndata: " + json.dumps(dict(id=s[0], seq=s[1], tool=s[2], args=json.loads(s[3] or "{}"), reason=s[4], ok=s[5], summary=s[6])) + "\n\n"
            for e in ev:
                ev_n = int(e[0][3:])
                yield "event: evidence\ndata: " + json.dumps(dict(id=e[0], kind=e[1], summary=e[2], confidence=e[3], step=e[4])) + "\n\n"
            for p in pat:
                pat_n = int(p[0][4:])
                yield "event: pattern\ndata: " + json.dumps(dict(id=p[0], type=p[1], summary=p[2], confidence=p[3], step=p[4])) + "\n\n"
            status = st[0]
            yield "event: status\ndata: " + json.dumps(dict(status=status, steps=seq)) + "\n\n"
            if status != "running":
                idle_after_done += 1
                if idle_after_done >= 2 and not (steps or ev or pat):
                    yield "event: done\ndata: " + json.dumps(dict(status=status, summary=st[1])) + "\n\n"
                    return
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/investigations/{inv}/memory")
def recalled(inv: str):
    r = q("MATCH (i:Investigation {id:$i}) RETURN i.recalled", {"i": inv})
    return dict(recalled=json.loads(r[0][0]) if r and r[0][0] else [])


NODE_CAP = 260


@app.get("/api/investigations/{inv}/graph")
def graph_view(inv: str):
    """Neighborhood of everything the agent touched, plus the evidence trail it wrote."""
    try:
        if not q("MATCH (i:Investigation {id:$i}) RETURN i.id", {"i": inv}):
            raise HTTPException(404, "Unknown investigation")
        nodes, edges = {}, set()

        def add(nid, label, props, **extra):
            if nid not in nodes and len(nodes) < NODE_CAP:
                nodes[nid] = dict(id=nid, type=label, label=nid, **extra, props={k: v for k, v in props.items() if k not in ("markdown", "json")})
            return nid in nodes

        # touched nodes with the first step that examined them
        touched = q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(s:Step)-[:EXAMINED]->(n) RETURN n.id, labels(n)[0], properties(n), min(s.seq)", {"i": inv})
        tx_target = q("MATCH (i:Investigation {id:$i}) RETURN i.target_tx", {"i": inv})[0][0]
        base = q("MATCH (a:Account)-[:SENT]->(t:Transaction {id:$t})-[:TO]->(b:Account) RETURN a, t, b", {"t": tx_target})
        for r in base:
            for n in r:
                add(n.properties["id"], list(n.labels)[0], n.properties, touched=True, step=0)
            edges.add((r[0].properties["id"], r[1].properties["id"], "SENT"))
            edges.add((r[1].properties["id"], r[2].properties["id"], "TO"))
        for nid, lab, props, seq in touched:
            if lab in ("Account", "Transaction", "Device", "Customer"):
                add(nid, lab, props, touched=True, step=seq)
        # supports of evidence
        sup = q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(s:Step)-[:PRODUCED]->(e:Evidence) OPTIONAL MATCH (e)-[:SUPPORTED_BY]->(n) "
                "RETURN e, s.seq, collect(n)", {"i": inv})
        for e, seq, ns in sup:
            add(e.properties["id"], "Evidence", e.properties, touched=True, step=seq)
            for n in ns:
                nid = n.properties["id"]
                add(nid, list(n.labels)[0], n.properties, touched=nid in nodes and nodes[nid].get("touched", False), step=seq)
                edges.add((e.properties["id"], nid, "SUPPORTED_BY"))
        pats = q("MATCH (:Investigation {id:$i})-[:HAS_STEP]->(s:Step)-[:PRODUCED]->(p:Pattern) OPTIONAL MATCH (p)-[:BACKED_BY]->(e) "
                 "RETURN p, s.seq, collect(e.id)", {"i": inv})
        for p, seq, eids in pats:
            add(p.properties["id"], "Pattern", p.properties, touched=True, step=seq)
            for eid in eids:
                edges.add((p.properties["id"], eid, "BACKED_BY"))
        # context: devices of touched accounts, transactions among displayed accounts
        accs = [n for n, v in nodes.items() if v["type"] == "Account"]
        if accs:
            for aid, did, dp in q("MATCH (a:Account)-[:USED_DEVICE]->(d:Device) WHERE a.id IN $a RETURN a.id, d.id, properties(d)", {"a": accs}):
                if add(did, "Device", dp, touched=False, step=999):
                    edges.add((aid, did, "USED_DEVICE"))
            for aid, tid, tp, bid in q("MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(b:Account) WHERE a.id IN $a AND b.id IN $a "
                                       "RETURN a.id, t.id, properties(t), b.id LIMIT 120", {"a": accs}):
                if add(tid, "Transaction", tp, touched=False, step=999):
                    edges.add((aid, tid, "SENT"))
                    edges.add((tid, bid, "TO"))
        # edges for support transactions (sender/receiver) when those accounts are displayed
        txs = [n for n, v in nodes.items() if v["type"] == "Transaction"]
        if txs:
            for aid, tid, bid in q("MATCH (a:Account)-[:SENT]->(t:Transaction)-[:TO]->(b:Account) WHERE t.id IN $t RETURN a.id, t.id, b.id", {"t": txs}):
                if aid in nodes:
                    edges.add((aid, tid, "SENT"))
                if bid in nodes:
                    edges.add((tid, bid, "TO"))
        # policies matched by the reporter
        for pid, polid, pp, rat in q("MATCH (p:Pattern)-[v:VIOLATES]->(o:Policy) WHERE p.investigation = $i RETURN p.id, o.id, properties(o), v.rationale", {"i": inv}):
            add(polid, "Policy", pp, touched=True, step=998)
            edges.add((pid, polid, "VIOLATES"))
        owners = q("MATCH (c:Customer)-[:OWNS]->(a:Account) WHERE a.id IN $a RETURN a.id, c.id, c.name", {"a": accs}) if accs else []
        for aid, cid, cname in owners:
            if aid in nodes:
                nodes[aid]["props"]["owner"] = f"{cid} ({cname})"
        return dict(nodes=list(nodes.values()), edges=[dict(source=s, target=t, type=ty) for s, t, ty in edges if s in nodes and t in nodes],
                    capped=len(nodes) >= NODE_CAP)
    except HTTPException:
        raise
    except Exception as e:
        return db_error_response(e)


@app.get("/api/node/{node_id}")
def node_detail(node_id: str):
    lab = label_of(node_id)
    if node_id.startswith("INV-") and re.search(r"-S\d+$", node_id):
        lab = "Step"
    if lab is None:
        raise HTTPException(404, "Unknown id format")
    try:
        r = q(f"MATCH (n:{lab} {{id:$id}}) OPTIONAL MATCH (n)-[r]-(m) RETURN properties(n), collect(DISTINCT [type(r), startNode(r).id = n.id, m.id, labels(m)[0]])[..40]", {"id": node_id})
    except Exception as e:
        return db_error_response(e)
    if not r:
        raise HTTPException(404, "Not found")
    props = {k: v for k, v in r[0][0].items() if k not in ("markdown", "json")}
    rels = [dict(edge=e[0], direction="out" if e[1] else "in", id=e[2], type=e[3]) for e in r[0][1] if e[2]]
    return dict(id=node_id, type=lab, props=props, relationships=rels)


# ---- reports ----------------------------------------------------------------------------
@app.post("/api/investigations/{inv}/report")
def make_report(inv: str, request: Request):
    """Runs the Reporter as a separate process; only the investigation id is passed."""
    deny_if_readonly(request, allow_admin=True)
    p = subprocess.run([sys.executable, "-u", "-m", "agents.reporter", "--investigation", inv], cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"REPORT_ID=(RPT-\d+)", p.stdout or "")
    if p.returncode or not m:
        return JSONResponse(status_code=502, content=dict(error="Report generation failed", detail=((p.stderr or "") + (p.stdout or ""))[-800:]))
    return get_report(m.group(1))


@app.get("/api/reports/{rid}")
def get_report(rid: str):
    r = q("MATCH (r:Report {id:$r}) OPTIONAL MATCH (r)-[:CITES]->(n) RETURN r.markdown, r.json, r.recommendation, r.confidence, r.investigation, collect(n.id)", {"r": rid})
    if not r:
        raise HTTPException(404, "Unknown report")
    md, js, rec, conf, inv, cites = r[0]
    return dict(id=rid, investigation=inv, markdown=md, json=json.loads(js), recommendation=rec, confidence=conf, cites=cites)


@app.get("/api/reports/{rid}/download")
def download_report(rid: str, format: str = "md"):
    rep = get_report(rid)
    if format == "json":
        return JSONResponse(rep["json"], headers={"Content-Disposition": f'attachment; filename="{rid}.json"'})
    return PlainTextResponse(rep["markdown"], headers={"Content-Disposition": f'attachment; filename="{rid}.md"'})


# ---- reset ---------------------------------------------------------------------------------
class ResetReq(BaseModel):
    size: str = "demo"


@app.post("/api/reset")
def reset(req: ResetReq, request: Request):
    deny_if_readonly(request)  # never allowed on a read-only deployment, even for the admin: it would wipe the recorded run
    if req.size not in ("dev", "demo"):
        raise HTTPException(400, "size must be dev or demo")
    with JOB_LOCK:
        if any(not j["holder"]["done"] for j in JOBS.values()):
            raise HTTPException(409, "An investigation is running; wait for it to finish.")
        p = subprocess.run([sys.executable, "-m", "seed.reset", "--size", req.size], cwd=ROOT, capture_output=True, text=True)
        JOBS.clear()
    if p.returncode:
        return JSONResponse(status_code=502, content=dict(error="Reset failed", detail=(p.stderr or p.stdout)[-600:]))
    return dict(ok=True, message=p.stdout.strip())


@app.get("/")
def index():
    return FileResponse(os.path.join(UI_DIR, "index.html"))


os.makedirs(UI_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")
