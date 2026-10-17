"""Shared config: env loading and the FalkorDB connection. No secrets are read here except via os.environ."""
import os

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()

GRAPH_NAME = os.environ.get("VEDATRACE_GRAPH", "vedatrace")
FALKOR_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKOR_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))
# Managed FalkorDB (e.g. FalkorDB Cloud) needs credentials; local Docker needs none. Never logged.
FALKOR_USERNAME = os.environ.get("FALKORDB_USERNAME") or None
FALKOR_PASSWORD = os.environ.get("FALKORDB_PASSWORD") or None
FALKOR_SSL = os.environ.get("FALKORDB_SSL") == "1"


def get_db() -> FalkorDB:
    return FalkorDB(host=FALKOR_HOST, port=FALKOR_PORT, username=FALKOR_USERNAME, password=FALKOR_PASSWORD, ssl=FALKOR_SSL)


def get_graph(name: str | None = None):
    return get_db().select_graph(name or GRAPH_NAME)
