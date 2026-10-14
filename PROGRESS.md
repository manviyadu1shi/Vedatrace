# VedaTrace PROGRESS

## LLM provider decision (Step 0)
- `VEDATRACE_API_KEY` was **not set** (checked via python-dotenv, value never printed). Anthropic call skipped.
- Fallback: **local Ollama** (`VEDATRACE_LLM_PROVIDER=ollama`), model `qwen2.5:14b` + `nomic-embed-text` (pulled on 2026-10-15).
- If a key is later put in `.env`, set `VEDATRACE_LLM_PROVIDER=anthropic` (default) — code path is implemented but UNTESTED against the live API.

## Setup / commands
- venv: `/opt/homebrew/bin/python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- FalkorDB: `docker run -d --name vedatrace-falkordb -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest` (restart: `docker start vedatrace-falkordb`)
- All python commands run from `vedatrace/` (code root): `cd vedatrace && ../.venv/bin/python -m seed.reset --size demo`

## Status
- [x] Phase 1 infra
- [x] Phase 2 seed (tests/test_seed.py passing)
