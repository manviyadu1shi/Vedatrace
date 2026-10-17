#!/bin/sh
# Single-container replay deployment: FalkorDB + seed + recorded run + API/UI. No LLM key needed.
set -e
cd /app/vedatrace
PY=/opt/venv/bin/python

redis-server --loadmodule /var/lib/falkordb/bin/falkordb.so --save "" --appendonly no --port 6379 --bind 127.0.0.1 &

for i in $(seq 1 40); do
  $PY -c "import falkordb; falkordb.FalkorDB(port=6379).connection.ping()" 2>/dev/null && break
  sleep 0.5
done

$PY -m seed.reset --size demo
if [ -f /app/vedatrace/demo_runs/flagship.json ]; then
  $PY -m seed.recording load /app/vedatrace/demo_runs/flagship.json
else
  echo "WARNING: no recorded run found at demo_runs/flagship.json; the UI will show an empty graph"
fi

exec $PY -m uvicorn api.app:app --host 0.0.0.0 --port "${PORT:-7860}"
