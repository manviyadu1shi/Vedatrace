# Free deployment (read-only replay)

The deployed app is a single Docker container: FalkorDB + the deterministic seed + a recorded real run + the UI.
No LLM key, no database service, nothing paid. Visitors cannot run agents, reset the graph or trigger LLM calls
(`VEDATRACE_READONLY=1`), so it is safe to make public.

Check locally first:
```bash
docker build -t vedatrace . && docker run -p 7860:7860 vedatrace      # http://localhost:7860
```

## Option A: Render (free web service, Docker) — recommended
Hugging Face now marks Docker Spaces as paid, so use Render. The whole container uses about 66 MB of RAM (measured), well inside a free instance.
1. Sign up at render.com (GitHub login works). No card is needed to create a free web service.
2. **New + > Web Service**. If your GitHub repo is public, use the **Public Git Repository** tab and paste the repo URL (no GitHub permissions needed); otherwise connect GitHub.
3. Settings: **Language: Docker**, branch `main`, Dockerfile path `./Dockerfile`, root directory empty, **Instance Type: Free**, Health Check Path `/api/health`. No environment variables are needed (`VEDATRACE_READONLY=1` is set in the Dockerfile).
4. Create the service. The first build takes a few minutes. Render supplies `$PORT`, which `deploy/start.sh` honours.
5. Your URL looks like `https://vedatrace-xxxx.onrender.com`. Free services sleep after ~15 minutes without traffic and take about a minute to wake, so open the URL yourself before recording or before judging starts. Mention this in the submission.

## Option A+: keep the graph on FalkorDB Cloud (free managed FalkorDB) and the app on Render
FalkorDB Cloud hosts only the database, not the app, so it is used together with Option A.
1. Create a free instance at app.falkordb.cloud (no card). From the instance's connection details note the **host, port, username (if shown) and password**.
2. In Render, on the service from Option A, add these **Environment** variables (type them in the dashboard; never commit them):
   `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_PASSWORD`, and `FALKORDB_USERNAME` if the instance gives one. Set `FALKORDB_SSL=1` only if the instance uses TLS.
3. Redeploy. With `FALKORDB_HOST` set, the container does not start its own FalkorDB. On start it seeds the demo graph and loads the recording **only if the graph holds no investigation yet**, so restarts do not wipe it.
Measured: the demo graph is about 4 MB (the whole instance ~21 MB), well inside the free tier's 100 MB.
Caveats of the free tier (per FalkorDB's docs): an instance idle for 1 day is stopped and one idle for 7 days is deleted; there is no persistence or backup; there is no TLS, so use a password you use nowhere else (the data is synthetic). If the instance is stopped or deleted, restart or recreate it and redeploy the app. For that reason Option A on its own (database inside the container) is the more robust choice; use A+ if you want the deployed demo to run on FalkorDB Cloud.

## Option B: Hugging Face Spaces
Docker Spaces need a paid plan on HF now, so this only works with PRO. If you have it: create a Docker Space, then
```bash
git remote add space https://huggingface.co/spaces/<user>/<space>
git checkout -b hf-space && cp deploy/hf_space_README.md README.md && git add README.md && git commit -m "space readme"
git push space hf-space:main --force && git checkout main
```
A free **Static** Space cannot run this app (no database, no Python).

## Notes
* Memory use is small: the demo graph is ~11k nodes and persistence is off (the graph is rebuilt on every start from the seed plus the recording).
* Never put API keys in the image: `.dockerignore` excludes `.env`, and the read-only deployment needs none.
* To re-record: run the agents locally, then `python -m seed.recording export INV-n --out demo_runs/flagship.json` (from `vedatrace/`), commit, redeploy.
