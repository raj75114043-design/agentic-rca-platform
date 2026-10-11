"""FastAPI service for the RCA platform.

Endpoints (all /v1 routes require an `X-API-Key` header):
  POST /v1/rca                     run an investigation
  GET  /v1/runs                    recent runs
  GET  /v1/runs/{run_id}           full trace of a run
  GET  /v1/actions?status=...      human-in-the-loop actions
  POST /v1/actions/{id}/decision   approve or reject an action
  GET  /health, GET /ready         liveness and readiness
  GET  /metrics                    Prometheus metrics
"""
from __future__ import annotations

import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from rca import logging_setup
from rca.agents.graph import run_rca
from rca.container import get_container

logging_setup.configure()
app = FastAPI(title="Agentic RCA Platform", version="1.0.0",
              description="Multi-agent root-cause analysis for telecom network operations")

REQUESTS = Counter("rca_http_requests_total", "HTTP requests", ["path", "status"])
RCA_RUNS = Counter("rca_runs_total", "RCA runs", ["intent", "root_cause", "confidence"])
RCA_LATENCY = Histogram("rca_run_seconds", "End-to-end RCA latency", buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
LLM_TOKENS = Counter("rca_llm_tokens_total", "LLM tokens", ["kind"])
TOOL_CALLS = Counter("rca_tool_calls_total", "Tool calls", ["tool", "ok"])


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
    token = logging_setup.request_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        logging_setup.request_id.reset(token)
    response.headers["X-Request-ID"] = rid
    REQUESTS.labels(request.url.path if not request.url.path.startswith("/v1/runs/") else "/v1/runs/{id}",
                    response.status_code).inc()
    return response


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    if x_api_key not in get_container().settings.api_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key


class RCARequest(BaseModel):
    question: str = Field(min_length=3, max_length=500, examples=["Why is SITE-0228 delayed?"])


class Decision(BaseModel):
    approve: bool
    user: str = Field(min_length=2, max_length=80)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    c = get_container()
    return {"status": "ready", "graph": c.graph.stats(), "llm": {"provider": c.llm.provider, "model": c.llm.model},
            "database": c.settings.database_url.split(":")[0]}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/rca", dependencies=[Depends(require_api_key)])
def create_rca(req: RCARequest):
    t0 = time.perf_counter()
    result = run_rca(get_container(), req.question)
    RCA_LATENCY.observe(time.perf_counter() - t0)
    RCA_RUNS.labels(result["intent"], result["root_cause"] or "none", result["confidence"] or "none").inc()
    LLM_TOKENS.labels("prompt").inc(result["llm"]["prompt_tokens"])
    LLM_TOKENS.labels("completion").inc(result["llm"]["completion_tokens"])
    for t in result["tool_log"]:
        TOOL_CALLS.labels(t["tool"], str(t["ok"]).lower()).inc()
    return result


@app.get("/v1/runs", dependencies=[Depends(require_api_key)])
def list_runs(limit: int = 20):
    return get_container().repo.recent_runs(min(limit, 100))


@app.get("/v1/runs/{run_id}", dependencies=[Depends(require_api_key)])
def get_run(run_id: str):
    run = get_container().repo.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/v1/actions", dependencies=[Depends(require_api_key)])
def list_actions(status: str | None = None):
    return get_container().repo.list_actions(status)


@app.post("/v1/actions/{action_id}/decision", dependencies=[Depends(require_api_key)])
def decide(action_id: str, d: Decision):
    res = get_container().repo.decide_action(action_id, d.approve, d.user)
    if not res:
        raise HTTPException(409, "Action not found or already decided")
    return res
