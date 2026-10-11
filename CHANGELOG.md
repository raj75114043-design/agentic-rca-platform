# Changelog

## 1.1.0 — 2026-10-11
- Default Groq model switched to open-weight `openai/gpt-oss-120b` (Llama 3.3 70B retired by Groq on 2026-08-16)
- Settings are read at instantiation and the container is rebuilt when LLM configuration changes
- Citation guard accepts grouped citations and performs one corrective retry before falling back
- Template reports always take recommended actions from the runbook Resolution section
- LLM error types are reported per run; configuration errors (4xx) are not retried

## 1.0.0 — 2026-10-11
- LangGraph multi-agent workflow with evidence loop and human-in-the-loop actions
- PostgreSQL/SQLite operational store and Neo4j/NetworkX graph store
- Validated tool registry and MCP server
- Groq / Ollama / OpenAI-compatible LLM provider with deterministic fallback and citation guard
- FastAPI service with API keys, metrics and persisted traces
- Streamlit operations console
- Labelled evaluation with CI quality gate; Postgres and Neo4j integration jobs
