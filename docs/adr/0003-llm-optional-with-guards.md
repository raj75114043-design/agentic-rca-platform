# ADR-0003: The LLM is optional and never the source of facts

**Status:** Accepted

## Context
Free, open-weight models (Groq GPT-OSS 120B, Ollama) are capable planners and writers but can invent identifiers,
dates and vendors. Operations decisions must be traceable to records.

## Decision
- Facts come only from tools. The LLM may classify intent, choose tool calls and phrase the report.
- Planner output is filtered to tools in the catalog and validated by Pydantic before execution.
- Root-cause ranking is deterministic and evidence-based (`rca/analysis.py`).
- The Responder's citations are checked against collected evidence; if any citation is unknown, the report is
  replaced by a deterministic template.
- Every LLM call is time-boxed and retried; failures degrade to the deterministic path.

## Consequences
- The system works with no LLM at all, which keeps CI free and reproducible.
- Model upgrades change wording and planning efficiency but cannot silently change the root cause.
