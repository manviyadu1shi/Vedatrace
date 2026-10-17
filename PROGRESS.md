# VedaTrace PROGRESS

## LLM provider decision (Step 0)
- `VEDATRACE_API_KEY` was **not set** (checked with python-dotenv; the value was never read or printed). The Anthropic call was therefore skipped.
- Fallback: **local Ollama** → `VEDATRACE_LLM_PROVIDER=ollama`, model `qwen2.5:14b` (agents) + `nomic-embed-text` (memory embeddings). Both pulled on 2026-10-15. Tool calling confirmed with a test chat.
- The `anthropic` code path in `agents/llm.py` is implemented (tool use, `claude-sonnet-5-5`, key passed explicitly from `VEDATRACE_API_KEY`) but is **UNTESTED against the live API** (no key, no credit). Put a key in `.env` and set `VEDATRACE_LLM_PROVIDER=anthropic` to try it.
- Hardware note: 16 GB RAM, so 14B is the largest local model that fits. `qwen3:14b` was tried too (slower, not better) and is installed but unused.

## Commands (run from `vedatrace/`, the code root; venv is `../.venv`)
```
docker start vedatrace-falkordb      # or: docker run -d --name vedatrace-falkordb -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest
cd vedatrace
../.venv/bin/python -m seed.reset --size demo        # or --size dev
../.venv/bin/python -m agents.investigator --tx TX-FLAGSHIP        # prints INVESTIGATION_ID=INV-n
../.venv/bin/python -m agents.reporter --investigation INV-n        # separate process; prints REPORT_ID=RPT-n
../.venv/bin/python run_demo.py                      # reset + investigator + reporter, each in its own process
../.venv/bin/python -m uvicorn api.app:app --port 8000   # UI at http://localhost:8000
../.venv/bin/python -m pytest -q                     # fast tests (no LLM)
RUN_E2E=1 E2E_RUNS=3 ../.venv/bin/python -m pytest -q -s tests/test_e2e.py   # slow, real LLM
../.venv/bin/python -m memory.graphiti_mem seed-history   # Graphiti episodes for the labelled demo history (slow, ~2 min each locally)
```

## Status
- [x] Phase 1 infra (git, .gitignore, .env.example, FalkorDB in Docker)
- [x] Phase 2 seed — `tests/test_seed.py` passes (determinism, counts, no agent labels). Demo size: 1506 accounts, 7993 tx, 396 devices.
- [x] Phase 3 tools + Investigator — works end to end on Ollama; see "Known issues" for the quality caveats.
- [x] Phase 4 Reporter — separate process, only an investigation id as input, citations verified against the graph.
- [~] Phase 5 e2e script + tests — `run_demo.py`, `tests/test_e2e.py`, `tests/test_no_cheating.py`, `tests/test_tools.py` written. Repeatability numbers: see below (filled in by demo-verify).
- [~] Phase 6 UI/FastAPI — written; verification results below.
- [~] Phase 7 Graphiti memory — see below.
- [x] Phase 8 README + DEMO_SCRIPT (drafted; numbers below are the real ones)
- [~] Phase 9 demo-verify — only partly done, see 'Verification results'

## Design notes worth knowing
- The Investigator is a free tool-using loop; no query order is coded. Tool outputs are generic analytics (shared devices, burst analysis against the account's own baseline, cycle search, flow tracing).
- `write_evidence` **verifies the claim against the graph** (e.g. RAPID_FANOUT must cite 3+ transfers of one sender to 3+ recipients inside 7 days; CIRCULAR_FLOW must cite transactions that form a closed loop; BENIGN_EXPLANATION needs a graph-checked basis) and rejects ids the agent never saw in a tool result. This is what keeps a 14B model honest.
- Tool results contain `cite_ids` / `cite_ids_for_burst` lists the agent can copy into evidence. The agent still decides what is evidence and which kinds/patterns to write; this is a prompt/tooling aid for small local models, not a hardcoded pipeline. A "self-review" nudge on the first two `finish` calls lists ungrouped evidence and unexplored device-linked accounts (generic, derived from graph state).
- Reporter structured sections (findings, ruled-out, reasoning path) are rendered deterministically from graph nodes; the LLM does Pattern→Policy mapping (`VIOLATES` edges) and narrative. Every `[ID]` citation is checked against the graph; one retry, then it fails loudly.
- Seed contains red herrings placed **next to** the ring: household device shared by the flagship sender (family transfers, 2-account back-and-forth), a payroll account (3 identical bursts to 60 employees) that also pays a ring-adjacent account, a marketplace payout account, rent/refund and bill-split cycles, legitimate shared devices (households/offices), unrelated just-under-threshold transfers, decoy alerts.
- Ground truth lives only in `vedatrace/seed/ground_truth.json` and is read only by `tests/`.

## Memory (Graphiti)
- `memory/graphiti_mem.py`: two layers in FalkorDB. (1) Case store (graph `vedatrace_memory`, embeddings via `nomic-embed-text`) used for recall; recalled cases are copied into the case graph as `PriorCase` nodes with a `RECALLED` edge so the Reporter can read and cite them (`[CASE-H1]`) without any in-process coupling. (2) Graphiti (`graphiti-core` 0.30.2, FalkorDB driver, graph `vedatrace_graphiti`) ingests each case as an episode.
- Compatibility fix needed: graphiti-core 0.30.2 uses an old FalkorDB fulltext-index procedure signature that the current `falkordb/falkordb:latest` (graph module 6.0.2) rejects; `memory/graphiti_mem.py` monkeypatches it to the Cypher DDL form. Also, Graphiti maps `group_id` to a FalkorDB graph name, so group id is set to `vedatrace_graphiti` (an earlier attempt wrote into the case graph by mistake; fixed).
- Graceful degradation: any Graphiti error disables only the Graphiti layer; the case store keeps working; `VEDATRACE_MEMORY=off` disables all of it.

## Known issues / honest limitations
- Quality depends on the local model. qwen2.5:14b finds the three structures in most runs but sometimes: records evidence on the wrong account, groups evidence into a slightly wrong pattern, or picks a benign reciprocal cycle first. Runs take ~6–18 minutes on this 16 GB machine.
- Anthropic path untested (see above).
- Graphiti entity extraction with a local 14B model is slow (~2 min per episode) and noisy; recall relies mostly on the case store.

## Verification results (honest, as of the end of the overnight session)
- Fast tests (no LLM): **19 passed, 1 skipped** (the skipped one is the opt-in LLM e2e test). Covers seed determinism/counts, no agent-written nodes after seeding, no ground-truth or ring ids in agent code, Reporter does not import the Investigator, only `agents/llm.py` imports provider SDKs, Cypher parameterisation, tool validators (hallucinated ids, claims checked against the graph, benign-basis checks).
- Secrets: `.env` is git-ignored; no key-like strings in tracked files; the forbidden env var name is not used in code.
- LLM runs that DID complete (qwen2.5:14b, demo seed, before the host ran out of memory): several full Investigator runs, and one complete `run_demo.py` run (Investigator -> separate-process Reporter -> memory recall/remember). In that run all citations resolved, the three policies matched, and `CASE-H1` was recalled and cited, but the Investigator **missed the SHARED_DEVICE evidence**. Earlier runs found all three structures (shared device, fan-out/structuring, circular flow) with correct provenance but with some mis-attributed evidence. No run has flagged a planted red herring as fraud.
- Headless-browser replay of a finished investigation (`tests/ui_check.py`): steps, graph (6 node types), 59 clickable citations, citation click focuses the graph node, download links, 0 JS errors. **The live "click Run" flow through the browser was NOT verified.**
- **NOT verified: the 3-run repeatability check.** `RUN_E2E=1 E2E_RUNS=3 pytest tests/test_e2e.py` was started four times and never completed a run: the 16 GB laptop had almost all of its swap used (12.2 of 13.3 GB), and Ollama stalled (investigation created, then no progress for 20-30+ min). I reduced `OLLAMA_NUM_CTX` to 8192, unload the embedding model after use, and added a 240 s timeout with retries, but did not get a clean run afterwards. So the pass rate is **unknown (0 of 3 measured)**; do not quote a number.
- NOT verified: failure-mode checks (kill FalkorDB mid-run, Graphiti off during a full run), the Anthropic provider, a second provider pass-rate comparison.
- `/demo-verify` was not completed.

## Next steps for the owner
1. Free memory (close apps, reboot if needed), `ollama serve`, then `cd vedatrace && RUN_E2E=1 E2E_RUNS=3 ../.venv/bin/python -m pytest -q -s tests/test_e2e.py` and record the real pass count here.
2. `python tests/ui_check.py --live` to verify the live browser flow.
3. With a key in `.env`: set `VEDATRACE_LLM_PROVIDER=anthropic` and repeat; expect better judgement than the 14B local model.
4. Run the `/demo-verify` skill.

## Independent verification, 2026-10-16 (OpenAI provider `gpt-5.6-luna`, FalkorDB in Docker, no Ollama)
Provider added in `agents/llm.py` (`VEDATRACE_LLM_PROVIDER=openai`, key `OPENAI_API_KEY` in `.env`, reasoning_effort=none, retry/backoff on 429). Graphiti uses `gpt-4.1-mini` + `text-embedding-3-small` in this mode. `/api/health` key check made provider-aware.
- Seed: deterministic (identical tx hash across resets); 0 Evidence/Pattern/Report nodes after seeding. Fast tests 19 passed, 1 skipped.
- **Repeatability (RUN_E2E): 7/8 full discoveries** (run batches 2/3 and 5/5; the one miss omitted CIRCULAR_FLOW). 8/8 provenance, citations resolve, 0 red-herring violations. 5 distinct step sequences in 5 runs. A run takes ~60 s.
- Decoupling: Reporter runs as a separate process given only an investigation id; refuses unfinished/missing investigations with a clear message.
- UI: `python tests/ui_check.py --live` passes in headless Chromium (click Run, 37 steps streamed, 55-node graph, report with 116 citations, citation click focuses graph node, 0 JS errors).
- Graphiti: works with OpenAI (4 episodes ingested, backend `graphiti+cases`); `run_demo.py` recalled H1/H3/H4, Reporter cited them, case stored with a Graphiti episode. `VEDATRACE_MEMORY=off` runs the core fine.
- Failure modes: stopping FalkorDB mid-run exits non-zero but leaves the Investigation stuck as `running`.
- Secrets: `.env` ignored, no key-like strings in the tree.
### Known issues found
1. Stale `running` Investigation after a DB crash (no recovery/sweep).
2. CLI `agents.investigator --tx TX-NOPE` creates an investigation for a nonexistent tx (the API rejects it).
3. A stored investigation case (`CASE-INV-n`) scores below the hand-written history cases, so with k=3 it is not recalled by the next run.
4. Graphiti extracts very few entities (2 from 4 episodes); recall leans on the case store. Cosmetic "Event loop is closed" traceback at CLI exit.
5. The Investigator never records BENIGN_EXPLANATION for the decoys; they are simply not flagged.
6. The Anthropic and Ollama paths were not re-tested in this session.

## Bug-fix pass, 2026-10-16
Fixed and verified:
1. Stale `running` investigations: `sweep_stale()` (investigator start + API list) marks runs older than 30 min `abandoned`; the Reporter refuses `abandoned`/`failed` runs.
2. `python -m agents.investigator --tx TX-NOPE` now errors (exit 2) and creates no investigation.
3. Memory recall always reserves a slot for the best self-written case (`origin=investigation`); `CASE-INV-1` is recalled first on a repeat run.
4. Graphiti extraction: `custom_extraction_instructions` raised extraction from 2 entities to 19 entities / 17 facts; async clients are closed inside the loop, so no "Event loop is closed" traceback.
5. Also: `MAX_STEPS` 36 -> 48 (runs were exhausting the budget), and the CIRCULAR_FLOW rejection message now says which ids to cite.
Re-measured after the fixes: **6/6** full discoveries, 0 red-herring violations, 6 distinct step sequences, ~90 s per run (e2e, gpt-5.6-luna).
**NOT fixed: the Investigator still never records BENIGN_EXPLANATION for the decoys.** I tried a generic self-review nudge ("check accounts on money loops before concluding"); it made the model investigate the payroll decoy and record it as fraud in 3 of 6 runs (red-herring violations), so I reverted it. Decoys are still not flagged, just not explained.

## Free-tier session, 2026-10-17 (no paid key; Windows; FalkorDB on port 6380)
Environment notes
- A local `redis-server` already owns port 6379 on this machine, so FalkorDB runs as `docker run -d --name vedatrace-falkordb -p 6380:6379 -p 3000:3000 falkordb/falkordb:latest` and every command needs `FALKORDB_PORT=6380`. Also set `PYTHONUTF8=1` on Windows (model output contains non-cp1252 characters).
- Venv is `.venv` (Windows: `.venv/Scripts/python`).
Code changes (uncommitted unless the owner committed them)
- `agents/llm.py`: `OPENAI_BASE_URL` (any OpenAI-compatible endpoint), `reasoning_effort` only sent to real OpenAI, Ollama embeddings on custom endpoints, retry on `tool_use_failed` 400s, retry on 5xx/connection errors, Gemini 3 `thought_signature` passthrough (`ToolCall.extra`).
- `seed/recording.py`: `export INV-n --out file.json` / `load file.json` for a RECORDED real run (kept separate from `seed.reset`, which still seeds raw facts only). Round trip verified: 32 nodes / 123 edges identical, a second load is refused, replay works with no LLM key. Loaded Investigation/Report are flagged `recorded=true`; the UI lists them as "recorded run".
Free providers tried (OpenAI-compatible endpoints)
- Groq `openai/gpt-oss-120b` (8k TPM) and `qwen/qwen3.8-27b` (7k input TPM): runs die mid-investigation with 413 once the context grows (steps 12-17). Not usable.
- Gemini `gemini-flash-latest` and `gemini-3-flash-preview`: free tier is 20 requests/day per model. Not usable (an investigation needs 40+).
- Gemini `gemini-3.1-flash-lite`: completes (26 steps, ~2-5 min, Reporter ~40 s).
Measured, honest numbers (Flash-Lite, demo seed, E2E_RUNS=3): **0/3 full discoveries.** Fan-out found 3/3; circular flow 2/3; shared-device cluster 0/3 (treated as a household); 1 red-herring violation in run 1. Provenance 3/3, report citations resolve 3/3, no crashes. Earlier OpenAI `gpt-5.6-luna`: 6/6 (see sections above). Quality depends on model strength.
NOT done: Anthropic provider test (no Anthropic key), Graphiti with a free provider (VEDATRACE_MEMORY=off was used), failure-mode checks, /demo-verify end to end, a good recorded run for the demo (`demo_runs/` not created yet).
Note: Reporter runs overwrite the tracked `vedatrace/reports/RPT-1.*`; the committed versions are the OpenAI run's.

## Hackathon-ready state, 2026-10-17 (end of free-tier session)
- Recording shipped: `vedatrace/demo_runs/flagship.json` (INV-1, 28 steps, 5 Evidence + 2 Pattern, RPT-1 ESCALATE; gemini-3.1-flash-lite, free tier). Found fan-out/structuring + circular flow, no red-herring violations, provenance and citations OK; MISSED the shared-device cluster (recorded as benign). Best of 7 Flash-Lite attempts this session by the "fan-out + circular + clean" rule; 0/8 Flash-Lite runs ever found the shared device.
- Deploy: `docker build -t vedatrace . && docker run -p 7860:7860 vedatrace`. Read-only (`VEDATRACE_READONLY=1`): POST /api/investigations, /report, /reset return 403. Verified: container seeds + loads the recording, UI auto-opens INV-1 (28 steps, 48 nodes/87 edges, 84 citations), write buttons disabled, badge "replay · no LLM calls", 0 console errors (headless Edge). Free hosting steps in `DEPLOY.md` (Hugging Face Spaces Docker, or Render).
- Docs: README (one-command quickstart, honest limitations with measured numbers), DEMO_SCRIPT.md section A = video script for the deployed replay.
- STILL NOT DONE: Anthropic provider never run (no key); Graphiti with a free provider; failure-mode checks; full `/demo-verify`; owner tasks (push, deploy the Space, record video, submission text).

## Final recording + memory, 2026-10-17 (supersedes the "Hackathon-ready state" recording above)
- Graphiti now runs on the free Gemini key: `memory/graphiti_mem.py` and `agents/llm.py:embed` honor `OPENAI_BASE_URL` / `OPENAI_EMBED_MODEL` (`gemini-embedding-001`, vectors truncated to 1024). `seed-history` ingested 4 episodes (14 entities), backend `graphiti+cases`.
- Shipped recording `vedatrace/demo_runs/flagship.json` (INV-1, 27 steps, 5 Evidence + 5 Pattern, RPT-1 ESCALATE 1.0; `gemini-3.1-flash-lite`, memory ON, 3 prior cases recalled and cited: CASE-H1/H3/H4). Discovery: shared device, fan-out, structuring, circular flow ALL found; provenance + citations OK; payroll/marketplace decoys not flagged.
- KNOWN FALSE POSITIVE in the recording: `EV-5`/`PAT-5` report the flagship sender's legitimate home device (DEV-0390, shared with ACC-1370; ground truth key `benign_household_near_ring`) as a SHARED_DEVICE_CLUSTER. The built-in red-herring check did not cover that decoy, so earlier "0 violations" claims excluded it. Added `household_near_ring_flagged()` in `tests/e2e_checks.py` (printed by `test_e2e.py`, not gating). Disclosed in README and DEMO_SCRIPT.
- Tried for a cleaner recording (all three + household device left alone): blocked because `gemini-3.1-flash-lite` free quota (500 requests/day) ran out; resets ~13 h later. Backups of earlier recordings are NOT in the repo (scratchpad only): memory-off partial run missed the ring shared device.
- Verified (deployed container, headless Edge): UI opens on INV-1, "Recalled prior cases" card, badges "replay · no LLM calls" / "memory · shown in recording", write buttons disabled, 0 console errors, POST /api/investigations /report /reset -> 403.
- Honest sample sizes: Flash-Lite memory-off ~12 runs, shared-device check never passed; memory-on 2 completed runs, shared device found 2/2, all three structures 1/2 (n too small to claim memory is the cause).
- NOT done: Anthropic provider (no key), failure-mode checks, full `/demo-verify`, the owner tasks (commit/push, deploy Space, video, submission text).

## UI polish + cloud deployment prep, 2026-10-17
- UI redesign (ids unchanged): how-it-works strip, result summary bar, colour-coded step cards with readable tool names, paced replay of a finished run (steps + graph growth, Replay / Skip buttons, auto-plays in read-only mode), hover labels on the graph, footer with the human-decides disclaimer. New read-only endpoint `GET /api/investigations/{id}/trace` (same shape as the SSE events).
- Read-only mode caches GET responses (the recorded run never changes) and pre-warms the cache at startup, because a remote FalkorDB costs ~1 s per query: trace/graph/report went from 5-7 s to ~0.2 s. Writes still 403, report download keeps its attachment header.
- FalkorDB Cloud: free instance (AWS ap-south-1) populated with the seed + recording (11,253 nodes, ~10 MB of 100 MB). App verified against it in read-only mode with headless Edge: 27 steps / 45 nodes / 88 edges / 80 citations, 0 console errors. Tests must be run with FALKORDB_HOST=localhost FALKORDB_PORT=6380 FALKORDB_USERNAME= FALKORDB_PASSWORD= while `.env` points at the cloud, or they would touch the cloud graph.
- Not yet done: Render deploy (owner), video, submission text.

## Session 3, 2026-10-18 (deadline: Sun 2026-10-18 23:59 IST)
### Done
- Deployed: https://vedatrace.onrender.com (Render free, Docker, read-only replay) against a FalkorDB Cloud free instance (it stops after 1 idle day, is deleted after 7). Repo: https://github.com/manviyadu1shi/Vedatrace (the owner commits and pushes herself).
- Admin unlock for live runs on the public site: header "Admin" button + env `VEDATRACE_ADMIN_TOKEN` (value only in Render). Live runs there need `VEDATRACE_LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_EMBED_MODEL`, `VEDATRACE_MEMORY=simple`. Reset stays locked. A live run from Render failed ("Investigation failed to start") while the Gemini free quota was exhausted; not re-tested after the reset.
- New endpoint `GET /api/alerts`: the transactions the seed marked `alerted` (TX-FLAGSHIP + 8 decoys, raw seed facts only), each with its latest investigation.
- UI rebuilt as ONE page workbench (ids kept): left rail = alert list (click to open, "Watch" for the recording, "Investigate" when live runs are allowed), centre = evidence graph with summary chips and Replay / Skip / View report, right = tabs Investigation | Report. Header progress: Pick an alert > Investigate > Report. Hash routes `#/alerts`, `#/case/INV-n`, `#/report/INV-n`. Files: `vedatrace/ui/index.html`, `app.js`, `style.css` (shared components), `layout.css` (new, workbench layout). Assets are loaded with `?v=3` to defeat stale browser caches (an old cached style.css made the first deploy of the redesign look unstyled; bump the number on UI changes).
- Errors are shown in full (wrapped), not clipped. Free-text transaction box removed (it only accepted ids that exist).
- Tested locally against the cloud graph in read-only mode with headless Edge: alerts list, Watch, Skip, Report tab, 0 console errors. NOT tested: live runs from an alert card, or the redeployed site.
- Removed the helper scripts (`scripts/*`) and `docs/medium-post.md` on the owner's request; the blog lives in a private Claude artifact (https://claude.ai/artifact/NsTpDmqaGNFfEqZEWzcqme). It embeds `docs/realdemo.png` (an OLD-UI screenshot, not committed on purpose). The owner will replace it with a screenshot of the new UI; "Copy for Medium" links `https://raw.githubusercontent.com/manviyadu1shi/Vedatrace/main/docs/realdemo.png`, so push a file with that name first.

### RESUME HERE
1. Commit + push the UI files (`git add vedatrace/ui/index.html vedatrace/ui/app.js vedatrace/ui/style.css vedatrace/ui/layout.css`; confirm `.env` is not staged), then Render > Manual Deploy, then a hard refresh.
2. Check the deployed site: alerts load, Watch plays the recording, Report tab shows the cited report.
3. After the Gemini quota resets (~05:30 IST), optionally try a cleaner live recording (`demo_runs/flagship.json` has one disclosed false positive: DEV-0390).
4. Take a screenshot of the new UI, replace `docs/realdemo.png`, push; tell Claude to swap the image in the blog artifact.
5. Record the demo video (DEMO_SCRIPT.md section A) from the deployed site, finish the Medium post, write the submission text, submit before 23:59 IST.

### Environment quirks
- A local `redis-server` owns 6379, so the local FalkorDB container `vedatrace-falkordb` uses 6380 (`docker start vedatrace-falkordb`; Docker Desktop must be running).
- `.env` points at the CLOUD graph: `python -m seed.reset` would DELETE it. For local tests override `FALKORDB_HOST=localhost FALKORDB_PORT=6380 FALKORDB_USERNAME= FALKORDB_PASSWORD=`. Set `PYTHONUTF8=1`. venv: `.venv/Scripts/python`.
- Local read-only preview against the cloud graph (reads only): `VEDATRACE_READONLY=1 python -m uvicorn api.app:app --app-dir vedatrace --port 8123`.

### Not done / known gaps
Anthropic provider never run (no key); failure-mode checks and a full `/demo-verify` not done; Graphiti verified on free Gemini only locally (the slim deploy image has no Graphiti; memory there is the case store).
