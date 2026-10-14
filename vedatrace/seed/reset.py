"""One-command reset: drop the case graph and reseed.  python -m seed.reset --size dev|demo"""
import argparse
import time

from config import GRAPH_NAME, get_db
from seed.build_graph import build


def reset(size: str = "dev", graph_name: str | None = None) -> dict:
    name = graph_name or GRAPH_NAME
    db = get_db()
    try:
        db.select_graph(name).delete()
    except Exception:
        pass  # graph did not exist yet
    return build(size, db.select_graph(name))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", choices=["dev", "demo"], default="dev")
    a = ap.parse_args()
    t = time.time()
    stats = reset(a.size)
    try:  # forget memories of investigations that no longer exist; keep the labelled demo history
        from memory import graphiti_mem
        graphiti_mem.reset_investigation_memory()
    except Exception:
        pass
    print(f"Seeded '{GRAPH_NAME}' ({a.size}) in {time.time() - t:.1f}s: {stats}")
