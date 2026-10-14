"""Shared config: env loading and the FalkorDB connection. No secrets are read here except via os.environ."""
import os

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()

GRAPH_NAME = os.environ.get("VEDATRACE_GRAPH", "vedatrace")
FALKOR_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKOR_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))


def get_db() -> FalkorDB:
    return FalkorDB(host=FALKOR_HOST, port=FALKOR_PORT)


def get_graph(name: str | None = None):
    return get_db().select_graph(name or GRAPH_NAME)
