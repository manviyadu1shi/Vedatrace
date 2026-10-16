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
