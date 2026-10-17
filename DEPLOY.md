# Free deployment (read-only replay)

The deployed app is a single Docker container: FalkorDB + the deterministic seed + a recorded real run + the UI.
No LLM key, no database service, nothing paid. Visitors cannot run agents, reset the graph or trigger LLM calls
(`VEDATRACE_READONLY=1`), so it is safe to make public.

Check locally first:
```bash
docker build -t vedatrace . && docker run -p 7860:7860 vedatrace      # http://localhost:7860
```

## Option A: Hugging Face Spaces (free, Docker SDK) — recommended
1. Create a Space at huggingface.co/new-space: SDK **Docker**, hardware **CPU basic (free)**, public.
2. From this repo (the recording `vedatrace/demo_runs/flagship.json` must be committed):
   ```bash
   git remote add space https://huggingface.co/spaces/<your-user>/<space-name>
   git checkout -b hf-space
   cp deploy/hf_space_README.md README.md        # Spaces needs this front matter; your GitHub README stays untouched on main
   git add README.md && git commit -m "space readme"
   git push space hf-space:main                  # use an HF access token as the password
   git checkout main
   ```
3. The Space builds the `Dockerfile` and serves on port 7860. The first build takes a few minutes.

## Option B: Render (free web service)
New > Web Service > connect the GitHub repo > Runtime **Docker** > Instance type **Free**. Render supplies `$PORT`,
which `deploy/start.sh` honours. Free instances sleep after ~15 min idle, so the first visit after a pause takes about a minute.
(Render may ask for a card even on the free tier; Hugging Face does not.)

## Notes
* Memory use is small: the demo graph is ~11k nodes and persistence is off (the graph is rebuilt on every start from the seed plus the recording).
* Never put API keys in the image: `.dockerignore` excludes `.env`, and the read-only deployment needs none.
* To re-record: run the agents locally, then `python -m seed.recording export INV-n --out demo_runs/flagship.json` (from `vedatrace/`), commit, redeploy.
