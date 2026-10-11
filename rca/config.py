"""Runtime configuration from environment variables (twelve-factor style)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _list(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("APP_ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'rca.db'}")
    graph_backend: str = os.getenv("GRAPH_BACKEND", "networkx")  # networkx | neo4j
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "password")
    # LLM: groq (free tier, OpenAI-compatible), ollama (local), openai_compatible, or none (deterministic)
    llm_provider: str = os.getenv("LLM_PROVIDER", "auto")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", os.getenv("GROQ_API_KEY", ""))
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "30"))
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "3"))
    api_keys: list[str] = field(default_factory=lambda: _list(os.getenv("API_KEYS", "dev-key")))
    seed_sites: int = int(os.getenv("SEED_SITES", "240"))
    escalation_delay_days: int = int(os.getenv("ESCALATION_DELAY_DAYS", "10"))


def get_settings() -> Settings:
    return Settings()
