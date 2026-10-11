# Architecture

## Components
| Component | Responsibility |
|---|---|
| Router | Extracts site, vendor and region with regular expressions; LLM (optional) refines intent |
| Planner | Iteration 1: standard investigation plan. Later iterations: LLM chooses from the catalog using the Analyst's next checks; falls back to those checks |
| Executor | Runs tool calls in parallel (thread pool), records evidence, suppresses repeated calls |
| Analyst | Ranks hypotheses with scores, supporting evidence IDs and next checks with concrete arguments |
| Verifier | Stops when the top hypothesis is confirmed (score ≥ 0.85, no open checks) or the iteration budget is spent |
| Responder | Adds the matching runbook resolution; LLM report with verified citations or deterministic template |
| Actions | Proposes ESCALATE_VENDOR, QUARANTINE_BATCH, ESCALATE_PERMIT or DISPATCH_GENERATOR as PENDING_APPROVAL |

## Data model
Sites, activities, materials, purchase orders, vendors, crews, permits, incidents and regional events in
PostgreSQL; the same entities and their relationships in Neo4j:

```
(Site)-[:IN_REGION]->(Region)        (Site)-[:REQUIRES]->(Permit)
(Site)-[:HAS_ACTIVITY]->(Activity)   (Activity)-[:ASSIGNED_TO]->(Crew)
(Activity)-[:NEEDS]->(PurchaseOrder) (PurchaseOrder)-[:SUPPLIED_BY]->(Vendor)
(Incident)-[:AFFECTS]->(Site)        (RegionEvent)-[:IMPACTS]->(Region)
```

## Request lifecycle
1. `POST /v1/rca` with `X-API-Key`; a request ID is attached to every log line.
2. The workflow runs; every node appends a trace span.
3. The run (question, root cause, confidence, latency, trace, tool log, hypotheses, LLM usage) is persisted.
4. Prometheus counters and histograms are updated; any proposed action waits for `POST /v1/actions/{id}/decision`.

## Security
- Tools are read-only, parameterised and schema-validated; unknown tools and invalid arguments are rejected.
- API keys are configured through the environment; containers run as a non-root user.
- The LLM never receives database credentials and cannot execute arbitrary queries.
