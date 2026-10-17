# VedaTrace — graph-native AML investigation agents

> Two agents investigate a suspicious transaction. **They never talk to each other; they talk through the FalkorDB graph.**

Built for **Graph Hacks (WeMakeDevs × FalkorDB)** — tracks: *01 Agents That Act on Connected Data* (primary), *02 Agent Memory and Coordination*.

**Live demo:** https://vedatrace.onrender.com (read-only replay of a recorded run; the free server sleeps when idle, so the first load can take about a minute).

| Agent | What it does | Reads | Writes |
|---|---|---|---|
| **Investigator** | Tool-using LLM loop. Explores the graph, decides its own next step, records what the data supports. | Customer, Account, Device, Transaction, Policy | `Investigation`, `Step` (audit trail), `Evidence`, `Pattern` — each with provenance edges to the exact Transaction/Account/Device nodes |
| **Reporter** | Separate process. Input is only an investigation id. | Everything the Investigator wrote + Policy nodes | `VIOLATES` edges (Pattern→Policy), a `Report` node with `CITES` edges, Markdown + JSON |
| **Memory** | Graphiti + a case store in FalkorDB. | past cases | `Case` episodes; recalled cases appear in the case graph as `PriorCase` nodes |

## Architecture

```mermaid
flowchart LR
  subgraph FalkorDB
    RAW[(Raw facts<br/>Customer · Account · Device<br/>Transaction · Policy)]
    TRAIL[(Agent-written<br/>Investigation · Step · Evidence · Pattern)]
    REP[(Report · VIOLATES · CITES)]
    MEM[(vedatrace_memory<br/>vedatrace_graphiti)]
  end
  LLM{{"agents/llm.py<br/>openai-compatible (free tiers), anthropic or ollama"}}
  INV[Investigator<br/>process 1]
  REPO[Reporter<br/>process 2]
  UI[FastAPI + web UI]
  INV -- "read tools (parameterised Cypher)" --> RAW
  INV -- "write_evidence / write_pattern<br/>(claims verified against the graph)" --> TRAIL
  TRAIL -- "only an investigation id crosses over" --> REPO
  REPO --> REP
  INV <-. "chat(messages, tools)" .-> LLM
  REPO <-. "chat(messages, tools)" .-> LLM
  MEM -- "recall prior cases" --> INV
  REP -- "remember case" --> MEM
  UI -- "SSE reads Step/Evidence nodes" --> TRAIL
  UI -- "spawns processes" --> INV
  UI -- "spawns processes" --> REPO
```

Why a graph for coordination: the Reporter can run hours later, on another machine, and still reconstruct *what was examined, in what order, why, and which exact nodes support each claim* — because that is all stored as nodes and edges, not in an agent's context window.

## The demo case
`TX-FLAGSHIP` looks ordinary (8,400, moderate, flagged only by a weak monitoring score). The fraud is only visible through several traversals, and the seed (`seed/build_graph.py`, fixed `random.seed(42)`) hides it among realistic noise:

* **Hidden ring** (no labels, ordinary ids — ground truth lives in `seed/ground_truth.json`, used only by tests): a device shared by sender + two other customers' accounts; one of them fans out to 8 recipients inside a day with amounts just under a review threshold; funds return through two intermediaries to an account on the same device.
* **Red herrings**: the sender also shares a *home* device with a household member (back-and-forth transfers); a payroll account fans out to 60 employees every month (and pays one ring-adjacent account); a marketplace payout account; rent/refund and bill-split cycles; legitimate shared devices (households, offices); unrelated just-under-threshold transfers; decoy alerts.
* Sizes: `dev` (small, fast tests) and `demo` (~1,500 accounts, ~8,000 transactions, ~400 devices).

## Hard rules honoured (and how they're tested)
| Rule | Where |
|---|---|
| FalkorDB is the primary DB | every read, write, trail, report and the memory layer |
| Never seed Evidence/Pattern/Report | `tests/test_seed.py`, `tests/test_no_cheating.py` |
| No shared state between agents | Reporter has no import of the Investigator/tools (AST test); API/`run_demo.py` start them as separate processes |
| Provenance on every claim | `SUPPORTED_BY` / `BACKED_BY` / `PRODUCED` edges; enforced in `write_evidence`; `tests/test_tools.py`, `tests/e2e_checks.py` |
| Deterministic, one-command reset | `python -m seed.reset --size demo` |
| Swappable LLM, one interface | only `agents/llm.py` imports provider SDKs (AST test); `VEDATRACE_LLM_PROVIDER=openai\|anthropic\|ollama` (`openai` = any OpenAI-compatible endpoint, e.g. free Gemini) |
| Parameterised Cypher | `tests/test_no_cheating.py::test_cypher_is_parameterized` |
| Key name / secrets | key only via `VEDATRACE_API_KEY` in git-ignored `.env`; never logged |

## Try it in one command (no API key, no setup)
```bash
docker build -t vedatrace . && docker run -p 7860:7860 vedatrace      # then open http://localhost:7860
```
This starts FalkorDB **and** the app in one container, seeds the deterministic demo graph (raw facts only) and loads a **recorded real run**: the Investigator's step-by-step trail, its Evidence/Pattern nodes with provenance, and the Reporter's report. The UI is a single page: the left rail lists the transactions the monitoring system flagged, the centre shows the evidence graph growing as the recorded investigation is replayed, and the right side has an *Investigation* tab (every step, with the Investigator's reason) and a *Report* tab (the cited report, with links back to the graph nodes). Alerts without a recording need a live run. The UI replays it from the graph. It is read-only (no LLM calls, no resets), so it is safe to host publicly. The recording is clearly flagged `recorded=true`; the live agents run locally (below). On the public site only the owner can start live runs, through an admin token (see `DEPLOY.md`).

**About the bundled recording:** one real, unedited run of the Investigator and then the Reporter (separate process), made on the free Gemini `gemini-3.1-flash-lite` model (via the OpenAI-compatible provider, so the UI shows `openai/gemini-3.1-flash-lite`) with memory on (Graphiti + case store). It discovered all three ring structures (shared device `DEV-0389`, rapid fan-out + structuring, circular flow), mapped them to `POL-SHARED-DEVICE`, `POL-RAPID-FANOUT`, `POL-STRUCTURING` and `POL-LAYERING`, recalled three prior cases and the Reporter cited them (`[CASE-H1]`, `[CASE-H3]`, `[CASE-H4]`); recommendation ESCALATE. **It also made one mistake:** it reported the flagship sender's legitimate home device (`DEV-0390`, shared with a family member) as a second shared-device pattern (`PAT-5`) instead of explaining it as benign. Re-record any time with `python -m seed.recording export INV-n --out demo_runs/flagship.json` (from `vedatrace/`).

## Setup (live agents)
Requirements: Python 3.11+, Docker, and an LLM. **Free options:** a Google Gemini key (aistudio.google.com/apikey, no card) or local [Ollama](https://ollama.com). Paid/other: any OpenAI-compatible key, or an Anthropic key.

```bash
git clone … && cd vedatrace-kit
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
docker run -d --name vedatrace-falkordb -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest   # 3000 = FalkorDB Browser

cp .env.example .env        # then edit:
#   Free (Gemini): VEDATRACE_LLM_PROVIDER=openai OPENAI_API_KEY=<free key> OPENAI_MODEL=gemini-3.1-flash-lite
#                  OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
#   Anthropic:     VEDATRACE_API_KEY=<your key>   VEDATRACE_LLM_PROVIDER=anthropic     (model claude-sonnet-5-5)
#   Local:      VEDATRACE_LLM_PROVIDER=ollama  OLLAMA_MODEL=qwen2.5:14b             (any tool-calling model)
#   OpenAI-compatible (OpenAI, Gemini, OpenRouter, ...):
#               VEDATRACE_LLM_PROVIDER=openai OPENAI_API_KEY=<key> OPENAI_MODEL=<model> [OPENAI_BASE_URL=<endpoint>]
#   If something else already uses port 6379 (e.g. a local Redis): map FalkorDB to another port and set FALKORDB_PORT.
ollama pull qwen2.5:14b && ollama pull nomic-embed-text     # local mode / memory embeddings

cd vedatrace
../.venv/bin/python -m seed.reset --size demo
../.venv/bin/python -m memory.graphiti_mem seed-history     # optional: Graphiti episodes for the labelled demo history
../.venv/bin/python -m uvicorn api.app:app --port 8000      # open http://localhost:8000
```

### CLI only
```bash
../.venv/bin/python run_demo.py                 # reset → Investigator (process) → Reporter (process)
../.venv/bin/python -m agents.investigator --tx TX-FLAGSHIP   # prints INVESTIGATION_ID=INV-n
../.venv/bin/python -m agents.reporter --investigation INV-n  # reads only the graph
```
Open the FalkorDB Browser at <http://localhost:3000>, graph `vedatrace`, and run e.g.
`MATCH (e:Evidence)-[r:SUPPORTED_BY]->(n) RETURN e,r,n` to see the evidence trail.

### Tests
```bash
../.venv/bin/python -m pytest -q                                   # fast, no LLM
RUN_E2E=1 E2E_RUNS=3 ../.venv/bin/python -m pytest -q -s tests/test_e2e.py   # real LLM, slow
```

## How the Investigator stays honest
* **Free tool loop** — the model picks every call from: `get_transaction, get_account_profile, get_neighbors, find_shared_devices, analyze_fanout, trace_flow, find_cycles, shortest_path, list_policies, write_evidence, write_pattern, finish`. Each call needs a stated `reason`, stored on the `Step` node.
* **Claims are checked against the graph** before they become Evidence: a `RAPID_FANOUT` must cite 3+ transfers from one sender to 3+ recipients inside 7 days; `CIRCULAR_FLOW` must cite transactions that actually close a loop; `SHARED_DEVICE` needs a device plus two accounts of different customers; a `BENIGN_EXPLANATION` (payroll, refund…) needs a basis that is verified (recurring bursts to the same recipients, equal amounts back and forth) or may not contradict the agent's own findings. Ids never returned by a tool are rejected.
* Tools return small, summarised JSON (ids, counts, top-N) with an `interpretation` that compares an account with its own baseline — so a payroll run is recognised as recurring rather than as a one-off burst.

## Memory (Track 02)
`memory/graphiti_mem.py`: on `Investigator` start, prior cases similar to the alert are recalled (embedding search over the case store + Graphiti search) and injected into the system prompt as *"may or may not apply"*. They are also written to the case graph as `PriorCase` nodes so the Reporter can compare and cite them (`[CASE-H1]`) with no in-process coupling. When a report is produced the case is remembered (Graphiti episode + case node). Demo history cases are synthetic and labelled `DEMO HISTORY`. `VEDATRACE_MEMORY=off|simple|graphiti|auto`. Any Graphiti failure degrades to the case store; any memory failure degrades to "no prior cases".

Notes on running Graphiti against the current FalkorDB image: graphiti-core 0.30.2 uses a fulltext-index procedure signature that FalkorDB's graph module 6.x rejects — `memory/graphiti_mem.py` patches that at runtime; and Graphiti uses `group_id` as the FalkorDB graph name, so it is pinned to `vedatrace_graphiti`.

## Honest limitations
* **Results depend on the model, and a single run is a sample.** Measured on the demo seed: OpenAI `gpt-5.6-luna` found all three ring structures in 6/6 runs (`PROGRESS.md`). The free Gemini `gemini-3.1-flash-lite` is weaker and varies run to run: in about a dozen memory-off runs its shared-device check never passed, while in the two completed memory-on runs it found the shared-device ring both times and all three structures in one of them (the bundled recording). Two runs are too few to say memory is the cause. Provenance and report citations held in every run on every model. The earlier local `qwen2.5:14b` runs were slower and mixed. Our automated red-herring check covers the payroll and marketplace decoys; it did not cover the household device next to the ring until we added `household_near_ring_flagged` to `tests/e2e_checks.py`, so earlier "0 red-herring violations" figures did not include that decoy.
* **The Anthropic provider is implemented but has never been run against the live API** (no key was available). `claude-sonnet-5-5` is the code's default only when `VEDATRACE_LLM_PROVIDER` is unset; `.env.example` ships the free Gemini setup instead.
* **The Investigator never records `BENIGN_EXPLANATION` for the decoys**; they are simply not flagged. A nudge that tried to make it do so caused false positives and was reverted.
* Free API tiers are tight for a 40+ call agent loop: Groq's free models cap at ~7-8k tokens/min (the run dies mid-investigation), Gemini Flash models allow 20 requests/day, and `gemini-3.1-flash-lite` allows 500 requests/day (about 10 runs). That is why the public demo is a recorded run.
* Graphiti works on the free Gemini model (4 history episodes ingested, 14 entities extracted, backend `graphiti+cases`), but extraction is noisy and recall leans on the case store. The deployed replay has no live memory service: memory is shown through the recorded run (recalled `PriorCase` nodes and the cited cases).
* The recommendation (`ESCALATE | MONITOR | CLEAR`) is decision support; **a human analyst makes the final decision**.
* Policy `regulatory_ref` values are generic placeholders, not real statutes.

## Layout
```
vedatrace/
  seed/    build_graph.py reset.py recording.py ground_truth.json (tests only)
  agents/  llm.py tools.py investigator.py reporter.py
  memory/  graphiti_mem.py
  api/     app.py            ui/  index.html app.js style.css layout.css
  tests/   test_seed.py test_tools.py test_no_cheating.py test_e2e.py e2e_checks.py
  run_demo.py
```
