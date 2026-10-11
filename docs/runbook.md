# On-call runbook

| Symptom | Check | Action |
|---|---|---|
| `/ready` fails | Database or Neo4j unreachable | `docker compose ps`; restart the failing service; re-run `python -m rca.cli seed` if empty |
| Reports always say "template" | `rca_llm_tokens_total` flat; logs show `llm_call_failed` | Check `GROQ_API_KEY`, rate limits or Ollama availability; the service keeps working without the LLM |
| Latency p95 above 10 s | `rca_run_seconds` histogram | LLM provider slow: lower `LLM_TIMEOUT_S` or switch to a smaller model for routing |
| Wrong root cause reported | Fetch `/v1/runs/{id}` and inspect the trace and tool log | Add the case to the evaluation set, fix the rule or tool, confirm `make eval` passes |
| Actions not appearing | Top hypothesis below HIGH confidence | Expected behaviour: actions are only proposed for confirmed causes |
