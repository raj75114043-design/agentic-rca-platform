"""Application wiring: builds stores, tools and LLM client once and shares them."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import inspect

from rca.config import Settings, get_settings
from rca.data.generator import generate
from rca.llm import LLMClient, build_llm
from rca.retrieval import RunbookIndex
from rca.stores.graph import GraphStore, Neo4jGraph, NetworkXGraph
from rca.stores.sql import OpsRepository, ensure_schema, make_engine, seed
from rca.tools import ToolRegistry

log = logging.getLogger("rca.container")


@dataclass
class Container:
    settings: Settings
    repo: OpsRepository
    graph: GraphStore
    tools: ToolRegistry
    llm: LLMClient


def build(settings: Settings | None = None, reseed: bool = False) -> Container:
    settings = settings or get_settings()
    if settings.database_url.startswith("sqlite:///"):
        Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    world = generate(n_sites=settings.seed_sites)
    if reseed or "sites" not in inspect(engine).get_table_names():
        log.info("seeding operational database")
        seed(engine, world)
    ensure_schema(engine)
    graph: GraphStore = (Neo4jGraph(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
                         if settings.graph_backend == "neo4j" else NetworkXGraph())
    if settings.graph_backend != "neo4j" or reseed:
        graph.load(world)
    repo = OpsRepository(engine)
    return Container(settings, repo, graph, ToolRegistry(repo, graph, RunbookIndex()), build_llm(settings))


@lru_cache(maxsize=4)
def _cached(llm_signature: tuple) -> Container:
    return build()


def get_container() -> Container:
    """Shared container; rebuilt when LLM configuration changes (e.g. a secret added at runtime)."""
    import os
    sig = tuple(os.getenv(k, "") for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY", "GROQ_API_KEY"))
    return _cached(sig)
