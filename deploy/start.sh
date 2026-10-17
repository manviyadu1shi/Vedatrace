#!/bin/sh
# Replay deployment: API + UI in one container. No LLM key needed. Two modes:
#  local  (default): FalkorDB runs inside this container; the seed and the recording are loaded on every start.
#  remote: FALKORDB_HOST points at a managed FalkorDB (e.g. FalkorDB Cloud; also set FALKORDB_PORT, FALKORDB_USERNAME,
#          FALKORDB_PASSWORD, and FALKORDB_SSL=1 if the instance uses TLS). The graph is seeded only if it holds no investigation yet.
set -e
cd /app/vedatrace
PY=/opt/venv/bin/python

case "${FALKORDB_HOST:-localhost}" in
  localhost|127.0.0.1) REMOTE=0 ;;
  *) REMOTE=1 ;;
esac

if [ "$REMOTE" = 0 ]; then
  redis-server --loadmodule /var/lib/falkordb/bin/falkordb.so --save "" --appendonly no --port 6379 --bind 127.0.0.1 &
fi

for i in $(seq 1 60); do
  $PY -c "from config import get_db; get_db().connection.ping()" 2>/dev/null && break
  sleep 1
done

HAS_RUN=$($PY -c "
from config import get_graph
try:
    print(get_graph().ro_query('MATCH (i:Investigation) RETURN count(i)').result_set[0][0])
except Exception:
    print(0)
")

if [ "$HAS_RUN" != "0" ]; then
  echo "graph already holds a recorded investigation, keeping it"
else
  $PY -m seed.reset --size demo
  if [ -f /app/vedatrace/demo_runs/flagship.json ]; then
    $PY -m seed.recording load /app/vedatrace/demo_runs/flagship.json
  else
    echo "WARNING: no recorded run found at demo_runs/flagship.json; the UI will show an empty graph"
  fi
fi

exec $PY -m uvicorn api.app:app --host 0.0.0.0 --port "${PORT:-7860}"
